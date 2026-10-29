"""The persistent duplex link between the Endbot plugin and endbotd.

One loopback TCP connection carries newline-delimited JSON in both directions
(docs/ENDBOTD.md P2). The plugin dials out and opens with ``hello``; after
that either side sends requests and replies:

    {"id": 7, "type": "world", "body": {...}}              a request
    {"id": 7, "ok": true, "result": ...}                   its reply
    {"id": 7, "ok": false, "error": {"code", "message"}}   or a failure

Request ids are per sender, so a reply always answers a request its receiver
sent. Each side handles the other's requests in arrival order on one thread,
except types it declares concurrent (the plugin's ``world`` calls), so
presence reports and commands reach endbotd in the order they happened (P4).

Bounds: loopback only on both ends, a 1 MiB frame limit, the token compared in
constant time and never logged, a deadline on every request, and explicit
error codes. A protocol or registry-hash mismatch refuses the link.

The same listener is endbotd's CLI endpoint (P8): a ``hello`` with
``"client": "cli"`` and the same token opens a short connection that carries
only the CLI request types. Its request ids must be unique on the connection,
so a replayed request is refused instead of run twice.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import queue
import socket
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from pathlib import Path

from endbot_core.i18n import LocalizedError
from endbot_core.operations import REGISTRY
from endbot_core.principal import Principal

PROTOCOL = 1
MAX_FRAME_BYTES = 1024 * 1024
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
HANDSHAKE_SECONDS = 10.0
REQUEST_SECONDS = 10.0
# Outbound frames waiting for the writer; a peer that stops reading loses the link, not memory.
MAX_QUEUED_FRAMES = 4096
MIN_TOKEN_LENGTH = 32
# CLI connections are short; these bound what a stuck or misbehaving one can hold.
MAX_CLI_CONNECTIONS = 4
MAX_CLI_REQUESTS = 64
CLI_IDLE_SECONDS = 60.0

ROOTS = frozenset({"bot", "botgroup", "botscript", "botcontrollers"})
PRESENCE_KINDS = frozenset({"snapshot", "join", "quit", "locale"})
# The ServerThreadWorld methods endbotd may call on the plugin.
WORLD_METHODS = frozenset({
    "default_spawn", "resolve_spawn_placement", "queue_spawn_placement", "clear_pending_placement", "observe",
    "target", "block_info", "reachable_block", "find_blocks", "describe_entities", "inventory", "held_book_pages",
    "fill_held_book", "teleport", "look_at", "resolve_interaction", "resolve_mining", "resolve_dismount",
    "assert_name_available", "capacity",
})


class LinkError(RuntimeError):
    """A link failure with a wire-safe code (``unavailable``, ``timeout``, ``unauthorized``, ...)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def registry_hash() -> str:
    """Fingerprint of the operation registry; both ends of a link must agree on it."""
    canonical = json.dumps(REGISTRY, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def require_loopback(host: str) -> str:
    if host not in LOOPBACK_HOSTS:
        raise ValueError("the endbotd link must use loopback")
    return host


def _peer_is_loopback(sock: socket.socket) -> bool:
    try:
        return ipaddress.ip_address(sock.getpeername()[0].split("%", 1)[0]).is_loopback
    except (OSError, ValueError):
        return False


def load_token(path: Path) -> str:
    """Read the link token; errors never include its contents."""
    try:
        token = path.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise LinkError("unavailable", f"endbotd token unavailable: {type(error).__name__}") from None
    if len(token) < MIN_TOKEN_LENGTH:
        raise LinkError("unavailable", "endbotd token is invalid")
    return token


def token_matches(expected: str, supplied: object) -> bool:
    if not isinstance(supplied, str):
        return False
    return hmac.compare_digest(expected.encode("utf-8"), supplied.encode("utf-8"))


def encode(message: dict) -> bytes:
    try:
        body = json.dumps(message, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise LinkError("invalid", f"message is not JSON: {error}") from None
    if len(body) + 1 > MAX_FRAME_BYTES:
        raise LinkError("too_large", "message exceeds the 1 MiB frame limit")
    return body + b"\n"


class FrameReader:
    """Read newline-delimited JSON objects through one bounded buffer."""

    def __init__(self, sock: socket.socket) -> None:
        self._sock = sock
        self._buffer = bytearray()

    def read(self) -> dict:
        while True:
            index = self._buffer.find(b"\n")
            if index >= 0:
                line = bytes(self._buffer[:index])
                del self._buffer[:index + 1]
                break
            if len(self._buffer) >= MAX_FRAME_BYTES:
                raise LinkError("protocol", "frame exceeds the 1 MiB limit")
            chunk = self._sock.recv(65536)
            if not chunk:
                raise LinkError("unavailable", "link closed")
            self._buffer.extend(chunk)
        if len(line) + 1 > MAX_FRAME_BYTES:
            raise LinkError("protocol", "frame exceeds the 1 MiB limit")
        try:
            message = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise LinkError("protocol", "frame is not JSON") from None
        if not isinstance(message, dict) or not isinstance(message.get("id"), int):
            raise LinkError("protocol", "frame is not a message")
        return message


# -- value codecs ---------------------------------------------------------

_PRINCIPAL_WIRE = {
    "kind": "kind", "name": "name", "xuid": "xuid", "grant_revision": "grantRevision", "language": "language",
    "origin": "origin", "player_id": "playerId", "authorization_revision": "authorizationRevision",
    "run_id": "runId",
}


def principal_to_wire(principal: Principal) -> dict:
    """A Principal without its form guard, which travels as its own field."""
    wire = {}
    for field, key in _PRINCIPAL_WIRE.items():
        value = getattr(principal, field)
        if value is not None:
            wire[key] = list(value) if isinstance(value, tuple) else value
    return wire


def principal_from_wire(wire: object) -> Principal:
    if not isinstance(wire, dict):
        raise LinkError("invalid", "principal must be an object")
    values = {field: wire[key] for field, key in _PRINCIPAL_WIRE.items() if wire.get(key) is not None}
    if isinstance(values.get("authorization_revision"), list):
        values["authorization_revision"] = tuple(values["authorization_revision"])
    try:
        return Principal(**values)
    except (TypeError, ValueError) as error:
        raise LinkError("invalid", f"invalid principal: {error}") from None


def encode_value(value):
    """JSON-safe form of world-call arguments; Principals are tagged."""
    if isinstance(value, Principal):
        return {"$principal": principal_to_wire(value)}
    if isinstance(value, dict):
        return {str(key): encode_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [encode_value(item) for item in value]
    return value


def decode_value(value):
    if isinstance(value, dict):
        if set(value) == {"$principal"}:
            return principal_from_wire(value["$principal"])
        return {key: decode_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [decode_value(item) for item in value]
    return value


def error_to_wire(error: BaseException) -> dict:
    """Keep the exception class that callers branch on: ValueError (and its
    LocalizedError template) stays a user-facing failure, everything else is a
    LinkError with a code."""
    if isinstance(error, LinkError):
        return {"code": error.code, "message": str(error)}
    if isinstance(error, LocalizedError):
        params = {key: value if value is None or isinstance(value, (str, int, float, bool)) else str(value)
                  for key, value in error.params.items()}
        return {"code": "invalid", "message": str(error), "template": error.template, "params": params}
    if isinstance(error, ValueError):
        return {"code": "invalid", "message": str(error)}
    if isinstance(error, TimeoutError):
        return {"code": "timeout", "message": str(error) or "timed out"}
    if isinstance(error, RuntimeError):
        return {"code": "failed", "message": str(error)}
    return {"code": "internal", "message": type(error).__name__}


def error_from_wire(detail: object) -> Exception:
    if not isinstance(detail, dict):
        return LinkError("protocol", "malformed error")
    code, message = str(detail.get("code", "internal")), str(detail.get("message", ""))
    if code == "invalid":
        template, params = detail.get("template"), detail.get("params")
        if isinstance(template, str) and isinstance(params, dict):
            return LocalizedError(template, **params)
        return ValueError(message)
    return LinkError(code, message)


# -- the connection ------------------------------------------------------

_STOP = object()


class LinkConnection:
    """One established link: a reader, a writer, an ordered handler thread and
    an optional pool for request types that may run concurrently.

    ``handlers`` maps a request type to ``handler(body) -> result``. The reader
    thread only parses frames; it never runs a handler.
    """

    def __init__(
        self,
        sock: socket.socket,
        reader: FrameReader,
        handlers: dict[str, Callable[[dict], object]],
        *,
        concurrent: frozenset[str] = frozenset(),
        workers: int = 4,
        on_close: Callable[[LinkConnection, str], None] | None = None,
        first_id: int = 1,
        unique_ids: bool = False,
    ) -> None:
        self._sock = sock
        self._reader = reader
        self._handlers = handlers
        self._concurrent = concurrent
        self._on_close = on_close
        self._lock = threading.Lock()
        self._next_id = first_id
        self._pending: dict[int, Future] = {}
        self._closed = False
        self._outbound: queue.Queue = queue.Queue(MAX_QUEUED_FRAMES)
        self._ordered: queue.Queue = queue.Queue()
        self._pool = ThreadPoolExecutor(workers, thread_name_prefix="endbot-link") if concurrent else None
        # Request ids already received, when the peer must never reuse one (CLI connections).
        self._seen: set[int] | None = set() if unique_ids else None
        self.close_reason: str | None = None

    def start(self) -> LinkConnection:
        for target in (self._read_loop, self._write_loop, self._ordered_loop):
            threading.Thread(target=target, name=f"endbot-link-{target.__name__}", daemon=True).start()
        return self

    @property
    def closed(self) -> bool:
        return self._closed

    def send(self, kind: str, body: dict) -> Future:
        """Queue a request; the Future resolves with its result or exception."""
        future: Future = Future()
        with self._lock:
            if self._closed:
                future.set_exception(LinkError("unavailable", "link is closed"))
                return future
            request_id = self._next_id
            self._next_id += 1
            self._pending[request_id] = future
        try:
            frame = encode({"id": request_id, "type": kind, "body": body})
        except LinkError as error:
            with self._lock:
                self._pending.pop(request_id, None)
            future.set_exception(error)
            return future
        self._enqueue(frame)
        return future

    def request(self, kind: str, body: dict, timeout: float = REQUEST_SECONDS):
        future = self.send(kind, body)
        try:
            return future.result(timeout)
        except FutureTimeoutError:
            with self._lock:
                self._pending.pop(self._id_of(future), None)
            raise LinkError("timeout", f"{kind} request timed out after {timeout:g} s") from None

    def _id_of(self, future: Future) -> int | None:
        for request_id, pending in self._pending.items():
            if pending is future:
                return request_id
        return None

    def close(self, reason: str = "closed") -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self.close_reason = reason
            pending = tuple(self._pending.values())
            self._pending.clear()
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self._sock.close()
        for future in pending:
            if not future.done():
                future.set_exception(LinkError("unavailable", f"link closed: {reason}"))
        for channel in (self._outbound, self._ordered):
            try:
                channel.put_nowait(_STOP)
            except queue.Full:
                pass
        if self._pool is not None:
            self._pool.shutdown(wait=False)
        if self._on_close is not None:
            self._on_close(self, reason)

    def _enqueue(self, frame: bytes) -> None:
        try:
            self._outbound.put_nowait(frame)
        except queue.Full:
            self.close("peer stopped reading")

    def _read_loop(self) -> None:
        try:
            while not self._closed:
                message = self._reader.read()
                if "type" in message:
                    self._accept_request(message)
                else:
                    self._accept_reply(message)
        except LinkError as error:
            self.close(str(error))
        except OSError as error:
            self.close(f"link failed: {type(error).__name__}")

    def _accept_request(self, message: dict) -> None:
        kind, body = message.get("type"), message.get("body")
        if not isinstance(kind, str) or not isinstance(body, dict):
            raise LinkError("protocol", "malformed request")
        duplicate = False
        if self._seen is not None:
            duplicate = message["id"] in self._seen
            if not duplicate and len(self._seen) >= MAX_CLI_REQUESTS:
                raise LinkError("protocol", "too many requests on one connection")
            self._seen.add(message["id"])
        if kind in self._concurrent:
            try:
                self._pool.submit(self._handle, message["id"], kind, body, duplicate)
            except RuntimeError:
                return
        else:
            # A duplicate's refusal is queued too, so replies keep the arrival order.
            self._ordered.put((message["id"], kind, body, duplicate))

    def _accept_reply(self, message: dict) -> None:
        with self._lock:
            future = self._pending.pop(message["id"], None)
        if future is None or future.done():
            return  # The request already timed out.
        if message.get("ok") is True:
            future.set_result(message.get("result"))
        else:
            future.set_exception(error_from_wire(message.get("error")))

    def _ordered_loop(self) -> None:
        while True:
            item = self._ordered.get()
            if item is _STOP:
                return
            self._handle(*item)

    def _handle(self, request_id: int, kind: str, body: dict, duplicate: bool = False) -> None:
        handler = self._handlers.get(kind)
        try:
            if duplicate:
                raise LinkError("duplicate", f"request id {request_id} was already used; it was not run again")
            if handler is None:
                raise LinkError("unsupported", f"unknown request type {kind}")
            reply = {"id": request_id, "ok": True, "result": handler(body)}
        except Exception as error:  # noqa: BLE001 - every handler failure becomes an explicit error reply
            reply = {"id": request_id, "ok": False, "error": error_to_wire(error)}
        try:
            frame = encode(reply)
        except LinkError as error:
            frame = encode({"id": request_id, "ok": False, "error": error_to_wire(error)})
        if not self._closed:
            self._enqueue(frame)

    def _write_loop(self) -> None:
        while True:
            frame = self._outbound.get()
            if frame is _STOP:
                return
            try:
                self._sock.sendall(frame)
            except OSError as error:
                self.close(f"link failed: {type(error).__name__}")
                return


def connect(
    host: str,
    port: int,
    hello: dict,
    handlers: dict[str, Callable[[dict], object]],
    *,
    concurrent: frozenset[str] = frozenset(),
    on_close: Callable[[LinkConnection, str], None] | None = None,
    timeout: float = HANDSHAKE_SECONDS,
) -> tuple[LinkConnection, dict]:
    """Dial endbotd, send ``hello`` (which carries the token) and return the
    started connection with endbotd's hello result. Refusals raise LinkError."""
    require_loopback(host)
    try:
        sock = socket.create_connection((host, port), timeout)
    except OSError as error:
        raise LinkError("unavailable", f"endbotd unavailable: {type(error).__name__}") from None
    try:
        if not _peer_is_loopback(sock):
            raise LinkError("unauthorized", "endbotd is not on loopback")
        sock.sendall(encode({"id": 0, "type": "hello", "body": hello}))
        reader = FrameReader(sock)
        reply = reader.read()
        if reply.get("id") != 0 or "type" in reply:
            raise LinkError("protocol", "endbotd did not answer hello")
        if reply.get("ok") is not True:
            raise error_from_wire(reply.get("error"))
        result = reply.get("result")
        if not isinstance(result, dict) or result.get("protocol") != PROTOCOL:
            raise LinkError("protocol_mismatch", "endbotd speaks another link protocol")
        sock.settimeout(None)
    except BaseException as error:
        sock.close()
        if isinstance(error, OSError):
            raise LinkError("unavailable", f"endbotd hello failed: {type(error).__name__}") from None
        raise
    connection = LinkConnection(sock, reader, handlers, concurrent=concurrent, on_close=on_close)
    return connection.start(), result


# -- endbotd's end -------------------------------------------------------

class Presence:
    """Who is online, as the connected plugin reported it (P4).

    Every link starts a new ``epoch`` with a snapshot. Each player's session
    revision is ``(epoch, join counter)``: the counter numbers every join this
    epoch, so a quit and rejoin, a reconnect or a permission change endbotd
    decides (``bump``) always yields a new revision. No plugin, no presence.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.epoch: str | None = None
        self._joins = 0
        self._players: dict[str, dict] = {}

    def reset(self, epoch: str | None) -> None:
        with self._lock:
            self.epoch = epoch
            self._joins = 0
            self._players = {}

    def apply(self, body: dict) -> list[dict]:
        """Apply one report; return the player records it touched."""
        kind = body.get("kind")
        if kind not in PRESENCE_KINDS:
            raise LinkError("invalid", "unknown presence kind")
        with self._lock:
            if self.epoch is None:
                raise LinkError("unavailable", "no plugin link")
            if kind == "snapshot":
                players = body.get("players")
                if not isinstance(players, list):
                    raise LinkError("invalid", "snapshot needs players")
                self._players = {}
                return [self._join(_player_record(player)) for player in players]
            if kind == "join":
                return [self._join(_player_record(body.get("player")))]
            player_id = body.get("playerId")
            if not isinstance(player_id, str):
                raise LinkError("invalid", "presence needs playerId")
            if kind == "quit":
                record = self._players.pop(player_id, None)
                return [record] if record is not None else []
            record = self._players.get(player_id)
            if record is None:
                return []
            record["locale"] = body.get("locale") if isinstance(body.get("locale"), str) else None
            return [dict(record)]

    def _join(self, record: dict) -> dict:
        self._joins += 1
        record["revision"] = (self.epoch, self._joins)
        self._players[record["playerId"]] = record
        return dict(record)

    def bump(self, player_id: str) -> None:
        with self._lock:
            record = self._players.get(player_id)
            if record is not None:
                self._join(record)

    def player(self, player_id: str) -> dict | None:
        with self._lock:
            record = self._players.get(player_id)
            return dict(record) if record is not None else None

    def session_revision(self, player_id: str):
        record = self.player(player_id)
        return None if record is None else record["revision"]

    def online(self) -> list[dict]:
        with self._lock:
            return [dict(record) for record in self._players.values()]


def _player_record(player: object) -> dict:
    if not isinstance(player, dict) or not isinstance(player.get("playerId"), str) or not player["playerId"]:
        raise LinkError("invalid", "player needs playerId")
    return {"playerId": player["playerId"],
            "name": player.get("name") if isinstance(player.get("name"), str) else None,
            "xuid": player.get("xuid") if isinstance(player.get("xuid"), str) else None,
            "locale": player.get("locale") if isinstance(player.get("locale"), str) else None}


@dataclass(frozen=True, slots=True)
class Submission:
    """A command line or form reply the plugin forwarded, with the principal
    endbotd derived from presence at the point it arrived on the stream."""

    root: str
    principal: Principal
    argv: tuple[str, ...] | None = None
    operation: dict | None = None
    guard: dict | None = None


class LinkServer:
    """endbotd's listener: accepts one authenticated plugin link at a time.

    A newer authenticated link replaces the older one (a restarted BDS
    reconnects before the old socket is noticed dead). ``on_submit`` and
    ``on_presence`` run on the link's ordered thread, in stream order.
    """

    def __init__(
        self,
        host: str,
        port: int,
        token: str,
        *,
        version: str,
        config: dict,
        on_submit: Callable[[Submission], object],
        on_presence: Callable[[str, list[dict]], None] | None = None,
        grant_revision: Callable[[str], int] | None = None,
        log: Callable[[str, str], None] | None = None,
        expected_registry: str | None = None,
        handshake_timeout: float = HANDSHAKE_SECONDS,
        world_timeout: float = REQUEST_SECONDS,
        cli_handlers: dict[str, Callable[[dict], object]] | None = None,
    ) -> None:
        self._host = require_loopback(host)
        self._port = port
        if len(token) < MIN_TOKEN_LENGTH:
            raise ValueError("endbotd token is invalid")
        self._token = token
        self._version = version
        self._config = config
        self._on_submit = on_submit
        self._on_presence = on_presence
        self._grant_revision = grant_revision or (lambda player_id: 0)
        self._log = log or (lambda level, message: None)
        self._registry = expected_registry or registry_hash()
        self._handshake_timeout = handshake_timeout
        self._lock = threading.Lock()
        self._listener: socket.socket | None = None
        self._link: LinkConnection | None = None
        self._cli_handlers = cli_handlers or {}
        self._cli: set[LinkConnection] = set()
        self._stopped = False
        self.presence = Presence()
        self.world = LinkWorld(self, world_timeout)

    @property
    def address(self) -> tuple[str, int]:
        return self._listener.getsockname()[:2]

    @property
    def connected(self) -> bool:
        with self._lock:
            return self._link is not None

    def start(self) -> LinkServer:
        family = socket.AF_INET6 if self._host == "::1" else socket.AF_INET
        listener = socket.socket(family, socket.SOCK_STREAM)
        listener.bind(("::1" if family == socket.AF_INET6 else "127.0.0.1", self._port))
        listener.listen(4)
        self._listener = listener
        threading.Thread(target=self._accept_loop, name="endbotd-link-accept", daemon=True).start()
        return self

    def stop(self) -> None:
        with self._lock:
            self._stopped = True
            link, self._link = self._link, None
            listener = self._listener
            cli, self._cli = self._cli, set()
        if listener is not None:
            listener.close()
        if link is not None:
            link.close("endbotd stopping")
        for connection in cli:
            connection.close("endbotd stopping")
        self.presence.reset(None)

    def drop_link(self, reason: str = "dropped") -> None:
        with self._lock:
            link = self._link
        if link is not None:
            link.close(reason)

    def _accept_loop(self) -> None:
        while True:
            try:
                sock, _ = self._listener.accept()
            except OSError:
                return
            if not _peer_is_loopback(sock):
                sock.close()
                continue
            threading.Thread(target=self._handshake, args=(sock,), name="endbotd-link-hello", daemon=True).start()

    def _handshake(self, sock: socket.socket) -> None:
        reader = FrameReader(sock)
        cli = False
        try:
            sock.settimeout(self._handshake_timeout)
            message = reader.read()
            body = message.get("body")
            if message.get("type") != "hello" or not isinstance(body, dict):
                raise LinkError("protocol", "the link must open with hello")
            cli = body.get("client") == "cli"
            if not token_matches(self._token, body.get("token")):
                raise LinkError("unauthorized", "invalid endbotd token")
            peer = "endbot CLI" if cli else "plugin"
            if body.get("protocol") != PROTOCOL:
                raise LinkError("protocol_mismatch", f"{peer} speaks link protocol {body.get('protocol')!r}, "
                                                     f"endbotd speaks {PROTOCOL}")
            if cli:
                if not self._cli_handlers:
                    raise LinkError("unsupported", "this endbotd takes no CLI connections")
                sock.settimeout(CLI_IDLE_SECONDS)
                self._accept_cli(sock, reader)
                return
            if body.get("registryHash") != self._registry:
                raise LinkError("registry_mismatch", "plugin and endbotd carry different operation registries")
            epoch = body.get("epoch")
            if not isinstance(epoch, str) or not epoch:
                raise LinkError("invalid", "hello needs an epoch")
            sock.settimeout(None)
        except (LinkError, OSError) as error:
            if isinstance(error, LinkError) and error.code != "unavailable":
                self._log("warning", f"{'endbot CLI connection' if cli else 'Endbot plugin link'} refused: {error}")
                try:
                    sock.sendall(encode({"id": 0, "ok": False, "error": error_to_wire(error)}))
                except OSError:
                    pass
            sock.close()
            return
        handlers = {"presence": self._presence, "submit": self._submit}
        connection = LinkConnection(sock, reader, handlers, on_close=self._closed)
        with self._lock:
            if self._stopped:
                sock.close()
                return
            previous, self._link = self._link, connection
            self.presence.reset(epoch)
            connection._enqueue(encode({"id": 0, "ok": True, "result": {
                "protocol": PROTOCOL, "endbotdVersion": self._version, "config": self._config}}))
            connection.start()
        if previous is not None:
            previous.close("replaced by a newer plugin link")
        self._log("info", f"Endbot plugin {body.get('pluginVersion')} linked")

    def _accept_cli(self, sock: socket.socket, reader: FrameReader) -> None:
        """Run one CLI connection beside the plugin link; it never replaces the link."""
        connection = LinkConnection(sock, reader, self._cli_handlers, on_close=self._cli_closed, unique_ids=True)
        with self._lock:
            if self._stopped:
                raise LinkError("unavailable", "endbotd is stopping")
            if len(self._cli) >= MAX_CLI_CONNECTIONS:
                raise LinkError("busy", "too many endbot CLI connections; retry shortly")
            self._cli.add(connection)
            connection._enqueue(encode({"id": 0, "ok": True, "result": {
                "protocol": PROTOCOL, "endbotdVersion": self._version}}))
            connection.start()

    def _cli_closed(self, connection: LinkConnection, reason: str) -> None:
        with self._lock:
            self._cli.discard(connection)

    def _closed(self, connection: LinkConnection, reason: str) -> None:
        with self._lock:
            if self._link is not connection:
                return
            self._link = None
            self.presence.reset(None)
        self._log("warning", f"Endbot plugin link closed: {reason}")

    def _presence(self, body: dict) -> dict:
        records = self.presence.apply(body)
        if self._on_presence is not None:
            self._on_presence(body["kind"], records)
        return {"epoch": self.presence.epoch}

    def _submit(self, body: dict):
        root = body.get("root")
        if root not in ROOTS:
            raise LinkError("invalid", "unknown command root")
        argv, operation, guard = body.get("argv"), body.get("operation"), body.get("guard")
        if (argv is None) == (operation is None):
            raise LinkError("invalid", "submit needs argv or operation")
        if argv is not None and (not isinstance(argv, list) or not all(isinstance(item, str) for item in argv)):
            raise LinkError("invalid", "argv must be a list of strings")
        if operation is not None and not isinstance(operation, dict):
            raise LinkError("invalid", "operation must be an object")
        if guard is not None and not isinstance(guard, dict):
            raise LinkError("invalid", "guard must be an object")
        return self._on_submit(Submission(root, self._sender(body.get("sender")),
                                          None if argv is None else tuple(argv), operation, guard))

    def _sender(self, wire: object) -> Principal:
        """Only the plugin's word on who typed it; revisions, names and XUIDs come from endbotd's own state."""
        if not isinstance(wire, dict) or wire.get("kind") not in {"player", "console"}:
            raise LinkError("invalid", "sender must be a player or the console")
        language = wire.get("language") if isinstance(wire.get("language"), str) else "en"
        origin = wire.get("origin") if isinstance(wire.get("origin"), dict) else None
        if wire["kind"] == "console":
            return Principal("console", origin=origin)
        player_id = wire.get("playerId")
        if not isinstance(player_id, str) or not player_id:
            raise LinkError("invalid", "a player sender needs playerId")
        record = self.presence.player(player_id) or {}
        return Principal(
            "player", name=record.get("name"), xuid=record.get("xuid"),
            grant_revision=self._grant_revision(player_id), language=language, origin=origin,
            player_id=player_id, authorization_revision=record.get("revision"),
        )

    def request(self, kind: str, body: dict, timeout: float):
        with self._lock:
            link = self._link
        if link is None:
            raise LinkError("unavailable", "the Endbot plugin is not connected")
        return link.request(kind, body, timeout)

    def deliver(self, to: str, lines: list[str], view: dict | None = None, timeout: float = REQUEST_SECONDS) -> bool:
        """Chat lines (and an optional form view) to a player id or ``console``."""
        body = {"to": to, "lines": list(lines)}
        if view is not None:
            body["view"] = view
        return bool(self.request("deliver", body, timeout).get("delivered"))

    def permission(self, player_id: str, allowed: bool, timeout: float = REQUEST_SECONDS) -> bool:
        """Set one player's control attachment; False when the player is not online."""
        return bool(self.request("permission", {"playerId": player_id, "allowed": bool(allowed)},
                                 timeout).get("applied"))


class LinkWorld:
    """The ServerThreadWorld methods, answered by the plugin over the link."""

    def __init__(self, server: LinkServer, timeout: float) -> None:
        self._server = server
        self._timeout = timeout

    def __getattr__(self, method: str):
        if method not in WORLD_METHODS:
            raise AttributeError(method)

        def call(*args, **kwargs):
            body = {"method": method, "args": encode_value(list(args)), "kwargs": encode_value(kwargs)}
            return self._server.request("world", body, self._timeout)

        return call
