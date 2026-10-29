"""Foreground supervisor for the Endbot runtime, endbotd and Endstone/BDS (docs/OPERATIONS.md section 5a).

``endbot start`` runs one supervisor in the foreground. It starts the Endbot
runtime (Node) and waits for its ``endbot_runtime_ready`` line, then endbotd
(docs/ENDBOTD.md P1) and waits for its ``endbotd_ready`` line, re-checks
``[local-bot-auth]``, then starts Endstone/BDS with its stdin on a pipe the
supervisor owns. From then on:

- lines typed on the supervisor's stdin and lines queued in
  ``state/run/console.request`` are forwarded to the BDS console;
- ``state/run/stop.request`` (from `endbot stop`, or Ctrl+C) triggers the stop
  sequence: `stop` to the BDS console (escalating to a process-tree kill after
  the timeout), then endbotd (its stdin is closed; it ends its runs, Group work
  and mining loops and exits), then a graceful runtime ``shutdown`` (each
  escalating the same way);
- if BDS exits on its own, endbotd and the runtime are stopped and the
  supervisor exits with BDS's exit code (no auto-restart);
- if the runtime or endbotd exits unexpectedly while BDS runs, it is restarted
  with exponential backoff (1 s, 2 s, 4 s ... capped at 30 s); after 5
  failures of one child inside a 5-minute window the supervisor stops BDS and
  exits non-zero. A replacement that does not become ready counts as another
  failure. While endbotd is down the plugin refuses Endbot commands.

Per-start logs land in ``state/run/logs/<start>/{runtime,endbotd,server}.log``
(the last 10 starts are kept); exit codes land in ``state/run/last-exit.json``.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from endbot_cli.control_client import RuntimeControlClient, RuntimeControlError
from endbot_cli.processes import kill_process_tree, spawn_child
from endbot_cli.runstate import (
    CONSOLE_REQUEST,
    ENDBOTD_PID,
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
    endbotd_command: tuple[str, ...]
    control_token_file: Path
    control_host: str = "127.0.0.1"
    control_port: int = 19142
    ready_timeout: float = 30.0
    stop_timeout: float = 60.0
    runtime_stop_timeout: float = 15.0
    # Above endbotd's own cleanup bound (10 s), so a clean stop is never killed.
    endbotd_stop_timeout: float = 20.0
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
        ready_name: str | None = None,
    ) -> None:
        super().__init__(daemon=True, name=f"output-{log_path.name}")
        self.stream = stream
        self.lines = lines
        self.ready_event = ready_event
        self.ready_name = ready_name
        self.log_path = log_path
        self.log = None

    def run(self) -> None:
        # Append: a restarted child keeps writing the same per-start log.
        with open(self.log_path, "a", encoding="utf-8", newline="\n") as self.log:
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
                        if isinstance(parsed, dict) and parsed.get("event") == self.ready_name:
                            self.ready_event.set()
                    self.lines.put(line)
            except (OSError, ValueError):
                pass


@dataclass
class _Child:
    """A restartable child: the runtime or endbotd."""

    name: str
    label: str
    ready_name: str
    pid_file: str
    exit_key: str
    lines: queue.Queue = field(default_factory=queue.Queue)
    ready: threading.Event = field(default_factory=threading.Event)
    process: subprocess.Popen | None = None
    failures: list[float] = field(default_factory=list)


def _default_echo(line: str) -> None:
    print(line, flush=True)


class Supervisor:
    """Runs the three children until a stop condition; ``run()`` returns the exit code."""

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
        self._server_lines: queue.Queue = queue.Queue()
        self._runtime = _Child("runtime", "the runtime", "endbot_runtime_ready", RUNTIME_PID, "runtimeExit")
        self._endbotd = _Child("endbotd", "endbotd", "endbotd_ready", ENDBOTD_PID, "endbotdExit")
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
            "endbotdExit": None,
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
            for name in (SUPERVISOR_PID, RUNTIME_PID, ENDBOTD_PID, SERVER_PID, STOP_REQUEST, CONSOLE_REQUEST):
                try:
                    (options.run_dir / name).unlink()
                except FileNotFoundError:
                    pass
                except OSError:
                    pass

    def _run(self) -> int:
        options = self.options
        for child, command in ((self._runtime, options.runtime_command), (self._endbotd, options.endbotd_command)):
            self._echo(f"[supervisor] starting {child.label}: {command[0]}")
            process = self._start(child, command)
            if process is None:
                self._echo(f"[supervisor] FAIL: {child.label} could not be started")
                self._stop_children()
                return 1
            if not self._await_ready(child, process, options.ready_timeout):
                if self._stop_wanted():
                    return self._stop_sequence()
                self._echo(
                    f"[supervisor] FAIL: {child.label} did not report {child.ready_name} "
                    f"within {options.ready_timeout:g}s"
                )
                kill_process_tree(process)
                self._record_exit()
                self._stop_children()
                return 1
            self._echo(f"[supervisor] {child.label} is ready")
            if self._stop_wanted():
                return self._stop_sequence()
        error = self._pre_server_check()
        if error is not None:
            self._echo(f"[supervisor] FAIL: refusing to start BDS: {error}")
            self._stop_children()
            return 1
        self._echo(f"[supervisor] starting BDS: {options.server_command[0]}")
        server = self._start_server()
        if server is None:
            self._echo("[supervisor] FAIL: BDS could not be started")
            self._stop_children()
            return 1
        return self._supervise()

    # -- main supervision loop ----------------------------------------------

    def _supervise(self) -> int:
        options = self.options
        while True:
            self._pump_output()
            self._pump_console()
            if self._stop_wanted():
                return self._stop_sequence()
            server = self._server_proc
            if server is not None and server.poll() is not None:
                code = server.poll()
                self._exit_record["serverExit"] = code
                self._echo(f"[supervisor] BDS exited with code {code}; stopping endbotd and the runtime")
                self._stop_children()
                self._echo("[supervisor] stopped (BDS exit code kept)")
                return code
            down = next((child for child in (self._runtime, self._endbotd)
                         if child.process is None or child.process.poll() is not None), None)
            if down is None:
                time.sleep(options.poll_interval)
                continue
            outcome = self._restart(down)
            if outcome is not None:
                return outcome

    def _restart(self, child: _Child) -> int | None:
        """Restart one child after a failure; a return value ends supervision."""

        options = self.options
        if child.process is not None:
            self._exit_record[child.exit_key] = child.process.poll()
        now = time.monotonic()
        if child.failures and now - child.failures[0] > options.restart_failure_window:
            child.failures.clear()
        child.failures.append(now)
        self._echo(f"[supervisor] {child.label} is down (failure {len(child.failures)} of "
                   f"{options.restart_max_failures})")
        if len(child.failures) >= options.restart_max_failures:
            self._echo(
                f"[supervisor] UNCLEAN: giving up after {len(child.failures)} {child.name} failures "
                f"within {options.restart_failure_window:g}s"
            )
            self._exit_record["unclean"] = True
            self._stop_server()
            self._stop_children()
            self._echo("[supervisor] stopped UNCLEAN")
            return 1
        delay = min(options.restart_initial_backoff * (2 ** (len(child.failures) - 1)), options.restart_max_backoff)
        self._echo(f"[supervisor] restarting {child.label} in {delay:g}s")
        if self._wait(delay):
            return self._stop_sequence()
        command = options.runtime_command if child is self._runtime else options.endbotd_command
        process = self._start(child, command)
        if process is not None and self._await_ready(child, process, options.ready_timeout):
            self._echo(f"[supervisor] {child.label} is back")
        elif process is not None and process.poll() is None:
            # Not ready in time: kill it but keep the handle, so the next pass counts another failure.
            kill_process_tree(process)
        return None

    # -- children -----------------------------------------------------------

    def _start(self, child: _Child, command: tuple[str, ...]) -> subprocess.Popen | None:
        try:
            # endbotd stops when its stdin closes, so it gets a pipe; the runtime keeps DEVNULL.
            stdin = subprocess.PIPE if child is self._endbotd else subprocess.DEVNULL
            process = spawn_child(command, stdin=stdin, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        except OSError as error:
            self._echo(f"[supervisor] {child.label} could not be spawned: {error}")
            child.process = None
            return None
        child.process = process
        child.ready.clear()
        write_pid_file(self.options.run_dir / child.pid_file, process.pid)
        _OutputReader(process.stdout, self._log_dir / f"{child.name}.log", child.lines, child.ready,
                      child.ready_name).start()
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

    def _await_ready(self, child: _Child, process: subprocess.Popen, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self._pump_output()
            if child.ready.is_set():
                return True
            if process.poll() is not None:
                self._echo(f"[supervisor] {child.label} exited early with code {process.poll()}")
                self._exit_record[child.exit_key] = process.poll()
                return False
            if self._stop_wanted():
                return False
            time.sleep(self.options.poll_interval)
        return child.ready.is_set()

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
        self._drain(self._runtime.lines, "[runtime] ")
        self._drain(self._endbotd.lines, "[endbotd] ")
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
        unclean = self._stop_children() or unclean
        self._echo("[supervisor] stopped UNCLEAN" if unclean else "[supervisor] stopped cleanly")
        return 1 if unclean else 0

    def _stop_children(self) -> bool:
        """endbotd first (its cleanup still reaches the runtime), then the runtime. True when unclean."""

        unclean = self._stop_endbotd()
        return self._stop_runtime() or unclean

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

    def _stop_endbotd(self) -> bool:
        """Close endbotd's stdin, its stop signal; escalate to a process-tree kill. True when unclean."""

        process = self._endbotd.process
        if process is None or process.poll() is not None:
            return False
        self._echo("[supervisor] stopping endbotd")
        try:
            if process.stdin is not None:
                process.stdin.close()
        except OSError:
            pass
        return self._await_stop(self._endbotd, process, self.options.endbotd_stop_timeout)

    def _stop_runtime(self) -> bool:
        """Ask the runtime for a graceful shutdown; escalate to a process-tree kill. True when unclean."""

        runtime = self._runtime.process
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
        return self._await_stop(self._runtime, runtime, self.options.runtime_stop_timeout)

    def _await_stop(self, child: _Child, process: subprocess.Popen, timeout: float) -> bool:
        if self._wait_exit(process, timeout):
            self._exit_record[child.exit_key] = process.poll()
            return False
        self._echo(f"[supervisor] UNCLEAN: {child.label} did not exit within {timeout:g}s; killing its process tree")
        self._exit_record["unclean"] = True
        kill_process_tree(process)
        self._exit_record[child.exit_key] = process.poll()
        return True

    def _record_exit(self) -> None:
        for child in (self._runtime, self._endbotd):
            if child.process is not None and child.process.poll() is not None:
                self._exit_record[child.exit_key] = child.process.poll()
        if self._server_proc is not None and self._server_proc.poll() is not None:
            self._exit_record["serverExit"] = self._server_proc.poll()


__all__ = ["Supervisor", "SupervisorOptions"]
