"""endbotd's link against a fake Durable Object on a real WebSocket (docs/WEB_PROTOCOL.md)."""

import base64
import importlib.util
import json
import os
import queue
import tempfile
import threading
import time
import unittest
from pathlib import Path

HAS_LIBRARIES = all(importlib.util.find_spec(name) for name in ("webauthn", "websockets", "cbor2"))
if HAS_LIBRARIES:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat
    from endbotd.web_link import (
        MAX_SESSION_CONNECTIONS,
        MAX_SESSION_REQUESTS_PER_SECOND,
        WEB_PROTOCOL,
        LinkKeyError,
        WebLink,
        load_link_key,
        proof_message,
    )
    from websockets.sync.server import serve

NONCE = "A" * 22
GOOD = "good-token"


def wait(event_or_condition, timeout=5.0):
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if event_or_condition():
            return True
        time.sleep(0.01)
    return False


class FakeAuth:
    def __init__(self):
        self.allowed = {GOOD}

    def principal(self, token):
        return object() if token in self.allowed else None


class FakeWeb:
    def __init__(self):
        self.auth = FakeAuth()
        self.requests = []

    def http(self, path, body, token, *, request_origin=None, site_cookie=None):
        self.requests.append((path, body, token, request_origin, site_cookie))
        if path == "/boom":
            raise RuntimeError("boom")
        return {"status": 200, "body": {"echo": body}}


class FakeDurableObject:
    """Plays the Durable Object's side of the link."""

    def __init__(self, *, protocol=None, origin="ws://placeholder", accept=True, answer=True, busy=False):
        self.protocol = WEB_PROTOCOL if protocol is None else protocol
        self.frames = queue.Queue()
        self.accept = accept
        # False plays a route that stopped carrying anything back, with no close.
        self.answer = answer
        self.proofs = []
        self.pings = 0
        self.socket = None
        self.connected = threading.Event()
        self.turned_away = 0
        # True plays the Durable Object's rate limit on link openings, which answers the upgrade with 429.
        self.server = serve(self._handler, "127.0.0.1", 0, process_request=self._busy if busy else None)
        self.port = self.server.socket.getsockname()[1]
        self.origin = f"http://localhost:{self.port}" if origin == "ws://placeholder" else origin
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def _busy(self, connection, request):
        self.turned_away += 1
        return connection.respond(429, "rate_limited")

    def _handler(self, socket):
        socket.send(json.dumps({"t": "challenge", "protocol": self.protocol, "nonce": NONCE, "origin": self.origin}))
        try:
            proof = json.loads(socket.recv(timeout=5))
            self.proofs.append(proof)
            if not self.accept:
                socket.close(4003, "proof refused")
                return
            socket.send(json.dumps({"t": "ready"}))
            self.socket = socket
            self.connected.set()
            for raw in socket:
                if raw == "link-ping":
                    # As the Workers runtime answers it, with the Durable Object asleep.
                    self.pings += 1
                    if self.answer:
                        socket.send("link-pong")
                else:
                    self.frames.put(json.loads(raw))
        except Exception:  # noqa: BLE001, S110 - a closed test socket
            pass

    def send(self, frame):
        self.socket.send(json.dumps(frame))

    def next(self, kind=None):
        while True:
            frame = self.frames.get(timeout=5)
            if kind is None or frame["t"] == kind:
                return frame

    def http(self, conn, path, body=None, session=None, origin=None, site=None):
        self.send({"t": "open", "conn": conn, "kind": "http", "path": path, "origin": origin, "session": session,
                   "siteCookie": site, "ipBucket": "b"})
        self.send({"t": "msg", "conn": conn, "id": 1, "body": {} if body is None else body})

    def ws(self, conn, session=GOOD):
        self.send({"t": "open", "conn": conn, "kind": "ws", "path": "/ws", "origin": None, "session": session,
                   "siteCookie": None, "ipBucket": "b"})

    def close(self):
        self.server.shutdown()


class RecordingEvent(threading.Event):
    """The link's stop event, noting each retry delay and waiting only briefly, so tests need no wall clock."""

    def __init__(self, delays):
        super().__init__()
        self.delays = delays

    def wait(self, timeout=None):
        if timeout is not None:
            self.delays.append(timeout)
            timeout = min(timeout, 0.01)
        return super().wait(timeout)


@unittest.skipUnless(HAS_LIBRARIES, "the web libraries are not installed")
class LinkCase(unittest.TestCase):
    def setUp(self):
        self.key = Ed25519PrivateKey.generate()
        self.web = FakeWeb()
        self.logs = []
        self.handled = []
        self.closed_conns = []

    def handler(self, token, conn, message, push):
        self.handled.append((token, message))
        if isinstance(message, dict) and message.get("type") == "push":
            push({"type": "event", "topic": "status"})
        return getattr(self, "handler_answer", {"type": "result", "code": "ok"})

    def start(self, **options):
        self.relay = FakeDurableObject(**options)
        self.addCleanup(self.relay.close)
        origin = f"http://localhost:{self.relay.port}"
        self.link = WebLink(self.web, origin, self.key, "0.7.0", ws_handler=self.handler,
                            cache=lambda: {"displayName": "Mine", "bds": "up"},
                            on_connection_closed=self.closed_conns.append,
                            log=lambda level, message: self.logs.append((level, message)),
                            retry=getattr(self, "retry", (0.05, 0.2)), keep_alive=getattr(self, "keep_alive", 30.0),
                            silent=getattr(self, "silent", 90.0), settling=getattr(self, "settling", (120.0, 5.0)))
        if hasattr(self, "delays"):
            self.link._stop = RecordingEvent(self.delays)
        self.link.start()
        self.addCleanup(self.link.stop)
        return self.link

    def up(self):
        self.start()
        self.assertTrue(self.relay.connected.wait(5))
        self.assertTrue(self.link.connected.wait(5))


class HandshakeTests(LinkCase):
    def test_the_proof_verifies_with_the_public_key(self):
        self.up()
        proof = self.relay.proofs[0]
        self.assertEqual((proof["t"], proof["protocol"], proof["endbotdVersion"]), ("prove", WEB_PROTOCOL, "0.7.0"))
        signature = base64.urlsafe_b64decode(proof["sig"] + "=" * (-len(proof["sig"]) % 4))
        self.key.public_key().verify(signature, proof_message(self.relay.origin, NONCE))
        with self.assertRaises(InvalidSignature):
            self.key.public_key().verify(signature, proof_message("https://other.example", NONCE))

    def test_the_display_cache_follows_the_proof(self):
        self.up()
        self.assertEqual(self.relay.next("cache"), {"t": "cache", "displayName": "Mine", "bds": "up"})

    def test_another_protocol_is_refused_with_the_operators_fix(self):
        self.start(protocol=WEB_PROTOCOL + 1)
        self.assertTrue(wait(lambda: self.link.problem and "run \"endbot web deploy\"" in self.link.problem))
        self.assertEqual(self.relay.proofs, [])
        self.assertFalse(self.link.connected.is_set())

    def test_a_challenge_for_another_origin_is_never_signed(self):
        self.start(origin="https://evil.example")
        self.assertTrue(wait(lambda: self.link.problem and "evil.example" in self.link.problem))
        self.assertEqual(self.relay.proofs, [])

    def test_a_refused_proof_retries_with_backoff_and_logs_once(self):
        self.start(accept=False)
        self.assertTrue(wait(lambda: len(self.relay.proofs) >= 2, timeout=20))
        self.assertFalse(self.link.connected.is_set())
        self.assertEqual(len({message for _, message in self.logs}), len([m for _, m in self.logs]))
        self.assertIn("(4003)", self.link.problem)

    def test_a_refused_proof_soon_after_start_is_retried_often_while_a_new_key_settles(self):
        self.retry = (0.5, 60.0)
        self.settling = (30.0, 0.05)
        self.delays = []
        self.start(accept=False)
        self.assertTrue(wait(lambda: len(self.relay.proofs) >= 3, timeout=30))
        self.assertTrue(self.delays and all(delay <= 0.05 for delay in self.delays), self.delays)

    def test_once_the_key_has_had_time_to_settle_a_refusal_backs_off_as_before(self):
        self.retry = (0.5, 60.0)
        self.settling = (0.0, 0.05)
        self.delays = []
        self.start(accept=False)
        self.assertTrue(wait(lambda: len(self.delays) >= 3, timeout=30))
        self.assertEqual(self.delays[:3], [0.5, 1.0, 2.0])

    def test_being_turned_away_for_trying_too_often_while_a_key_settles_does_not_back_off(self):
        # Step 10 on rc.2: a retry every 5 s hit the Durable Object's 10 openings a minute, and its 429 backed off.
        self.retry = (0.5, 60.0)
        self.settling = (30.0, 0.05)
        self.delays = []
        self.start(busy=True)
        self.assertTrue(wait(lambda: self.relay.turned_away >= 3, timeout=30))
        self.assertTrue(self.delays and all(delay <= 0.05 for delay in self.delays), self.delays)
        self.assertIn("429", self.link.problem)

    def test_other_failures_back_off_as_before_even_just_after_start(self):
        self.retry = (0.5, 60.0)
        self.settling = (30.0, 0.05)
        self.delays = []
        self.start(origin="https://evil.example")
        self.assertTrue(wait(lambda: len(self.delays) >= 3, timeout=30))
        self.assertEqual(self.delays[:3], [0.5, 1.0, 2.0])


class HttpTests(LinkCase):
    def test_a_request_is_answered_and_closed(self):
        self.up()
        self.relay.http("c1", "/auth/login/options", {"a": 1}, session="tok", origin="https://o", site="s")
        reply = self.relay.next("msg")
        self.assertEqual(reply["body"], {"status": 200, "body": {"echo": {"a": 1}}})
        self.assertEqual(self.relay.next("close")["conn"], "c1")
        self.assertEqual(self.web.requests[0], ("/auth/login/options", {"a": 1}, "tok", "https://o", "s"))

    def test_one_failing_request_does_not_end_the_link(self):
        self.up()
        self.relay.http("c1", "/boom")
        self.assertEqual(self.relay.next("msg")["body"]["status"], 500)
        self.relay.http("c2", "/fine")
        self.assertEqual(self.relay.next("msg")["conn"], "c2")
        self.assertTrue(any("boom" in message for _, message in self.logs))

    def test_a_second_message_on_an_http_conn_is_ignored(self):
        self.up()
        self.relay.http("c1", "/one")
        self.relay.send({"t": "msg", "conn": "c1", "id": 2, "body": {}})
        self.relay.next("close")
        self.relay.http("c2", "/two")
        self.relay.next("close")
        self.assertEqual([request[0] for request in self.web.requests], ["/one", "/two"])

    def test_junk_frames_are_ignored(self):
        self.up()
        for raw in ("not json", "[]", "5", '{"t":"open"}', '{"t":"open","conn":"x","kind":"tcp","path":"/"}',
                    '{"t":"open","conn":"x","kind":[],"path":"/"}', '{"t":"open","conn":[],"kind":"ws","path":"/"}',
                    '{"t":"msg","conn":{},"id":1,"body":{}}', '{"t":"close","conn":[]}',
                    '{"t":"msg","conn":"none","id":1,"body":{}}', '{"t":"close"}', '{"t":"unknown"}'):
            self.relay.socket.send(raw)
        self.relay.http("c1", "/after")
        self.assertEqual(self.relay.next("msg")["conn"], "c1")


class WebSocketTests(LinkCase):
    def message(self, conn="w1", identifier=7, **body):
        self.relay.send({"t": "msg", "conn": conn, "id": identifier, "body": {"v": WEB_PROTOCOL, "id": 3, **body}})

    def test_a_signed_out_upgrade_is_closed(self):
        self.up()
        self.relay.ws("w1", session="nope")
        self.assertEqual(self.relay.next("close"), {"t": "close", "conn": "w1", "reason": "signed_out"})
        self.relay.ws("w2", session=None)
        self.assertEqual(self.relay.next("close")["conn"], "w2")

    def test_a_message_reaches_the_handler_and_carries_its_id_back(self):
        self.up()
        self.relay.ws("w1")
        self.message(type="query", what="status")
        reply = self.relay.next("msg")
        self.assertEqual(reply["body"], {"id": 3, "type": "result", "code": "ok"})
        self.assertEqual(self.handled[0][0], GOOD)

    def test_the_session_is_checked_again_for_every_message(self):
        self.up()
        self.relay.ws("w1")
        self.message(type="query")
        self.relay.next("msg")
        self.web.auth.allowed.clear()
        self.message(type="query")
        self.assertEqual(self.relay.next("msg")["body"]["code"], "signed_out")
        self.assertEqual(self.relay.next("close")["reason"], "signed_out")
        self.assertEqual(len(self.handled), 1)

    def test_another_browser_protocol_asks_the_page_to_reload(self):
        self.up()
        self.relay.ws("w1")
        self.relay.send({"t": "msg", "conn": "w1", "id": 7, "body": {"v": WEB_PROTOCOL + 1, "id": 3, "type": "query"}})
        self.assertEqual(self.relay.next("msg")["body"], {"type": "reload"})
        self.assertEqual(self.handled, [])

    def test_an_old_browser_protocol_asks_the_page_to_reload(self):
        self.up()
        self.relay.ws("w1")
        self.relay.send({"t": "msg", "conn": "w1", "id": 7, "body": {"v": WEB_PROTOCOL - 1, "id": 3, "type": "query"}})
        self.assertEqual(self.relay.next("msg")["body"], {"type": "reload"})
        self.assertEqual(self.handled, [])

    def test_pushes_reach_the_connection(self):
        self.up()
        self.relay.ws("w1")
        self.message(type="push")
        frames = [self.relay.next("msg"), self.relay.next("msg")]
        self.assertIn({"type": "event", "topic": "status"}, [frame["body"] for frame in frames])

    def test_a_session_may_hold_four_connections(self):
        self.up()
        for index in range(MAX_SESSION_CONNECTIONS):
            self.relay.ws(f"w{index}")
        self.relay.ws("extra")
        self.assertEqual(self.relay.next("close"), {"t": "close", "conn": "extra", "reason": "too_many_connections"})
        # Closing one makes room again.
        self.relay.send({"t": "close", "conn": "w0", "reason": "browser"})
        self.relay.ws("again")
        self.message(conn="again", type="query")
        self.assertEqual(self.relay.next("msg")["conn"], "again")

    def test_a_session_may_send_twenty_requests_per_second(self):
        self.up()
        now = [100.0]
        self.link._clock = lambda: now[0]
        self.relay.ws("w1")
        for _ in range(MAX_SESSION_REQUESTS_PER_SECOND + 1):
            self.message(type="query")
        codes = [self.relay.next("msg")["body"]["code"] for _ in range(MAX_SESSION_REQUESTS_PER_SECOND + 1)]
        self.assertEqual(codes.count("rate_limited"), 1)
        now[0] = 101.0
        self.message(type="query")
        self.assertEqual(self.relay.next("msg")["body"]["code"], "ok")

    def test_requests_over_http_count_against_the_session_too(self):
        self.up()
        now = [100.0]
        self.link._clock = lambda: now[0]
        for index in range(MAX_SESSION_REQUESTS_PER_SECOND):
            self.relay.http(f"h{index}", "/auth/devices", session=GOOD)
        self.relay.http("over", "/auth/devices", session=GOOD)
        statuses = {}
        for _ in range(MAX_SESSION_REQUESTS_PER_SECOND + 1):
            frame = self.relay.next("msg")
            statuses[frame["conn"]] = frame["body"]["status"]
        self.assertEqual(statuses["over"], 429)
        self.assertEqual(list(statuses.values()).count(429), 1)

    def test_closing_a_connection_does_not_reset_the_request_count(self):
        self.up()
        now = [100.0]
        self.link._clock = lambda: now[0]
        self.relay.ws("w1")
        for _ in range(MAX_SESSION_REQUESTS_PER_SECOND):
            self.message(type="query")
        for _ in range(MAX_SESSION_REQUESTS_PER_SECOND):
            self.relay.next("msg")
        self.relay.send({"t": "close", "conn": "w1", "reason": "browser"})
        self.relay.ws("w2")
        self.message(conn="w2", type="query")
        self.assertEqual(self.relay.next("msg")["body"]["code"], "rate_limited")

    def test_a_closed_browser_is_reported_so_its_subscriptions_end(self):
        self.up()
        self.relay.ws("w1")
        self.message(type="query")
        self.relay.next("msg")
        self.relay.send({"t": "close", "conn": "w1", "reason": "browser"})
        self.assertTrue(wait(lambda: self.closed_conns == ["w1"]))

    def test_an_answer_over_the_frame_limit_says_so_instead_of_vanishing(self):
        self.up()
        self.handler_answer = {"type": "result", "code": "ok", "data": "x" * (1024 * 1024)}
        self.relay.ws("w1")
        self.message(type="query")
        self.assertEqual(self.relay.next("msg")["body"], {"id": 3, "type": "result", "code": "too_large"})

    def test_an_ended_session_has_its_connections_closed(self):
        self.up()
        self.relay.ws("w1")
        self.message(type="query")
        self.relay.next("msg")
        import hashlib

        self.link.sessions_removed([hashlib.sha256(GOOD.encode()).hexdigest()])
        self.assertEqual(self.relay.next("close"), {"t": "close", "conn": "w1", "reason": "signed_out"})


class KeepAliveTests(LinkCase):
    def test_a_quiet_link_says_it_is_alive_and_the_answer_is_no_frame(self):
        self.keep_alive = 0.05
        self.up()
        for _ in range(100):
            if self.relay.pings >= 3:
                break
            time.sleep(0.05)
        self.assertGreaterEqual(self.relay.pings, 3)
        self.assertTrue(self.link.connected.is_set())
        self.assertFalse([message for level, message in self.logs if level != "info"], self.logs)
        # Still serving frames in between.
        self.relay.http("c1", "/auth/info")
        self.assertEqual(self.relay.next("msg")["conn"], "c1")


    def test_a_busy_link_still_says_it_is_alive(self):
        self.keep_alive = 0.1
        self.up()
        # Frames arriving faster than the keep-alive period: receiving must not put off the ping.
        for index in range(40):
            self.relay.http(f"b{index}", "/auth/info")
            time.sleep(0.02)
        self.assertGreaterEqual(self.relay.pings, 3)


    def test_a_link_that_hears_nothing_back_is_dropped_and_made_again(self):
        self.keep_alive = 0.05
        self.silent = 0.4
        self.start(answer=False)
        self.assertTrue(wait(lambda: len(self.relay.proofs) >= 2, timeout=10))
        self.assertTrue(any("no answer from the Worker" in message for _, message in self.logs), self.logs)

    def test_an_answered_ping_keeps_a_quiet_link(self):
        self.keep_alive = 0.05
        self.silent = 0.4
        self.up()
        time.sleep(1.2)
        self.assertEqual(len(self.relay.proofs), 1)
        self.assertTrue(self.link.connected.is_set())


class ReconnectTests(LinkCase):
    def test_an_answer_for_an_old_link_is_not_sent_on_the_new_one(self):
        self.up()
        release = threading.Event()
        answered = threading.Event()
        http = self.web.http

        def slow(*args, **kwargs):
            release.wait(5)
            answered.set()
            return http(*args, **kwargs)

        self.web.http = slow
        self.relay.http("c1", "/auth/login/verify")
        self.assertTrue(wait(lambda: self.web.auth is not None))
        import time

        time.sleep(0.1)
        generation = self.link._generation
        self.relay.connected.clear()
        self.relay.socket.close()
        self.assertTrue(self.relay.connected.wait(10))
        self.assertTrue(wait(lambda: self.link._generation == generation + 1 and self.link.connected.is_set()))
        while not self.relay.frames.empty():
            self.relay.frames.get()
        release.set()
        self.assertTrue(answered.wait(5))
        time.sleep(0.2)
        self.assertEqual([frame for frame in list(self.relay.frames.queue) if frame.get("conn") == "c1"], [])

    def test_the_retry_delay_starts_over_after_a_healthy_link(self):
        self.up()
        for _ in range(3):
            self.relay.connected.clear()
            self.link.connected.clear()
            self.relay.socket.close()
            # At the shortest delay each comes back within the close handshake, well under the 0.2 s cap's growth.
            self.assertTrue(self.relay.connected.wait(10))
            self.assertTrue(self.link.connected.wait(10))
        self.assertTrue(self.link._proved)

    def test_a_lost_link_reconnects_and_proves_again(self):
        self.up()
        self.relay.connected.clear()
        self.relay.socket.close()
        self.assertTrue(self.relay.connected.wait(5))
        self.assertTrue(wait(lambda: len(self.relay.proofs) == 2))
        self.assertTrue(self.link.connected.wait(5))

    def test_stop_ends_the_thread(self):
        self.up()
        self.link.stop()
        self.assertFalse(self.link._thread.is_alive())


@unittest.skipUnless(HAS_LIBRARIES, "the web libraries are not installed")
class KeyFileTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "web-link.key"

    def test_a_valid_key_loads(self):
        key = Ed25519PrivateKey.generate()
        self.path.write_bytes(key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
        os.chmod(self.path, 0o600)
        self.assertIsInstance(load_link_key(self.path), Ed25519PrivateKey)

    @unittest.skipIf(os.name == "nt", "POSIX modes")
    def test_a_key_others_can_read_is_refused(self):
        key = Ed25519PrivateKey.generate()
        self.path.write_bytes(key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
        os.chmod(self.path, 0o644)
        with self.assertRaisesRegex(LinkKeyError, "owner"):
            load_link_key(self.path)

    def test_a_directory_is_refused(self):
        self.path.mkdir()
        with self.assertRaisesRegex(LinkKeyError, "regular file"):
            load_link_key(self.path)

    def test_a_missing_or_broken_key_names_the_fix_without_the_contents(self):
        with self.assertRaisesRegex(LinkKeyError, "endbot web deploy"):
            load_link_key(self.path)
        self.path.write_text("SECRET-LOOKING", encoding="utf-8")
        with self.assertRaises(LinkKeyError) as raised:
            load_link_key(self.path)
        self.assertNotIn("SECRET-LOOKING", str(raised.exception))


@unittest.skipUnless(HAS_LIBRARIES, "the web libraries are not installed")
class ProtocolExampleTests(unittest.TestCase):
    def test_the_schema_version_is_the_protocol_version(self):
        root = Path(__file__).resolve().parents[3]
        schema = json.loads((root / "core/src/endbotd/web_protocol.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(schema["version"], WEB_PROTOCOL)
        examples = json.loads((root / "web/protocol-examples.json").read_text(encoding="utf-8"))
        kinds = {example["t"] for example in examples["valid"] if "t" in example}
        self.assertEqual(kinds, {"challenge", "prove", "ready", "open", "msg", "close", "cache"})
        messages = {example["type"] for example in examples["valid"] if "type" in example}
        self.assertEqual(messages, {"trigger.create"})


if __name__ == "__main__":
    unittest.main()
