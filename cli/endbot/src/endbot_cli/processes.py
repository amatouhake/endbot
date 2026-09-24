"""Toolchain resolution and child process-tree control (docs/OPERATIONS.md sections 1, 5a).

The Endbot executables come from the active application version (``app/current``
naming ``app/<version>/``). For development without an ``app/`` directory the
``ENDBOT_PYTHON``, ``ENDBOT_NODE``, and ``ENDBOT_RUNTIME_DIR`` environment
variables override each component (developer-only, see cli/endbot/README.md).

Children are spawned in their own process group / session so Ctrl+C on the
supervisor console reaches only the supervisor (Windows: CREATE_NEW_PROCESS_GROUP
disables CTRL+C for the child) and so a whole child process tree can be killed
on escalation.
"""

from __future__ import annotations

import os
import signal
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from endbot_cli.instance import InstancePaths

ENV_PYTHON = "ENDBOT_PYTHON"
ENV_NODE = "ENDBOT_NODE"
ENV_RUNTIME_DIR = "ENDBOT_RUNTIME_DIR"


class ToolchainError(RuntimeError):
    """An Endbot executable cannot be resolved; the message names both options."""


@dataclass(frozen=True, slots=True)
class Toolchain:
    python: Path
    node: Path
    runtime_dir: Path


def _resolve_component(
    environ: Mapping[str, str],
    env_var: str,
    app_candidate: Path | None,
    app_dir: Path | None,
    description: str,
) -> Path:
    override = environ.get(env_var)
    if override:
        candidate, source = Path(override), f"${env_var}"
    elif app_candidate is not None and app_dir is not None:
        candidate, source = app_candidate, f"app/current -> {app_dir.name}"
    else:
        raise ToolchainError(
            f"{description} cannot be resolved: there is no app/current and ${env_var} is not set; "
            f"run `endbot setup` or (for development) set ${env_var}"
        )
    if not candidate.exists():
        raise ToolchainError(
            f"{description} {candidate} ({source}) does not exist; "
            f"reinstall the application or fix ${env_var}"
        )
    return candidate


def _app_dir(paths: InstancePaths) -> Path | None:
    """Return ``app/<version>/`` named by ``app/current``, or None in development."""

    try:
        version = paths.app_current.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise ToolchainError(f"{paths.app_current} cannot be read: {error}; fix the file permissions") from error
    if not version:
        raise ToolchainError(f"{paths.app_current} is empty; it must name the active application version")
    return paths.root / "app" / version


def resolve_toolchain(
    paths: InstancePaths, environ: Mapping[str, str] | None = None, *, windows: bool | None = None
) -> Toolchain:
    """Resolve ``python``, ``node``, and ``runtime_dir`` per the module rule.

    ``windows`` selects the platform layout explicitly (tests exercise both
    layouts); it defaults to the current platform.
    """

    environ = os.environ if environ is None else environ
    on_windows = os.name == "nt" if windows is None else windows
    app_dir = _app_dir(paths)
    python_name = "python/python.exe" if on_windows else "python/bin/python3"
    node_name = "node/node.exe" if on_windows else "node/bin/node"
    python = _resolve_component(
        environ,
        ENV_PYTHON,
        app_dir / python_name if app_dir is not None else None,
        app_dir,
        "the Endbot Python interpreter",
    )
    node = _resolve_component(
        environ,
        ENV_NODE,
        app_dir / node_name if app_dir is not None else None,
        app_dir,
        "the Endbot Node.js interpreter",
    )
    runtime_dir = _resolve_component(
        environ,
        ENV_RUNTIME_DIR,
        app_dir / "runtime" if app_dir is not None else None,
        app_dir,
        "the Endbot runtime directory",
    )
    if not (runtime_dir / "src" / "cli.js").is_file():
        raise ToolchainError(f"{runtime_dir} does not look like the Endbot runtime (missing src/cli.js)")
    return Toolchain(python=python, node=node, runtime_dir=runtime_dir)


def spawn_child(command: Sequence[str], **kwargs) -> subprocess.Popen:
    """Spawn a supervised child in its own process group / session."""

    kwargs.setdefault("stdin", subprocess.DEVNULL)
    if os.name == "nt":
        kwargs.setdefault("creationflags", subprocess.CREATE_NEW_PROCESS_GROUP)
    else:
        kwargs.setdefault("start_new_session", True)
    return subprocess.Popen(list(command), **kwargs)


def process_alive(pid: int) -> bool:
    """True when a process with ``pid`` exists.

    On Windows ``os.kill(pid, 0)`` would terminate the process, so the Win32
    process handle API is used instead. PID reuse can report a recycled PID as
    alive; callers treat that as "supervisor running" and fail safe.
    """

    if pid is None or pid <= 0:
        return False
    if os.name == "nt":
        return _windows_process_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _windows_process_alive(pid: int) -> bool:
    import ctypes

    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.restype = ctypes.c_void_p
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ctypes.GetLastError() == 5  # ERROR_ACCESS_DENIED: exists but not ours
    try:
        code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return True
        return code.value == 259  # STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def kill_process_tree(process: subprocess.Popen) -> None:
    """Escalation: kill a child and everything it spawned (best effort)."""

    try:
        if process.poll() is None:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
            else:
                os.killpg(process.pid, signal.SIGKILL)  # children run via start_new_session
    except OSError:
        pass
    try:
        process.kill()
    except OSError:
        pass
    try:
        process.wait(timeout=5)
    except (subprocess.TimeoutExpired, OSError):
        pass
