"""Bounded synchronous NDJSON pipe. The owner thread is never the server thread."""

import errno
import json
import logging
import queue
import subprocess
import threading
import time

from endstone_endbot.i18n import LocalizedError
from endstone_endbot.script_config import FRAME_BYTES
from endstone_endbot.script_confine import Confinement

LOGGER = logging.getLogger(__name__)


class RunnerProcess:
    def __init__(self, executable, source, bootstrap, operations, limits):
        if not executable.is_file():
            raise LocalizedError("scripting runner not built/installed")
        self._events = queue.Queue(maxsize=8)
        self._closed = threading.Event()
        self._confine, self._process = None, None
        self._watchdog, self._reader = None, None
        self._host_terminated = False
        self._confinement_error = None
        try:
            self._confine = Confinement(limits)
            options = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
            options.update(self._confine.popen_kwargs)
            self._process = subprocess.Popen(
                [str(executable), *self._confine.runner_args],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, **options
            )
            self._confine.attach(self._process)
        except BaseException as error:
            LOGGER.exception("Endbot runner OS confinement/startup failed")
            try:
                self.close()
            except Exception:
                LOGGER.exception("Endbot runner startup cleanup failed")
            if not isinstance(error, Exception):
                raise
            if isinstance(error, OSError) and error.errno == errno.ENOMEM:
                raise LocalizedError("Script runner process memory allocation failed") from error
            if limits.get("osConfinement", True):
                raise LocalizedError("Script runner OS confinement failed; see the server log") from error
            raise LocalizedError("Script runner failed: {error}", error=str(error)) from error
        # Independent of VM callbacks and pipe writes (including the initial load).
        try:
            if limits["wallMs"] > 0:
                self._watchdog = threading.Timer(limits["wallMs"] / 1000, self._terminate)
                self._watchdog.daemon = True
                self._watchdog.start()
            self._reader = threading.Thread(target=self._read, daemon=True, name="endbot-script-pipe")
            self._reader.start()
            self.send(
                {"type": "load", "source": source, "bootstrap": bootstrap, "operations": operations,
                 "limits": {key: value for key, value in limits.items()
                            if key not in {"osConfinement", "processMemoryBytes", "cpuSeconds"}}}
            )
        except BaseException:
            try:
                self.close()
            except Exception:
                LOGGER.exception("Endbot runner load cleanup failed")
            raise

    def _read(self):
        try:
            while not self._closed.is_set():
                line = self._process.stdout.readline(FRAME_BYTES + 1)
                if not line:
                    # EOF can precede waitpid/GetExitCodeProcess observing the actual exit.
                    self._wait_exit()
                if not line or len(line) > FRAME_BYTES or not line.endswith(b"\n"):
                    raise ValueError("runner EOF/oversized frame")
                event = json.loads(line)
                if not isinstance(event, dict):
                    raise ValueError("invalid runner event")  # noqa: TRY004 - protocol validation
                # Only the runner's own phase marks a confinement failure; script text cannot.
                if event.get("type") == "error" and event.get("phase") == "confinement":
                    self._confinement_error = str(event.get("message", "confinement failed"))
                    LOGGER.error("Endbot runner OS confinement failed: %s", self._confinement_error)
                    raise ValueError(self._confinement_error)
                # Logs need no reply, so a script can emit them faster than the host handles them. Wait for
                # room instead of failing: while this thread waits it stops reading, and the runner blocks on
                # its full pipe, so the queue bound still holds.
                while not self._closed.is_set():
                    try:
                        self._events.put(event, timeout=0.1)
                        break
                    except queue.Full:
                        continue
        except (ValueError, OSError) as error:
            try:
                self._events.put_nowait(error)
            except queue.Full:
                pass
            self._terminate()

    def send(self, event):
        line = json.dumps(event, ensure_ascii=True, separators=(",", ":")).encode() + b"\n"
        if len(line) > FRAME_BYTES:
            raise LocalizedError("Script protocol frame too large")
        try:
            self._process.stdin.write(line)
            self._process.stdin.flush()
        except OSError as error:
            self._wait_exit()
            self._failure(str(error))

    def _wait_exit(self):
        try:
            self._process.wait(timeout=0.2)
        except subprocess.TimeoutExpired:
            pass

    def _failure(self, detail):
        if getattr(self, "_confinement_error", None) is not None:
            raise LocalizedError("Script runner OS confinement failed; see the server log")
        reason = None if self._host_terminated else self._confine.failure(self._process.poll())
        if reason == "memory":
            raise LocalizedError("Script runner process memory allocation failed")
        if reason == "cpu":
            raise LocalizedError("Script runner exceeded the CPU time limit")
        raise LocalizedError("Script runner failed: {error}", error=detail)

    def _terminate(self):
        try:
            if self._process.poll() is None:
                self._host_terminated = True
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
                # The runner may exit while events it wrote are still in the pipe; fail only once the reader
                # has drained them (it ends at EOF, queueing the error).
                reader = getattr(self, "_reader", None)
                if self._process.poll() is not None and not (reader is not None and reader.is_alive()):
                    self._failure("runner exited")
                continue
            if isinstance(event, BaseException):
                self._failure(str(event))
            return event

    def close(self):
        self._closed.set()
        if self._watchdog is not None:
            self._watchdog.cancel()
        if self._process is None:
            if self._confine is not None:
                self._confine.close()
            return
        self._terminate()
        try:
            if self._confine is not None:
                self._confine.close()
        finally:
            try:
                self._process.wait(timeout=5)
            finally:
                for stream in (self._process.stdin, self._process.stdout):
                    try:
                        stream.close()
                    except OSError:
                        pass  # A dead runner can break the buffered writer during close.
                if self._reader is not None and self._reader.ident is not None:
                    self._reader.join(timeout=1)
