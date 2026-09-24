"""Instance lifecycle commands: ``start``, ``stop``, ``console``, ``controllers reset``.

``endbot start`` implements docs/OPERATIONS.md section 5a: regenerate the
derived configs (section 2), run the fail-fast doctor checks (section 7),
refuse on any FAIL or on a ``<server>/version.txt`` mismatch with the lock,
start the runtime, wait for ``endbot_runtime_ready``, re-check
``[local-bot-auth]`` (the owner keys are created by the runtime on the very
first start, after configs are generated), then supervise runtime + BDS in the
foreground until a stop condition.

``endbot stop`` and ``endbot console`` are other-terminal commands over the
``state/run/`` request files (see runstate.py); both refuse clearly when no
live supervisor exists and clean up stale PID files.
"""

from __future__ import annotations

import signal
import sys
import time
from collections.abc import Callable, Iterable, Mapping, Sequence

from endbot_cli.config import ConfigError, load_config
from endbot_cli.configgen import ConfigGenerationError, generate_all
from endbot_cli.controllers import ControllersError, load_controllers, remove_binding, save_controllers
from endbot_cli.doctor import FAIL, DoctorContext, check_local_bot_auth, run_doctor
from endbot_cli.instance import InstancePaths
from endbot_cli.netcheck import lan_discovery_port_problem
from endbot_cli.processes import ToolchainError, process_alive, resolve_toolchain
from endbot_cli.runstate import (
    RUNNING,
    STALE,
    append_console_line,
    clean_supervisor_pid,
    inspect_supervisor,
    read_last_exit,
    request_stop,
)
from endbot_cli.supervisor import Supervisor, SupervisorOptions

_STDIN_DEFAULT = object()


def first_start_tolerable(name: str, paths: InstancePaths) -> bool:
    """True for doctor FAILs that the very first start legitimately produces.

    The runtime creates the control token and the owner key pair after config
    generation, so a first start may not have them yet; the strict
    ``[local-bot-auth]`` re-check runs again right before BDS starts.
    """

    if name == "control-token" and not paths.control_token.exists():
        return True
    return name == "local-bot-auth" and not paths.owner_public_key.exists()


def _print(message: str) -> None:
    print(message, flush=True)


def _fail(message: str) -> int:
    print(message, file=sys.stderr, flush=True)
    return 1


def run_start(
    paths: InstancePaths,
    *,
    environ: Mapping[str, str] | None = None,
    stdin: Iterable[str] | None | object = _STDIN_DEFAULT,
    echo: Callable[[str], None] | None = None,
    install_signal_handlers: bool = True,
    runtime_command: Sequence[str] | None = None,
    server_command: Sequence[str] | None = None,
    installed_endstone_version: Callable[[], str | None] | None = None,
    lan_port_check: Callable[[], str | None] | None = None,
    ready_timeout: float = 30.0,
    stop_timeout: float = 60.0,
    runtime_stop_timeout: float = 15.0,
    poll_interval: float = 0.2,
    restart_initial_backoff: float = 1.0,
    restart_max_backoff: float = 30.0,
    restart_max_failures: int = 5,
    restart_failure_window: float = 300.0,
) -> int:
    """Run the section 5a start sequence and supervise in the foreground.

    The ``*_command`` and timing parameters are internal test hooks; production
    callers get the real child commands and the documented timeouts.
    """

    try:
        config = load_config(paths.endbot_toml)
    except ConfigError as error:
        return _fail(f"FAIL start: {error}")

    state = inspect_supervisor(paths.state_run)
    if state.status == RUNNING:
        return _fail(f"FAIL start: a supervisor (pid {state.pid}) is already running; run `endbot stop` first")
    if state.status == STALE:
        _print(f"WARN start: stale {paths.state_run / 'supervisor.pid'} (pid {state.pid} is not running); removed")
        clean_supervisor_pid(paths.state_run)

    server = config.server.resolve(paths.root)
    try:
        generated = generate_all(config, paths, server)
    except ConfigGenerationError as error:
        return _fail(f"FAIL start: {error}")
    for message in generated.messages:
        _print(message)

    context = DoctorContext(paths=paths, live=False, installed_endstone_version=installed_endstone_version)
    results = run_doctor(context)
    fatal = False
    for result in results:
        if result.status == FAIL and first_start_tolerable(result.name, paths):
            _print(f"WARN {result.name}: {result.message} (first start: the runtime creates this during start)")
            continue
        _print(result.line())
        if result.status == FAIL:
            fatal = True
    if fatal:
        return _fail("FAIL start: refusing to start (fix the FAIL lines above)")

    # Fake children in tests bring no BDS; production always checks the port.
    if lan_port_check is None and server_command is None:
        lan_port_check = lan_discovery_port_problem
    problem = lan_port_check() if lan_port_check is not None else None
    if problem is not None:
        return _fail(f"FAIL start: {problem}")

    try:
        toolchain = resolve_toolchain(paths, environ)
    except ToolchainError as error:
        if runtime_command is None or server_command is None:
            return _fail(f"FAIL start: {error}")
        toolchain = None
    if runtime_command is None:
        runtime_command = (
            str(toolchain.node),
            str(toolchain.runtime_dir / "src" / "cli.js"),
            "--config",
            str(generated.runtime_config),
        )
    if server_command is None:
        server_command = (
            str(toolchain.python),
            "-m",
            "endbot_cli.endstone_entry",
            "-s",
            str(server),
            "--no-interactive",
        )

    def recheck_local_bot_auth() -> str | None:
        result = check_local_bot_auth(server, paths)
        return None if result.status != FAIL else f"{result.name}: {result.message}"

    options = SupervisorOptions(
        run_dir=paths.state_run,
        runtime_command=tuple(runtime_command),
        server_command=tuple(server_command),
        control_token_file=paths.control_token,
        control_host="127.0.0.1",
        control_port=config.runtime.control_port,
        ready_timeout=ready_timeout,
        stop_timeout=stop_timeout,
        runtime_stop_timeout=runtime_stop_timeout,
        poll_interval=poll_interval,
        restart_initial_backoff=restart_initial_backoff,
        restart_max_backoff=restart_max_backoff,
        restart_max_failures=restart_max_failures,
        restart_failure_window=restart_failure_window,
    )
    stdin_stream = sys.stdin if stdin is _STDIN_DEFAULT else stdin
    supervisor = Supervisor(options, stdin=stdin_stream, echo=echo, pre_server_check=recheck_local_bot_auth)
    if install_signal_handlers:
        _install_signal_handlers(supervisor)
    return supervisor.run()


def _install_signal_handlers(supervisor: Supervisor) -> None:
    def handler(signum, _frame) -> None:
        supervisor.request_stop(f"interrupt (signal {signum}) received")

    try:
        signal.signal(signal.SIGINT, handler)
        signal.signal(signal.SIGTERM, handler)
    except (OSError, ValueError):  # not the main thread, or unsupported signal
        pass


def _report_stopped(paths: InstancePaths, previous: dict | None) -> int:
    """Report the supervisor's own exit record; an UNCLEAN stop is a failure (section 5a)."""

    record = read_last_exit(paths.state_run)
    if record is None or record == previous:
        _print("stop: supervisor stopped (no new exit record)")
        return 0
    if record.get("unclean"):
        return _fail(
            f"FAIL stop: supervisor stopped UNCLEAN (runtime exit {record.get('runtimeExit')}, "
            f"server exit {record.get('serverExit')}); see {record.get('logDir')}"
        )
    _print("stop: supervisor stopped cleanly")
    return 0


def run_stop(paths: InstancePaths, *, timeout: float = 120.0, poll_interval: float = 0.25) -> int:
    """Request a stop and wait for the supervisor to exit (section 5a)."""

    state = inspect_supervisor(paths.state_run)
    if state.status == STALE:
        clean_supervisor_pid(paths.state_run)
        return _fail(
            f"FAIL stop: stale {paths.state_run / 'supervisor.pid'} (pid {state.pid} is not running); "
            "removed it, nothing is running"
        )
    if state.status != RUNNING:
        return _fail(f"FAIL stop: no supervisor is running ({paths.state_run / 'supervisor.pid'} is missing)")
    previous = read_last_exit(paths.state_run)
    request_stop(paths.state_run)
    _print(f"stop: requested; waiting up to {timeout:g}s for supervisor pid {state.pid} to exit")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not process_alive(state.pid):
            return _report_stopped(paths, previous)
        time.sleep(poll_interval)
    if not process_alive(state.pid):
        return _report_stopped(paths, previous)
    return _fail(
        f"FAIL stop: supervisor pid {state.pid} is still running after {timeout:g}s; "
        f"inspect {paths.state_run / 'last-exit.json'} and {paths.state_run / 'logs'}"
    )


def run_console(paths: InstancePaths, line: str) -> int:
    """Queue one BDS console line for the supervisor (section 5a)."""

    if "\n" in line or "\r" in line:
        return _fail("FAIL console: the request must be a single line")
    state = inspect_supervisor(paths.state_run)
    if state.status == STALE:
        clean_supervisor_pid(paths.state_run)
        return _fail(
            f"FAIL console: stale {paths.state_run / 'supervisor.pid'} (pid {state.pid} is not running); "
            "removed it, nothing is running"
        )
    if state.status != RUNNING:
        return _fail(f"FAIL console: no supervisor is running ({paths.state_run / 'supervisor.pid'} is missing)")
    append_console_line(paths.state_run, line)
    _print(f"console: queued for the BDS console: {line}")
    return 0


def run_controllers_reset(paths: InstancePaths, gamertag: str) -> int:
    """Return one controller binding to pending (section 3)."""

    state = inspect_supervisor(paths.state_run)
    if state.status == RUNNING:
        return _fail(
            f"FAIL controllers reset: a supervisor (pid {state.pid}) is running; "
            "run `endbot stop` first (the plugin rewrites controllers.json while running)"
        )
    if state.status == STALE:
        _print(f"WARN controllers reset: stale {paths.state_run / 'supervisor.pid'} (pid {state.pid}); removed")
        clean_supervisor_pid(paths.state_run)
    try:
        controllers = load_controllers(paths.state_controllers)
    except ControllersError as error:
        return _fail(f"FAIL controllers reset: {error}")
    if controllers.find(gamertag) is None:
        _print(f"controllers reset: {gamertag} has no binding; it is pending (or not configured)")
        return 0
    save_controllers(paths.state_controllers, remove_binding(controllers, gamertag))
    _print(f"controllers reset: removed the binding for {gamertag}; it is pending again and re-binds on the next join")
    return 0
