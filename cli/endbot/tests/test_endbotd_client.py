"""The CLI side of `endbot controllers add|remove|list` (docs/ENDBOTD.md P8).

The round trip against a real endbotd is in tests/test_cli_controllers_endbotd.py;
here a fake endpoint stands in for endbotd's failure modes.
"""

from __future__ import annotations

import importlib.util
import json
import os
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path

import endbot_cli
from endbot_cli.endbotd_client import EndbotdError, controllers_request

TOKEN = "t" * 40


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class FakeEndpoint:
    """Accepts one connection, answers hello, then does ``after_request(sock, request)``."""

    def __init__(self, after_request) -> None:
        self.listener = socket.create_server(("127.0.0.1", 0))
        self.port = self.listener.getsockname()[1]
        self.after_request = after_request
        self.hello: dict | None = None
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        sock, _ = self.listener.accept()
        with sock, sock.makefile("rb") as stream:
            self.hello = json.loads(stream.readline())
            sock.sendall(b'{"id":0,"ok":true,"result":{"protocol":1,"endbotdVersion":"test"}}\n')
            self.after_request(sock, json.loads(stream.readline()))

    def close(self) -> None:
        self.listener.close()


class ClientTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.run_dir = self.root / "run"
        self.run_dir.mkdir()
        (self.run_dir / "supervisor.pid").write_text(str(os.getpid()), encoding="utf-8")
        self.token = self.root / "endbotd.token"
        self.token.write_text(TOKEN + "\n", encoding="utf-8")

    def request(self, port: int, operation: str, target: str | None = None, **options) -> dict:
        return controllers_request(self.run_dir, self.token, port, operation, target, **options)

    def test_endbotd_down_is_reported_at_once(self) -> None:
        started = time.monotonic()
        with self.assertRaises(EndbotdError) as raised:
            self.request(free_port(), "add", "Player")
        self.assertEqual(raised.exception.code, "unavailable")
        self.assertIn("endbotd is not running", str(raised.exception))
        self.assertLess(time.monotonic() - started, 3.0)

    def test_stopped_instance_and_bad_targets_never_connect(self) -> None:
        (self.run_dir / "supervisor.pid").unlink()
        with self.assertRaises(EndbotdError) as raised:
            self.request(free_port(), "list")
        self.assertIn("not running; start it", str(raised.exception))
        for target in ("Player\nstop", "  ", "", None, "x" * 257):
            with self.assertRaises(EndbotdError):
                self.request(free_port(), "add", target)

    def test_result_and_token_go_over_one_request(self) -> None:
        def answer(sock, request):
            self.assertEqual(request["body"], {"operation": "add", "target": "Player With Spaces"})
            reply = {"id": request["id"], "ok": True, "result": {"ok": True, "messages": ["done"], "entries": []}}
            sock.sendall(json.dumps(reply).encode() + b"\n")

        endpoint = FakeEndpoint(answer)
        self.addCleanup(endpoint.close)
        result = self.request(endpoint.port, "add", "Player With Spaces")
        self.assertEqual(result["messages"], ["done"])
        self.assertEqual((endpoint.hello["body"]["client"], endpoint.hello["body"]["token"]), ("cli", TOKEN))

    def test_lost_connection_after_a_mutation_is_result_unknown(self) -> None:
        for operation, target, code in (("remove", "Player", "unknown"), ("list", None, "unavailable")):
            endpoint = FakeEndpoint(lambda sock, request: None)
            self.addCleanup(endpoint.close)
            with self.assertRaises(EndbotdError) as raised:
                self.request(endpoint.port, operation, target)
            self.assertEqual(raised.exception.code, code)
            self.assertNotIn(TOKEN, str(raised.exception))

    def test_a_reply_to_another_request_is_result_unknown(self) -> None:
        def answer(sock, request):
            reply = {"id": request["id"] + 1, "ok": True, "result": {"ok": True, "messages": ["x"]}}
            sock.sendall(json.dumps(reply).encode() + b"\n")

        endpoint = FakeEndpoint(answer)
        self.addCleanup(endpoint.close)
        with self.assertRaises(EndbotdError) as raised:
            self.request(endpoint.port, "add", "Player")
        self.assertEqual(raised.exception.code, "unknown")

    def test_a_timeout_is_result_unknown(self) -> None:
        endpoint = FakeEndpoint(lambda sock, request: time.sleep(1.0))
        self.addCleanup(endpoint.close)
        with self.assertRaises(EndbotdError) as raised:
            self.request(endpoint.port, "add", "Player", timeout=0.3)
        self.assertEqual(raised.exception.code, "unknown")


class FileChannelRemovedTests(unittest.TestCase):
    def test_the_controller_request_file_channel_is_gone(self) -> None:
        self.assertIsNone(importlib.util.find_spec("endbot_cli.controller_requests"))
        package = Path(endbot_cli.__file__).parent
        for path in package.glob("*.py"):
            source = path.read_text(encoding="utf-8")
            for marker in ("controller-requests", "controller-session", "botcontrollers request", "dispatch_requests"):
                self.assertNotIn(marker, source, f"{path.name} still has {marker!r}")


if __name__ == "__main__":
    unittest.main()
