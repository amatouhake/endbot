"""``endbot controllers add|remove|list`` through endbotd's loopback CLI endpoint (docs/ENDBOTD.md P8).

endbotd's link listener also takes CLI connections: a ``hello`` with
``"client": "cli"`` and the endbotd token, then one request whose reply is the
result. A request id is used once per connection, so a reply can only answer
this request and endbotd refuses a replayed one. Nothing waits on files: a
stopped endbotd refuses the connection at once.

Bounds as the link's: loopback only, a 1 MiB frame limit, a deadline, and the
token never printed or put in an error message.
"""

from __future__ import annotations

import ipaddress
import json
import socket
import time
from pathlib import Path

from endbot_cli.runstate import RUNNING, inspect_supervisor

PROTOCOL = 1
HOST = "127.0.0.1"
MAX_FRAME_BYTES = 1024 * 1024
MIN_TOKEN_LENGTH = 32
REQUEST_ID = 1
UNKNOWN = "controller result is unknown; inspect `endbot controllers list` before retrying"


class EndbotdError(ValueError):
    """A failure the CLI reports as one line; ``code`` is endbotd's error code or a local one."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _token(path: Path) -> str:
    try:
        token = path.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise EndbotdError("unavailable", f"cannot read {path} ({type(error).__name__}); run `endbot start`") from None
    if len(token) < MIN_TOKEN_LENGTH:
        raise EndbotdError("unavailable", f"{path} is invalid; delete it and run `endbot start`")
    return token


def _read_frame(sock: socket.socket, buffer: bytearray, deadline: float) -> dict:
    while True:
        index = buffer.find(b"\n")
        if index >= 0:
            line = bytes(buffer[:index])
            del buffer[:index + 1]
            break
        if len(buffer) >= MAX_FRAME_BYTES:
            raise EndbotdError("protocol", "endbotd sent a frame over the 1 MiB limit")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError
        sock.settimeout(remaining)
        chunk = sock.recv(65536)
        if not chunk:
            raise ConnectionResetError
        buffer.extend(chunk)
    if len(line) + 1 > MAX_FRAME_BYTES:
        raise EndbotdError("protocol", "endbotd sent a frame over the 1 MiB limit")
    try:
        message = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise EndbotdError("protocol", "endbotd sent a frame that is not JSON") from None
    if not isinstance(message, dict) or not isinstance(message.get("id"), int) or "type" in message:
        raise EndbotdError("protocol", "endbotd sent a frame that is not a reply")
    return message


def _error(reply: dict) -> EndbotdError:
    detail = reply.get("error")
    detail = detail if isinstance(detail, dict) else {}
    return EndbotdError(str(detail.get("code", "failed")), str(detail.get("message", "endbotd refused the request")))


def _valid_result(result: object) -> dict:
    if (
        not isinstance(result, dict)
        or not isinstance(result.get("ok"), bool)
        or not isinstance(result.get("messages"), list)
        or not result["messages"]
        or any(not isinstance(message, str) for message in result["messages"])
        or not isinstance(result.get("entries", []), list)
    ):
        raise EndbotdError("protocol", "endbotd sent an invalid controllers result")
    return result


def controllers_request(
    run_dir: Path, token_file: Path, port: int, operation: str, target: str | None = None, *, timeout: float = 15.0
) -> dict:
    """Run one controllers operation in endbotd and return its result ``{ok, messages, entries}``."""
    if operation not in {"add", "remove", "list"}:
        raise EndbotdError("invalid", "expected add, remove, or list")
    if operation == "list":
        target = None
    elif not isinstance(target, str) or not target.strip() or not target.isprintable() or len(target) > 256:
        raise EndbotdError("invalid", "a non-empty printable target of at most 256 characters is required")
    if inspect_supervisor(run_dir).status != RUNNING:
        raise EndbotdError("unavailable", "the instance is not running; start it with `endbot start`")
    token = _token(token_file)
    deadline = time.monotonic() + timeout
    try:
        sock = socket.create_connection((HOST, port), min(timeout, 5.0))
    except OSError:
        raise EndbotdError(
            "unavailable",
            f"endbotd is not running (nothing listens on {HOST}:{port}); the supervisor restarts it, "
            "so retry shortly or check `endbot status`",
        ) from None
    sent = False
    try:
        try:
            if not ipaddress.ip_address(sock.getpeername()[0]).is_loopback:
                raise EndbotdError("unauthorized", "endbotd is not on loopback")
            buffer = bytearray()
            hello = {"client": "cli", "protocol": PROTOCOL, "token": token}
            sock.sendall(json.dumps({"id": 0, "type": "hello", "body": hello}).encode("utf-8") + b"\n")
            reply = _read_frame(sock, buffer, deadline)
            if reply.get("id") != 0:
                raise EndbotdError("protocol", "endbotd did not answer hello")
            if reply.get("ok") is not True:
                raise _error(reply)
            if not isinstance(reply.get("result"), dict) or reply["result"].get("protocol") != PROTOCOL:
                raise EndbotdError("protocol_mismatch", "endbotd speaks another protocol; run `endbot update`")
            request = {"id": REQUEST_ID, "type": "controllers", "body": {"operation": operation, "target": target}}
            sock.sendall(json.dumps(request).encode("utf-8") + b"\n")
            sent = True
            reply = _read_frame(sock, buffer, deadline)
        except (TimeoutError, OSError) as error:
            lost = "timed out" if isinstance(error, TimeoutError) else "closed the connection"
            if sent and operation != "list":
                raise EndbotdError("unknown", f"endbotd {lost}; {UNKNOWN}") from None
            raise EndbotdError("unavailable", f"endbotd {lost}; retry shortly") from None
        if reply.get("id") != REQUEST_ID:
            raise EndbotdError("unknown", f"endbotd answered another request; {UNKNOWN}")
        if reply.get("ok") is not True:
            raise _error(reply)
        return _valid_result(reply.get("result"))
    finally:
        sock.close()
