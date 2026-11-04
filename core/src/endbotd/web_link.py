"""endbotd's one outbound link to the Durable Object (docs/WEB.md P1, P2; docs/WEB_PROTOCOL.md).

endbotd proves itself with its Ed25519 key and then serves multiplexed requests.
Everything arriving on the link is untrusted: the Worker is a relay. Each request is
decided by ``WebService`` from endbotd's own state, and a session is checked again
for every WebSocket message.
"""

from __future__ import annotations

import base64
import hashlib
import json
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat, load_pem_private_key
from websockets.exceptions import ConnectionClosed, WebSocketException
from websockets.sync.client import connect as websocket_connect

WEB_PROTOCOL = json.loads((Path(__file__).parent / "web_protocol.schema.json").read_text(encoding="utf-8"))["version"]
MAX_FRAME_BYTES = 1024 * 1024
CHALLENGE_SECONDS = 10.0
RETRY_INITIAL = 1.0
RETRY_MAX = 60.0
MAX_SESSION_CONNECTIONS = 4
MAX_SESSION_REQUESTS_PER_SECOND = 20
MAX_REQUEST_WORKERS = 8
PROOF_PREFIX = "endbot-link-v1"


class LinkKeyError(ValueError):
    """The link key is missing or invalid; the web stays offline until `endbot web deploy` runs."""


def load_link_key(path: Path) -> Ed25519PrivateKey:
    path = Path(path)
    if path.is_symlink():
        raise LinkKeyError(f"{path} must not be a symlink")
    try:
        key = load_pem_private_key(path.read_bytes(), password=None)
    except FileNotFoundError:
        raise LinkKeyError(f"{path} is missing; run `endbot web deploy`") from None
    except (OSError, ValueError, TypeError) as error:
        raise LinkKeyError(f"{path} cannot be read: {type(error).__name__}") from None
    if not isinstance(key, Ed25519PrivateKey):
        raise LinkKeyError(f"{path} must be an Ed25519 private key")
    return key


def public_key_bytes(key: Ed25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


def key_id(key: Ed25519PrivateKey) -> str:
    """A short name for the public key; rotation changes it."""
    return hashlib.sha256(public_key_bytes(key)).hexdigest()[:16]


def proof_message(origin: str, nonce: str) -> bytes:
    return f"{PROOF_PREFIX}\n{origin}\n{nonce}".encode()


def _sign(key: Ed25519PrivateKey, origin: str, nonce: str) -> str:
    return base64.urlsafe_b64encode(key.sign(proof_message(origin, nonce))).rstrip(b"=").decode("ascii")


def _session_hash(token: str) -> str:
    return hashlib.sha256(token.encode("ascii", "replace")).hexdigest()


@dataclass
class _Connection:
    conn: str
    kind: str
    path: str
    origin: str | None
    session: str | None
    site_cookie: str | None
    hash: str | None = None
    answered: bool = False


@dataclass
class _RateWindow:
    second: int = 0
    count: int = 0
    connections: set = field(default_factory=set)


class LinkProtocolMismatch(Exception):
    pass


class WebLink:
    """One background thread holding the link, reconnecting with a capped backoff."""

    def __init__(self, web, origin: str, key: Ed25519PrivateKey, version: str, *,
                 ws_handler: Callable[[str, object, Callable[[object], None]], object | None] | None = None,
                 cache: Callable[[], dict] | None = None,
                 log: Callable[[str, str], None] | None = None,
                 connect: Callable | None = None, clock: Callable[[], float] = time.monotonic,
                 retry: tuple[float, float] = (RETRY_INITIAL, RETRY_MAX)) -> None:
        self._web = web
        self.origin = origin
        self._key = key
        self.key_id = key_id(key)
        self._version = version
        self._ws_handler = ws_handler or (lambda token, message, push: {"type": "result", "code": "unsupported"})
        self._cache = cache or (lambda: {"displayName": "", "bds": "down"})
        self._log = log or (lambda level, message: None)
        self._connect = connect or websocket_connect
        self._clock = clock
        self._retry = retry
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._socket = None
        self._send_lock = threading.Lock()
        self._lock = threading.RLock()
        self._connections: dict[str, _Connection] = {}
        self._rates: dict[str, _RateWindow] = {}
        self._workers: ThreadPoolExecutor | None = None
        self.connected = threading.Event()
        # For `endbot doctor` (step 4): why the link is down.
        self.problem: str | None = None

    # -- lifecycle ---------------------------------------------------------------

    def start(self) -> WebLink:
        self._workers = ThreadPoolExecutor(max_workers=MAX_REQUEST_WORKERS, thread_name_prefix="endbotd-web")
        self._thread = threading.Thread(target=self._run, name="endbotd-web-link", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        socket = self._socket
        if socket is not None:
            try:
                socket.close()
            except (OSError, WebSocketException):
                pass
        if self._thread is not None:
            self._thread.join(timeout=5)
        if self._workers is not None:
            self._workers.shutdown(wait=False, cancel_futures=True)

    @property
    def _url(self) -> str:
        return self.origin.replace("https://", "wss://", 1).replace("http://", "ws://", 1) + "/link"

    def _run(self) -> None:
        delay = self._retry[0]
        while not self._stop.is_set():
            try:
                self._serve_once()
                delay = self._retry[0]
            except LinkProtocolMismatch as error:
                self._down(str(error), "error")
            except (OSError, ValueError, TimeoutError, WebSocketException) as error:
                self._down(f"web link failed: {type(error).__name__}: {error}", "warning")
            self.connected.clear()
            if self._stop.wait(delay):
                return
            delay = min(delay * 2, self._retry[1])

    def _down(self, problem: str, level: str) -> None:
        if problem != self.problem:
            self._log(level, problem)
        self.problem = problem

    def _serve_once(self) -> None:
        with self._connect(self._url, max_size=MAX_FRAME_BYTES, open_timeout=10, close_timeout=2,
                           user_agent_header="Endbot") as socket:
            self._socket = socket
            try:
                self._prove(socket)
                self.connected.set()
                self.problem = None
                self._log("info", f"Web link up to {self.origin}")
                self.publish_cache()
                while not self._stop.is_set():
                    self._frame(socket.recv())
            except ConnectionClosed as error:
                raise OSError(f"the Worker closed the link ({error.rcvd.code if error.rcvd else 'no code'})") from None
            finally:
                self._socket = None
                self.connected.clear()
                with self._lock:
                    self._connections.clear()
                    self._rates.clear()

    def _prove(self, socket) -> None:
        challenge = json.loads(socket.recv(timeout=CHALLENGE_SECONDS))
        if not isinstance(challenge, dict) or challenge.get("t") != "challenge":
            raise ValueError("the Worker did not send a challenge")
        protocol = challenge.get("protocol")
        if protocol != WEB_PROTOCOL:
            raise LinkProtocolMismatch(
                f'Worker speaks web protocol {protocol}, endbotd expects {WEB_PROTOCOL}: run "endbot web deploy"')
        # Signing only for our own origin makes a proof useless anywhere else.
        if challenge.get("origin") != self.origin or not isinstance(challenge.get("nonce"), str):
            raise ValueError(f"the challenge names origin {challenge.get('origin')!r}, not {self.origin}")
        socket.send(json.dumps({"t": "prove", "protocol": WEB_PROTOCOL, "endbotdVersion": self._version,
                                "keyId": self.key_id, "sig": _sign(self._key, self.origin, challenge["nonce"])}))
        answer = json.loads(socket.recv(timeout=CHALLENGE_SECONDS))
        if not isinstance(answer, dict) or answer.get("t") != "ready":
            raise ValueError("the Worker did not accept the proof")

    # -- frames ------------------------------------------------------------------

    def _frame(self, raw: str | bytes) -> None:
        try:
            frame = json.loads(raw)
        except ValueError:
            self._log("warning", "Web link: a frame that is not JSON was ignored")
            return
        kind = frame.get("t") if isinstance(frame, dict) else None
        if kind == "open":
            self._open(frame)
        elif kind == "msg":
            self._message(frame)
        elif kind == "close":
            self._forget(frame.get("conn"))

    @staticmethod
    def _text(frame: dict, field_name: str) -> str | None:
        value = frame.get(field_name)
        return value if isinstance(value, str) and value else None

    def _open(self, frame: dict) -> None:
        conn, kind, path = frame.get("conn"), frame.get("kind"), frame.get("path")
        if not isinstance(conn, str) or not conn or kind not in {"http", "ws"} or not isinstance(path, str):
            return
        session = self._text(frame, "session")
        connection = _Connection(conn, kind, path, self._text(frame, "origin"), session,
                                 self._text(frame, "siteCookie"))
        if kind == "ws":
            # Authenticated when it opens and again per message (P5).
            if self._web.auth is None or self._web.auth.principal(session) is None:
                self.send_close(conn, "signed_out")
                return
            connection.hash = _session_hash(session)
            with self._lock:
                window = self._rates.setdefault(connection.hash, _RateWindow())
                if len(window.connections) >= MAX_SESSION_CONNECTIONS:
                    refused = True
                else:
                    refused = False
                    window.connections.add(conn)
            if refused:
                self.send_close(conn, "too_many_connections")
                return
        with self._lock:
            self._connections[conn] = connection

    def _message(self, frame: dict) -> None:
        conn, identifier, body = frame.get("conn"), frame.get("id"), frame.get("body")
        with self._lock:
            connection = self._connections.get(conn) if isinstance(conn, str) else None
        if connection is None or type(identifier) is not int:
            return
        if connection.kind == "http":
            if connection.answered:
                return
            connection.answered = True
            self._workers.submit(self._http, connection, identifier, body)
            return
        if not self._within_rate(connection.hash):
            self.send(conn, identifier, {"id": body.get("id") if isinstance(body, dict) else None,
                                         "type": "result", "code": "rate_limited"})
            return
        self._workers.submit(self._ws, connection, identifier, body)

    def _within_rate(self, session_hash: str) -> bool:
        with self._lock:
            window = self._rates.setdefault(session_hash, _RateWindow())
            second = int(self._clock())
            if window.second != second:
                window.second, window.count = second, 0
            window.count += 1
            return window.count <= MAX_SESSION_REQUESTS_PER_SECOND

    def _http(self, connection: _Connection, identifier: int, body: object) -> None:
        try:
            result = self._web.http(connection.path, body, connection.session, request_origin=connection.origin,
                                    site_cookie=connection.site_cookie)
        except Exception as error:  # noqa: BLE001 - one request must not end the link
            self._log("error", f"Web request {connection.path} failed: {type(error).__name__}: {error}")
            result = {"status": 500, "body": {"code": "unavailable"}}
        self.send(connection.conn, identifier, result)
        self.send_close(connection.conn, "done")
        self._forget(connection.conn)

    def _ws(self, connection: _Connection, identifier: int, body: object) -> None:
        def push(message: object) -> None:
            self.send(connection.conn, 0, message)

        request_id = body.get("id") if isinstance(body, dict) else None
        if self._web.auth is None or self._web.auth.principal(connection.session) is None:
            self.send(connection.conn, identifier, {"id": request_id, "type": "result", "code": "signed_out"})
            self.send_close(connection.conn, "signed_out")
            self._forget(connection.conn)
            return
        if isinstance(body, dict) and body.get("v") != WEB_PROTOCOL:
            self.send(connection.conn, identifier, {"type": "reload"})
            return
        try:
            result = self._ws_handler(connection.session, body, push)
        except Exception as error:  # noqa: BLE001 - one request must not end the link
            self._log("error", f"Web message failed: {type(error).__name__}: {error}")
            result = {"type": "result", "code": "unavailable"}
        if result is not None:
            self.send(connection.conn, identifier, {"id": request_id, **result})

    def _forget(self, conn: object) -> None:
        with self._lock:
            connection = self._connections.pop(conn, None) if isinstance(conn, str) else None
            if connection is not None and connection.hash is not None:
                window = self._rates.get(connection.hash)
                if window is not None:
                    window.connections.discard(conn)
                    if not window.connections:
                        del self._rates[connection.hash]

    # -- toward the relay --------------------------------------------------------

    def _send_frame(self, frame: dict) -> None:
        socket = self._socket
        if socket is None:
            return
        data = json.dumps(frame, separators=(",", ":"))
        if len(data.encode("utf-8")) > MAX_FRAME_BYTES:
            self._log("warning", "Web link: an outgoing frame over 1 MiB was dropped")
            return
        try:
            with self._send_lock:
                socket.send(data)
        except (OSError, WebSocketException) as error:
            self._log("warning", f"Web link send failed: {type(error).__name__}")

    def send(self, conn: str, identifier: int, body: object) -> None:
        self._send_frame({"t": "msg", "conn": conn, "id": identifier, "body": body})

    def send_close(self, conn: str, reason: str) -> None:
        self._send_frame({"t": "close", "conn": conn, "reason": reason})

    def publish_cache(self) -> None:
        self._send_frame({"t": "cache", **self._cache()})

    def sessions_removed(self, hashes: list[str]) -> None:
        """Close the connections of ended sessions (P6). The per-request check is what enforces it."""
        removed = set(hashes)
        with self._lock:
            conns = [connection.conn for connection in self._connections.values() if connection.hash in removed]
        for conn in conns:
            self.send_close(conn, "signed_out")
            self._forget(conn)
