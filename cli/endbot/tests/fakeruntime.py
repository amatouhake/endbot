"""Fake Endbot runtime child for supervisor tests (tests only).

Impersonates ``runtime/src/cli.js``: creates the control token and owner key
pair on first start (like ``loadOrCreateControlToken`` / ``prepareOwnerKeyPair``),
prints the ``endbot_runtime_ready`` line, and answers the control protocol
(``ping`` and ``shutdown``). Environment knobs (tests only):

- ``FAKE_RUNTIME_STARTS_FILE``: append one line per start (restart counting);
- ``FAKE_RUNTIME_CRASH_ON_START``: ``all`` or a 1-based start number: that run
  exits with code 3 shortly after becoming ready;
- ``FAKE_RUNTIME_CRASH_DELAY``: seconds between ready and the crash (default 0.05).
"""

from __future__ import annotations

import json
import os
import socket
import sys
import time
from pathlib import Path

CONTROL_METHODS = {"ping", "shutdown"}


def _start_number() -> int:
    starts_file = os.environ.get("FAKE_RUNTIME_STARTS_FILE")
    if not starts_file:
        return 1
    path = Path(starts_file)
    existing = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    with open(path, "a", encoding="utf-8", newline="\n") as handle:
        handle.write("start\n")
    return len(existing) + 1


def _should_crash(start_number: int) -> bool:
    setting = os.environ.get("FAKE_RUNTIME_CRASH_ON_START", "")
    return setting == "all" or (setting.isdecimal() and int(setting) == start_number)


def _ensure_identity(config: dict) -> str:
    """Create the runtime-owned secrets on first start; return the control token."""

    Path(config["dataDirectory"], "profiles").mkdir(parents=True, exist_ok=True)
    token_path = Path(config["controlTokenPath"])
    token_path.parent.mkdir(parents=True, exist_ok=True)
    if not token_path.exists():
        token_path.write_text("test-control-token-0123456789abcdef0123456789\n", encoding="utf-8")
    private_path = Path(config["ownerPrivateKeyPath"])
    if not private_path.exists():
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec

        private_key = ec.generate_private_key(ec.SECP384R1())
        private_path.parent.mkdir(parents=True, exist_ok=True)
        private_path.write_bytes(
            private_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        Path(config["ownerPublicKeyPath"]).write_bytes(
            private_key.public_key().public_bytes(
                serialization.Encoding.PEM,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            )
        )
    return token_path.read_text(encoding="utf-8").strip()


def _serve(config: dict, token: str) -> int:
    host = config.get("controlHost", "127.0.0.1")
    port = int(config.get("controlPort", 19142))
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((host, port))
        listener.listen(4)
        listener.settimeout(0.2)
        while True:
            try:
                connection, _address = listener.accept()
            except TimeoutError:
                continue
            with connection:
                connection.settimeout(5)
                body = b""
                while b"\n" not in body and len(body) < 64 * 1024:
                    chunk = connection.recv(4096)
                    if not chunk:
                        break
                    body += chunk
                try:
                    request = json.loads(body.split(b"\n", 1)[0].decode("utf-8"))
                except (ValueError, IndexError):
                    continue
                request_id = request.get("id", "")
                method = request.get("method")
                if request.get("token") != token:
                    response = {"version": 1, "id": request_id, "ok": False, "error": {"code": "unauthorized"}}
                elif method not in CONTROL_METHODS:
                    response = {"version": 1, "id": request_id, "ok": False, "error": {"code": "unknown_method"}}
                else:
                    response = {"version": 1, "id": request_id, "ok": True, "result": {"stopping": True}}
                connection.sendall(json.dumps(response).encode("utf-8") + b"\n")
                if method == "shutdown" and request.get("token") == token:
                    return 0


def main(argv: list[str]) -> int:
    if "--config" not in argv:
        print("fake runtime: --config is required", file=sys.stderr)
        return 2
    config = json.loads(Path(argv[argv.index("--config") + 1]).read_text(encoding="utf-8"))
    start_number = _start_number()
    if os.environ.get("FAKE_RUNTIME_CRASH_IMMEDIATE"):
        print(f"fake runtime: crashing before ready (start {start_number})", flush=True)
        return 3
    token = _ensure_identity(config)
    print(
        json.dumps(
            {
                "event": "endbot_runtime_ready",
                "host": config.get("controlHost", "127.0.0.1"),
                "port": config.get("controlPort"),
            }
        ),
        flush=True,
    )
    if _should_crash(start_number):
        time.sleep(float(os.environ.get("FAKE_RUNTIME_CRASH_DELAY", "0.05")))
        print(f"fake runtime: crashing after ready (start {start_number})", flush=True)
        return 3
    return _serve(config, token)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
