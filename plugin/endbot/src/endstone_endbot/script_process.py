"""Bounded synchronous NDJSON pipe. The owner thread is never the server thread."""

import json
import queue
import subprocess
import threading
import time

from endstone_endbot.i18n import LocalizedError
from endstone_endbot.script_config import FRAME_BYTES


class RunnerProcess:
    def __init__(self, executable, source, bootstrap, operations, limits):
        if not executable.is_file():
            raise LocalizedError("scripting runner not built/installed")
        self._events = queue.Queue(maxsize=8)
        self._closed = threading.Event()
        self._process = subprocess.Popen(
            [str(executable)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        # Independent of VM callbacks and pipe writes (including the initial load).
        self._watchdog = None
        if limits["wallMs"] > 0:
            self._watchdog = threading.Timer(limits["wallMs"] / 1000, self._terminate)
            self._watchdog.daemon = True
            self._watchdog.start()
        self._reader = threading.Thread(target=self._read, daemon=True, name="endbot-script-pipe")
        self._reader.start()
        try:
            self.send(
                {"type": "load", "source": source, "bootstrap": bootstrap, "operations": operations, "limits": limits}
            )
        except BaseException:
            self.close()
            raise

    def _read(self):
        try:
            while not self._closed.is_set():
                line = self._process.stdout.readline(FRAME_BYTES + 1)
                if not line or len(line) > FRAME_BYTES or not line.endswith(b"\n"):
                    raise ValueError("runner EOF/oversized frame")
                event = json.loads(line)
                if not isinstance(event, dict):
                    raise ValueError("invalid runner event")  # noqa: TRY004 - protocol validation
                self._events.put_nowait(event)
        except (ValueError, OSError, queue.Full) as error:
            try:
                self._events.put_nowait(error)
            except queue.Full:
                pass
            self._terminate()

    def send(self, event):
        line = json.dumps(event, ensure_ascii=True, separators=(",", ":")).encode() + b"\n"
        if len(line) > FRAME_BYTES:
            raise LocalizedError("Script protocol frame too large")
        self._process.stdin.write(line)
        self._process.stdin.flush()

    def _terminate(self):
        try:
            if self._process.poll() is None:
                self._process.kill()
        except OSError:
            pass  # It may have exited between poll and kill.

    def receive(self, deadline, canceled, check):
        while True:
            check()
            if canceled.is_set() or (deadline is not None and time.monotonic() >= deadline):
                raise LocalizedError("Script stopped or time limit reached")
            try:
                timeout = 0.1 if deadline is None else min(0.1, max(0.001, deadline - time.monotonic()))
                event = self._events.get(timeout=timeout)
            except queue.Empty:
                if self._process.poll() is not None:
                    raise LocalizedError("Script runner failed: {error}", error="runner exited") from None
                continue
            if isinstance(event, BaseException):
                raise LocalizedError("Script runner failed: {error}", error=str(event))
            return event

    def close(self):
        self._closed.set()
        if self._watchdog is not None:
            self._watchdog.cancel()
        self._terminate()
        self._process.wait(timeout=5)
        for stream in (self._process.stdin, self._process.stdout):
            stream.close()
        self._reader.join(timeout=1)
