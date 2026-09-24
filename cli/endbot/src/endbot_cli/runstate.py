"""Supervisor run-state files under ``state/run/`` (docs/OPERATIONS.md section 5a).

File protocol shared by ``endbot start`` (the foreground supervisor) and the
other-terminal commands::

    state/run/supervisor.pid    PID of the live supervisor (written last, removed on exit)
    state/run/runtime.pid       PID of the Endbot runtime child (informational)
    state/run/server.pid        PID of the Endstone/BDS child (informational)
    state/run/stop.request      created by `endbot stop`; consumed by the supervisor
    state/run/console.request   appended to by `endbot console`, one line per
                                request; the supervisor forwards and truncates it
    state/run/last-exit.json    exit record of the most recent supervisor run
    state/run/logs/<start>/     per-start runtime.log / server.log (last 10 kept)

A ``supervisor.pid`` naming a process that no longer exists is stale: every
command reports it and removes it instead of acting on it.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from endbot_cli.fsutil import atomic_write_text
from endbot_cli.processes import process_alive

SUPERVISOR_PID = "supervisor.pid"
RUNTIME_PID = "runtime.pid"
SERVER_PID = "server.pid"
STOP_REQUEST = "stop.request"
CONSOLE_REQUEST = "console.request"
LAST_EXIT = "last-exit.json"
LOGS_DIR = "logs"
KEEP_LOG_STARTS = 10

RUNNING = "running"
STALE = "stale"
NONE = "none"


@dataclass(frozen=True, slots=True)
class SupervisorState:
    status: str
    pid: int | None = None


def read_pid_file(path: Path) -> int | None:
    """Return the PID in ``path``, or None when missing, empty, or not a number."""

    try:
        text = path.read_text(encoding="utf-8").strip()
    except (FileNotFoundError, OSError, UnicodeDecodeError):
        return None
    if not text.isdecimal():
        return None
    pid = int(text)
    return pid if pid > 0 else None


def inspect_supervisor(run_dir: Path) -> SupervisorState:
    """Classify ``state/run/supervisor.pid`` as running, stale, or absent."""

    pid = read_pid_file(Path(run_dir) / SUPERVISOR_PID)
    if pid is None:
        return SupervisorState(NONE)
    if process_alive(pid):
        return SupervisorState(RUNNING, pid)
    return SupervisorState(STALE, pid)


def clean_supervisor_pid(run_dir: Path) -> None:
    try:
        (Path(run_dir) / SUPERVISOR_PID).unlink()
    except FileNotFoundError:
        pass


def request_stop(run_dir: Path) -> Path:
    """Drop a stop request for the supervisor to consume."""

    path = Path(run_dir) / STOP_REQUEST
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    return path


def stop_requested(run_dir: Path) -> bool:
    return (Path(run_dir) / STOP_REQUEST).exists()


def append_console_line(run_dir: Path, line: str) -> Path:
    """Queue one BDS console line (the supervisor consumes and truncates the file)."""

    if "\n" in line or "\r" in line:
        raise ValueError("console requests must be a single line")
    path = Path(run_dir) / CONSOLE_REQUEST
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(line + "\n")
    return path


def consume_console_lines(run_dir: Path) -> list[str]:
    """Read and truncate ``console.request``; return the queued lines.

    A line appended in the tiny window between the read and the truncate can be
    lost; callers on one host with short lines never hit it in practice, and the
    protocol avoids cross-process file locking (docs/OPERATIONS.md section 5a).
    """

    path = Path(run_dir) / CONSOLE_REQUEST
    try:
        with open(path, "r+", encoding="utf-8", newline="") as handle:
            data = handle.read()
            handle.truncate(0)
    except FileNotFoundError:
        return []
    except OSError:
        return []
    return [line for line in data.splitlines() if line.strip()]


def write_pid_file(path: Path, pid: int) -> None:
    atomic_write_text(Path(path), f"{pid}\n")


def write_last_exit(run_dir: Path, record: dict) -> None:
    atomic_write_text(Path(run_dir) / LAST_EXIT, json.dumps(record, indent=2) + "\n")


def read_last_exit(run_dir: Path) -> dict | None:
    """Return the most recent supervisor exit record, or None if absent or unreadable."""

    try:
        record = json.loads((Path(run_dir) / LAST_EXIT).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) else None


def make_log_dir(run_dir: Path, keep: int = KEEP_LOG_STARTS) -> Path:
    """Create a fresh per-start log directory and prune all but the last ``keep``."""

    logs_root = Path(run_dir) / LOGS_DIR
    logs_root.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime()) + f"-{os.getpid()}"
    log_dir = logs_root / stamp
    log_dir.mkdir(exist_ok=True)
    starts = sorted(entry for entry in logs_root.iterdir() if entry.is_dir())
    for old in starts[: max(0, len(starts) - keep)]:
        shutil.rmtree(old, ignore_errors=True)
    return log_dir
