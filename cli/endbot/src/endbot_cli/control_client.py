"""Minimal client for the local runtime control protocol (docs/OPERATIONS.md section 7).

Copied from ``plugin/endbot/src/endstone_endbot/control.py`` so the doctor can
probe runtime reachability without importing the plugin (which would drag in
``endstone``). Keep the newline-delimited JSON wire format in sync with the
plugin and the runtime: request ``{"version", "id", "token", "method",
"params"}``, response ``{"version", "id", "ok", "result" | "error"}``.
"""

from __future__ import annotations

import json
import socket
import uuid
from pathlib import Path


class RuntimeControlError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class RuntimeControlClient:
    def __init__(self, host: str, port: int, token_file: Path, timeout: float = 10.0) -> None:
        if host not in {"127.0.0.1", "::1", "localhost"}:
            raise ValueError("Endbot runtime control must use loopback")
        self.host = host
        self.port = port
        self.token_file = token_file
        self.timeout = timeout

    def request(self, method: str, **parameters):
        try:
            token = self.token_file.read_text(encoding="utf-8").strip()
        except OSError as error:
            raise RuntimeControlError("unavailable", f"runtime token unavailable: {error}") from error
        if len(token) < 32:
            raise RuntimeControlError("unavailable", "runtime token is invalid")
        request_id = str(uuid.uuid4())
        payload = (
            json.dumps(
                {"version": 1, "id": request_id, "token": token, "method": method, "params": parameters},
                separators=(",", ":"),
            ).encode()
            + b"\n"
        )
        try:
            with socket.create_connection((self.host, self.port), self.timeout) as connection:
                connection.settimeout(self.timeout)
                connection.sendall(payload)
                response = bytearray()
                while b"\n" not in response:
                    chunk = connection.recv(8192)
                    if not chunk:
                        break
                    response.extend(chunk)
                    if len(response) > 64 * 1024:
                        raise RuntimeControlError("protocol", "runtime response is too large")
        except OSError as error:
            raise RuntimeControlError("unavailable", f"runtime unavailable: {error}") from error
        try:
            decoded = json.loads(bytes(response).split(b"\n", 1)[0])
        except (ValueError, IndexError) as error:
            raise RuntimeControlError("protocol", "runtime returned an invalid response") from error
        if decoded.get("id") != request_id or decoded.get("version") != 1:
            raise RuntimeControlError("protocol", "runtime response did not match the request")
        if not decoded.get("ok"):
            detail = decoded.get("error", {})
            raise RuntimeControlError(
                str(detail.get("code", "runtime_error")),
                str(detail.get("message", "runtime error")),
            )
        return decoded.get("result")
