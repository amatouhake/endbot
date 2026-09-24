"""Foreground supervisor for the Endbot runtime and Endstone/BDS (docs/OPERATIONS.md section 5a).

``endbot start`` runs one supervisor in the foreground. It starts the Endbot
runtime (Node), waits for its ``endbot_runtime_ready`` line, re-checks
``[local-bot-auth]``, then starts Endstone/BDS with its stdin on a pipe the
supervisor owns. From then on:

- lines typed on the supervisor's stdin and lines queued in
  ``state/run/console.request`` are forwarded to the BDS console;
- ``state/run/stop.request`` (from `endbot stop`, or Ctrl+C) triggers the stop
  sequence: `stop` to the BDS console (escalating to a process-tree kill after
  the timeout), then a graceful runtime ``shutdown`` (also escalating);
- if BDS exits on its own, the runtime is stopped gracefully and the
  supervisor exits with BDS's exit code (no auto-restart);
- if the runtime exits unexpectedly while BDS runs, it is restarted with
  exponential backoff (1 s, 2 s, 4 s ... capped at 30 s); after 5 failures
  inside a 5-minute window the supervisor stops BDS and exits non-zero.

Per-start logs land in ``state/run/logs/<start>/{runtime,server}.log`` (the
last 10 starts are kept); exit codes land in ``state/run/last-exit.json``.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from endbot_cli.control_client import RuntimeControlClient, RuntimeControlError
from endbot_cli.processes import kill_process_tree, spawn_child
from endbot_cli.runstate import (
    CONSOLE_REQUEST,
    KEEP_LOG_STARTS,
    RUNTIME_PID,
    SERVER_PID,
    STOP_REQUEST,
    SUPERVISOR_PID,
    consume_console_lines,
    make_log_dir,
    stop_requested,
    write_last_exit,
    write_pid_file,
)


@dataclass(frozen=True, slots=True)
class SupervisorOptions:
    run_dir: Path
    runtime_command: tuple[str, ...]
    server_command: tuple[str, ...]
    control_token_file: Path
    control_host: str = "127.0.0.1"
    control_port: int = 19142
    ready_timeout: float = 30.0
    stop_timeout: float = 60.0
    runtime_stop_timeout: float = 15.0
    poll_interval: float = 0.2
    restart_initial_backoff: float = 1.0
    restart_max_backoff: float = 30.0
    restart_max_failures: int = 5
    restart_failure_window: float = 300.0
    keep_log_starts: int = KEEP_LOG_STARTS
    runtime_control_timeout: float = 5.0


class _OutputReader(threading.Thread):
    """Drains one child's stdout into its log file and an echo queue."""

    def __init__(
        self,
        stream,
        log_path: Path,
        lines: queue.Queue,
        ready_event: threading.Event | None = None,
    ) -> None:
        super().__init__(daemon=True, name=f"output-{log_path.name}")
        self.stream = stream
        self.lines = lines
        self.ready_event = ready_event
        self.log_path = log_path
        self.log = None

    def run(self) -> None:
        with open(self.log_path, "w", encoding="utf-8", newline="\n") as self.log:
            try:
                for raw in iter(self.stream.readline, b""):
                    line = raw.decode("utf-8", "replace").rstrip("\n")
                    self.log.write(line + "\n")
                    self.log.flush()
                    if self.ready_event is not None and not self.ready_event.is_set():
                        try:
                            parsed = json.loads(line)
                        except ValueError:
                            parsed = None
                        if isinstance(parsed, dict) and parsed.get("event") == "endbot_runtime_ready":
                            self.ready_event.set()
                    self.lines.put(line)
            except (OSError, ValueError):
                pass


def _default_echo(line: str) -> None:
    print(line, flush=True)


class Supervisor:
    """Runs the two children until a stop condition; ``run()`` returns the exit code."""

    def __init__(
        self,
        options: SupervisorOptions,
        *,
        stdin: Iterable[str] | None = None,
        echo: Callable[[str], None] | None = None,
        pre_server_check: Callable[[], str | None] | None = None,
    ) -> None:
        self.options = options
        self._stdin = stdin
        self._echo = echo or _default_echo
        self._pre_server_check = pre_server_check or (lambda: None)
        self._stop_flag = threading.Event()
        self._stdin_lines: queue.Queue[str] = queue.Queue()
        self._runtime_lines: queue.Queue = queue.Queue()
        self._server_lines: queue.Queue = queue.Queue()
        self._runtime_ready = threading.Event()
        self._runtime_proc: subprocess.Popen | None = None
        self._server_proc: subprocess.Popen | None = None
        self._log_dir: Path | None = None
        self._exit_record: dict = {}

    # -- control -----------------------------------------------------------

    def request_stop(self, reason: str = "stop requested") -> None:
        """Trigger the graceful stop sequence (Ctrl+C handler lands here)."""

        if not self._stop_flag.is_set():
            self._echo(f"[supervisor] {reason}; stopping")
        self._stop_flag.set()

    def _stop_wanted(self) -> bool:
        return self._stop_flag.is_set() or stop_requested(self.options.run_dir)

    # -- entry point --------------------------------------------------------

    def run(self) -> int:
        options = self.options
        options.run_dir.mkdir(parents=True, exist_ok=True)
        self._log_dir = make_log_dir(options.run_dir, options.keep_log_starts)
        self._exit_record = {
            "startedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "logDir": str(self._log_dir),
            "runtimeExit": None,
            "serverExit": None,
            "unclean": False,
        }
        write_pid_file(options.run_dir / SUPERVISOR_PID, os.getpid())
        if self._stdin is not None:
            threading.Thread(target=self._stdin_reader, daemon=True, name="supervisor-stdin").start()
        try:
            return self._run()
        finally:
            self._record_exit()
            write_last_exit(options.run_dir, self._exit_record)
            for name in (SUPERVISOR_PID, RUNTIME_PID, SERVER_PID, STOP_REQUEST, CONSOLE_REQUEST):
                try:
                    (options.run_dir / name).unlink()
                except FileNotFoundError:
                    pass
                except OSError:
                    pass

    def _run(self) -> int:
        options = self.options
        self._echo(f"[supervisor] starting the runtime: {options.runtime_command[0]}")
        runtime = self._start_runtime()
        if runtime is None:
            self._echo("[supervisor] FAIL: the runtime could not be started")
            return 1
        if not self._await_ready(runtime, options.ready_timeout):
            if self._stop_wanted():
                return self._stop_sequence()
            self._echo(
                f"[supervisor] FAIL: the runtime did not report endbot_runtime_ready "
                f"within {options.ready_timeout:g}s"
            )
            self._record_exit()
            kill_process_tree(runtime)
            self._exit_record["runtimeExit"] = runtime.poll()
            return 1
        self._echo("[supervisor] runtime is ready")
        if self._stop_wanted():
            return self._stop_sequence()
        error = self._pre_server_check()
        if error is not None:
            self._echo(f"[supervisor] FAIL: refusing to start BDS: {error}")
            self._stop_runtime()
            return 1
        self._echo(f"[supervisor] starting BDS: {options.server_command[0]}")
        server = self._start_server()
        if server is None:
            self._echo("[supervisor] FAIL: BDS could not be started")
            self._stop_runtime()
            return 1
        return self._supervise()

    # -- main supervision loop ----------------------------------------------

    def _supervise(self) -> int:
        options = self.options
        failures: list[float] = []
        while True:
            self._pump_output()
            self._pump_console()
            if self._stop_wanted():
                return self._stop_sequence()
            server = self._server_proc
            if server is not None and server.poll() is not None:
                code = server.poll()
                self._exit_record["serverExit"] = code
                self._echo(f"[supervisor] BDS exited with code {code}; stopping the runtime")
                self._stop_runtime()
                self._echo("[supervisor] stopped (BDS exit code kept)")
                return code
            runtime = self._runtime_proc
            if runtime is not None and runtime.poll() is None:
                time.sleep(options.poll_interval)
                continue

            now = time.monotonic()
            if failures and now - failures[0] > options.restart_failure_window:
                failures = []
            failures.append(now)
            self._echo(f"[supervisor] the runtime is down (failure {len(failures)} of {options.restart_max_failures})")
            if len(failures) >= options.restart_max_failures:
                self._echo(
                    f"[supervisor] UNCLEAN: giving up after {len(failures)} runtime failures "
                    f"within {options.restart_failure_window:g}s"
                )
                self._exit_record["unclean"] = True
                self._stop_server()
                self._stop_runtime()
                self._echo("[supervisor] stopped UNCLEAN")
                return 1
            delay = min(options.restart_initial_backoff * (2 ** (len(failures) - 1)), options.restart_max_backoff)
            self._echo(f"[supervisor] restarting the runtime in {delay:g}s")
            if self._wait(delay):
                return self._stop_sequence()
            runtime = self._start_runtime()
            if runtime is not None and self._await_ready(runtime, options.ready_timeout):
                self._echo("[supervisor] runtime is back")
            else:
                if runtime is not None:
                    kill_process_tree(runtime)
                self._runtime_proc = None

    # -- children -----------------------------------------------------------

    def _start_runtime(self) -> subprocess.Popen | None:
        try:
            process = spawn_child(
                self.options.runtime_command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
        except OSError as error:
            self._echo(f"[supervisor] the runtime could not be spawned: {error}")
            return None
        self._runtime_proc = process
        self._runtime_ready.clear()
        write_pid_file(self.options.run_dir / RUNTIME_PID, process.pid)
        _OutputReader(process.stdout, self._log_dir / "runtime.log", self._runtime_lines, self._runtime_ready).start()
        return process

    def _start_server(self) -> subprocess.Popen | None:
        try:
            process = spawn_child(
                self.options.server_command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
        except OSError as error:
            self._echo(f"[supervisor] BDS could not be spawned: {error}")
            return None
        self._server_proc = process
        write_pid_file(self.options.run_dir / SERVER_PID, process.pid)
        _OutputReader(process.stdout, self._log_dir / "server.log", self._server_lines).start()
        return process

    def _await_ready(self, process: subprocess.Popen, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self._pump_output()
            if self._runtime_ready.is_set():
                return True
            if process.poll() is not None:
                self._echo(f"[supervisor] the runtime exited early with code {process.poll()}")
                self._exit_record["runtimeExit"] = process.poll()
                return False
            if self._stop_wanted():
                return False
            time.sleep(self.options.poll_interval)
        return self._runtime_ready.is_set()

    def _wait(self, seconds: float) -> bool:
        """Sleep up to ``seconds``; return True when a stop was requested meanwhile."""

        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self._pump_output()
            self._pump_console()
            if self._stop_wanted():
                return True
            time.sleep(min(self.options.poll_interval, max(0.0, deadline - time.monotonic())))
        return self._stop_wanted()

    def _wait_exit(self, process: subprocess.Popen, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self._pump_output()
            if process.poll() is not None:
                return True
            time.sleep(self.options.poll_interval)
        return process.poll() is not None

    # -- console I/O --------------------------------------------------------

    def _stdin_reader(self) -> None:
        try:
            for line in self._stdin:
                self._stdin_lines.put(line)
        except (OSError, ValueError):
            pass

    def _pump_console(self) -> None:
        for line in consume_console_lines(self.options.run_dir):
            self._send_console_line(line)
        while True:
            try:
                line = self._stdin_lines.get_nowait()
            except queue.Empty:
                return
            self._send_console_line(line.rstrip("\r\n"))

    def _send_console_line(self, line: str) -> bool:
        server = self._server_proc
        if server is None or server.poll() is not None or server.stdin is None:
            return False
        try:
            server.stdin.write(line.encode("utf-8") + b"\n")
            server.stdin.flush()
        except (OSError, ValueError):
            return False
        return True

    def _pump_output(self) -> None:
        self._drain(self._runtime_lines, "[runtime] ")
        self._drain(self._server_lines, "")

    def _drain(self, lines: queue.Queue, prefix: str) -> None:
        while True:
            try:
                line = lines.get_nowait()
            except queue.Empty:
                return
            self._echo(f"{prefix}{line}")

    # -- stop sequence ------------------------------------------------------

    def _stop_sequence(self) -> int:
        unclean = self._stop_server()
        unclean = self._stop_runtime() or unclean
        self._echo("[supervisor] stopped UNCLEAN" if unclean else "[supervisor] stopped cleanly")
        return 1 if unclean else 0

    def _stop_server(self) -> bool:
        """Send `stop` to the BDS console; escalate to a process-tree kill. True when unclean."""

        server = self._server_proc
        if server is None or server.poll() is not None:
            return False
        self._echo("[supervisor] stopping BDS ('stop' on the console)")
        self._send_console_line("stop")
        if self._wait_exit(server, self.options.stop_timeout):
            self._exit_record["serverExit"] = server.poll()
            return False
        self._echo(
            f"[supervisor] UNCLEAN: BDS did not exit within {self.options.stop_timeout:g}s; killing its process tree"
        )
        self._exit_record["unclean"] = True
        kill_process_tree(server)
        self._exit_record["serverExit"] = server.poll()
        return True

    def _stop_runtime(self) -> bool:
        """Ask the runtime for a graceful shutdown; escalate to a process-tree kill. True when unclean."""

        runtime = self._runtime_proc
        if runtime is None or runtime.poll() is not None:
            return False
        self._echo("[supervisor] stopping the runtime (graceful shutdown)")
        try:
            client = RuntimeControlClient(
                self.options.control_host,
                self.options.control_port,
                self.options.control_token_file,
                timeout=self.options.runtime_control_timeout,
            )
            client.shutdown()
        except RuntimeControlError as error:
            self._echo(f"[supervisor] runtime shutdown request failed: {error}")
        if self._wait_exit(runtime, self.options.runtime_stop_timeout):
            self._exit_record["runtimeExit"] = runtime.poll()
            return False
        self._echo(
            f"[supervisor] UNCLEAN: the runtime did not exit within "
            f"{self.options.runtime_stop_timeout:g}s; killing its process tree"
        )
        self._exit_record["unclean"] = True
        kill_process_tree(runtime)
        self._exit_record["runtimeExit"] = runtime.poll()
        return True

    def _record_exit(self) -> None:
        if self._runtime_proc is not None and self._runtime_proc.poll() is not None:
            self._exit_record["runtimeExit"] = self._runtime_proc.poll()
        if self._server_proc is not None and self._server_proc.poll() is not None:
            self._exit_record["serverExit"] = self._server_proc.poll()


__all__ = ["Supervisor", "SupervisorOptions"]
