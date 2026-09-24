"""``endbot setup`` — plan and apply a fresh or adopted instance (section 6).

The command is always dry-run first: it computes the full plan (every file to
create or modify with a short reason, every backup to take, and whether BDS
will be downloaded or overwritten through Endstone's acquisition path) and
prints it; without ``--apply`` nothing is ever changed. ``--apply`` is the
confirmation — the CLI stays scriptable and never prompts.

Safety rules (AGENTS.md): BDS is only acquired through Endstone's own bootstrap
(:mod:`endbot_cli.bds`, never started), ``online-mode=true`` and
``allow-cheats=false`` are preserved, world history is never changed, and an
existing server's ``server.properties`` is checked, never silently flipped.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from endbot_cli.backup import EXECUTABLE_NAMES, BackupEntry, collect_backup_entries, create_backup, describe_entries
from endbot_cli.bds import BdsError, run_install
from endbot_cli.commands import first_start_tolerable
from endbot_cli.config import DEFAULT_CONTROL_PORT
from endbot_cli.doctor import FAIL, WARN, DoctorContext, check_world, run_doctor
from endbot_cli.fsutil import atomic_write_text
from endbot_cli.instance import InstancePaths
from endbot_cli.lock import LockData, LockError, compare_bds_versions, load_lock
from endbot_cli.processes import ToolchainError, resolve_python
from endbot_cli.properties import PreflightError, edit_properties, parse_properties, verify_server_properties
from endbot_cli.runstate import RUNNING, STALE, clean_supervisor_pid, inspect_supervisor

BDS_DOWNLOAD = "download"
BDS_UPDATE = "update"
BDS_NONE = "none"

STATE_SUBDIRS = ("secrets", "profiles", "generated", "run")
SAFETY_UPDATES = {"online-mode": "true", "allow-cheats": "false"}


def _print(message: str) -> None:
    print(message, flush=True)


def _fail(message: str) -> int:
    print(message, file=sys.stderr, flush=True)
    return 1


@dataclass(frozen=True, slots=True)
class BdsPlan:
    """What the Endstone acquisition step will do to the server directory."""

    action: str
    lines: tuple[str, ...] = ()
    failure: str | None = None


@dataclass(frozen=True, slots=True)
class SetupPlan:
    mode: str  # "fresh" | "existing"
    root: Path
    server: Path
    server_path_value: str  # the [server].path value to write
    gamertags: tuple[str, ...]
    control_port: int
    bds: BdsPlan
    backup_entries: tuple[BackupEntry, ...]
    backup_worlds: bool
    lines: tuple[str, ...]
    warnings: tuple[str, ...]
    failures: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.failures


def plan_bds(server: Path, lock: LockData) -> BdsPlan:
    """Classify the server directory the way Endstone's ``_install()`` will.

    ``version.txt`` decides: matching the lock means no BDS change, older (or
    missing) means Endstone overwrites the executable and the vanilla packs it
    ships, newer is refused. A missing server executable always downloads.
    """

    version_file = server / "version.txt"
    executable_present = any((server / name).is_file() for name in EXECUTABLE_NAMES)
    try:
        found = version_file.read_text(encoding="utf-8-sig").strip()
    except FileNotFoundError:
        found = ""
    except OSError as error:
        return BdsPlan(BDS_NONE, (), f"{version_file} cannot be read: {error}; fix the file permissions")
    if found:
        try:
            order = compare_bds_versions(found, lock.bds_version)
        except LockError as error:
            return BdsPlan(
                BDS_NONE, (), f"{version_file} {error}; restore it to the installed BDS version before setup"
            )
        if order > 0:
            return BdsPlan(
                BDS_NONE,
                (),
                f"{version_file} says {found!r}, which is NEWER than locked {lock.bds_version!r}; "
                "Endbot refuses to adopt a newer BDS (install a newer Endbot or restore the locked server)",
            )
        if order == 0 and executable_present:
            return BdsPlan(
                BDS_NONE,
                (f"no BDS change ({version_file} says {found!r}, matching locked {lock.bds_version!r})",),
            )
        if order == 0:
            detail = f"{version_file} matches the lock but the server executable is missing"
        else:
            detail = f"{version_file} says {found!r}, older than locked {lock.bds_version!r}"
    elif executable_present:
        detail = "version.txt is missing; Endstone treats the server as older than supported"
    else:
        detail = "new or incomplete server directory"
    overwrite = (
        "Endstone overwrites the server executable and the vanilla behavior_packs/, resource_packs/, "
        "definitions/ entries it ships; worlds, server.properties values, allowlist.json, and permissions.json are kept"
    )
    if executable_present:
        return BdsPlan(
            BDS_UPDATE,
            (f"update BDS to locked {lock.bds_version!r} through Endstone's acquisition path: {detail}; {overwrite}",),
        )
    message = (
        f"download BDS {lock.bds_version!r} into {server} through Endstone's acquisition path "
        f"(no server executable found; {detail})"
    )
    return BdsPlan(BDS_DOWNLOAD, (message,))


def compute_setup_plan(
    paths: InstancePaths,
    *,
    fresh: bool,
    existing: Path | None,
    gamertags: Sequence[str] = (),
    backup_worlds: bool = False,
    control_port: int = DEFAULT_CONTROL_PORT,
    lock: LockData | None = None,
) -> SetupPlan:
    """Compute the section 6 plan without changing anything."""

    lock = lock or load_lock()
    mode = "fresh" if fresh else "existing"
    root = paths.root
    warnings: list[str] = []
    failures: list[str] = []
    lines: list[str] = []

    if fresh:
        server = root / "server"
        server_path_value = "server"
        if server.exists() and not server.is_dir():
            failures.append(f"{server} exists but is not a directory; remove it or use --existing")
    else:
        server = Path(existing).absolute()
        server_path_value = str(server)
        if not server.is_dir():
            failures.append(f"{server} (--existing) does not exist or is not a directory")

    tags_display = "[" + ", ".join(f'"{tag}"' for tag in gamertags) + "]"
    lines.append(
        f"create endbot.toml with [server] path = '{server_path_value}', "
        f"gamertags = {tags_display}, control-port = {control_port} (the only file you edit, section 2)"
    )
    lines.append(f"create state/{', state/'.join(STATE_SUBDIRS)}/ (persistent Endbot state, section 1)")

    bds = plan_bds(server, lock) if server.is_dir() or fresh else BdsPlan(BDS_NONE)
    lines.extend(bds.lines)
    if bds.failure:
        failures.append(bds.failure)

    properties_path = server / "server.properties"
    properties: dict[str, str] | None = None
    if fresh:
        if properties_path.is_file():
            lines.append(
                f"ensure {properties_path} sets online-mode=true and allow-cheats=false "
                "(only those keys are edited; comments and other keys are preserved)"
            )
        else:
            lines.append(
                f"create {properties_path} with online-mode=true and allow-cheats=false after the BDS download"
            )
        if (server / "worlds").is_dir():
            warnings.append(
                f"{server / 'worlds'} already exists; --fresh assumes a new server (use --existing to adopt)"
            )
    else:
        if not server.is_dir():
            pass  # already failed above
        else:
            try:
                properties = parse_properties(properties_path)
                verify_server_properties(properties_path)
            except FileNotFoundError:
                failures.append(
                    f"{properties_path} is missing; restore it before adopting this server "
                    "(Endbot never writes an existing server's server.properties)"
                )
            except PreflightError as error:
                failures.append(
                    f"{properties_path}: {error}; set online-mode=true and allow-cheats=false yourself first "
                    "(Endbot never silently changes an existing server's settings)"
                )
            except OSError as error:
                failures.append(f"{properties_path} cannot be read: {error}; fix the file permissions")
            else:
                lines.append(f"keep {properties_path} unchanged (verified online-mode=true, allow-cheats=false)")
            world = check_world(server, properties)
            if world.status == FAIL:
                failures.append(f"world check: {world.message}")
            elif world.status == WARN:
                warnings.append(f"world check: {world.message}")

    lines.append(
        f"generate [local-bot-auth] in {server / 'endstone.toml'} and {server / 'plugins' / 'endbot' / 'config.toml'} "
        "on the first `endbot start` (derived files, section 2)"
    )

    backup_entries: tuple[BackupEntry, ...] = ()
    if not fresh:
        backup_entries = collect_backup_entries(server, worlds=backup_worlds)
        if backup_entries:
            lines.append(
                f"back up {describe_entries(backup_entries)} into backups/<UTC timestamp>/ "
                "with a manifest.json listing every file's SHA-256"
            )
        else:
            warnings.append(f"nothing to back up under {server} (no operator-owned BDS files found)")
        if backup_worlds:
            lines.append("back up worlds/ as well (--backup-worlds)")
        else:
            lines.append("worlds/ is NOT copied; confirm a world backup with --i-have-a-world-backup to apply")

    if not gamertags:
        warnings.append("no --controller GamerTags given; nobody can control Bots until configured (section 3)")

    return SetupPlan(
        mode=mode,
        root=root,
        server=server,
        server_path_value=server_path_value,
        gamertags=tuple(gamertags),
        control_port=control_port,
        bds=bds,
        backup_entries=backup_entries,
        backup_worlds=backup_worlds,
        lines=tuple(lines),
        warnings=tuple(warnings),
        failures=tuple(failures),
    )


def _toml_string(value: str):
    import tomlkit

    if "'" in value:  # TOML literal strings cannot contain single quotes
        return tomlkit.string(value)
    return tomlkit.string(value, literal=True)


def write_endbot_toml(paths: InstancePaths, plan: SetupPlan) -> Path:
    """Write ``endbot.toml`` (section 2) with literal-string paths."""

    import tomlkit

    document = tomlkit.document()
    server = tomlkit.table()
    server["path"] = _toml_string(plan.server_path_value)
    document.add("server", server)
    controllers = tomlkit.table()
    controllers["gamertags"] = list(plan.gamertags)
    document.add("controllers", controllers)
    runtime = tomlkit.table()
    runtime["control-port"] = plan.control_port
    document.add("runtime", runtime)
    atomic_write_text(paths.endbot_toml, tomlkit.dumps(document))
    return paths.endbot_toml


def create_state_dirs(paths: InstancePaths) -> None:
    for name in STATE_SUBDIRS:
        (paths.root / "state" / name).mkdir(parents=True, exist_ok=True)


def report_doctor(paths: InstancePaths, installed_endstone_version: Callable[[], str | None] | None) -> bool:
    """Run the non-live doctor and print it; return True when anything still FAILs.

    FAILs the very first start legitimately produces (the runtime generates the
    owner keys and the control token during ``endbot start``) are reported as
    WARN with that explanation instead (section 6).
    """

    results = run_doctor(DoctorContext(paths=paths, live=False, installed_endstone_version=installed_endstone_version))
    fatal = False
    for result in results:
        if result.status == FAIL and first_start_tolerable(result.name, paths):
            print(
                f"WARN {result.name}: {result.message} "
                "(expected now: the first `endbot start` generates the owner keys and control token)"
            )
            continue
        print(result.line())
        if result.status == FAIL:
            fatal = True
    return fatal


def run_setup(
    paths: InstancePaths,
    *,
    fresh: bool,
    existing: Path | None,
    gamertags: Sequence[str] = (),
    apply: bool = False,
    backup_worlds: bool = False,
    have_world_backup: bool = False,
    control_port: int = DEFAULT_CONTROL_PORT,
    windows: bool | None = None,
    environ: Mapping[str, str] | None = None,
    acquire_bds: Callable[[Path], None] | None = None,
    installed_endstone_version: Callable[[], str | None] | None = None,
    lock: LockData | None = None,
) -> int:
    """Run the section 6 setup sequence (see the module docstring)."""

    if paths.endbot_toml.exists():
        return _fail(
            f"FAIL setup: {paths.endbot_toml} already exists; edit it (section 2) or run `endbot update` "
            "to change the application — setup never touches an existing instance"
        )
    state = inspect_supervisor(paths.state_run)
    if state.status == RUNNING:
        return _fail(
            f"FAIL setup: a supervisor (pid {state.pid}) is running for this instance; run `endbot stop` first"
        )
    if state.status == STALE:
        _print(f"WARN setup: stale {paths.state_run / 'supervisor.pid'} (pid {state.pid} is not running); removed")
        clean_supervisor_pid(paths.state_run)

    plan = compute_setup_plan(
        paths,
        fresh=fresh,
        existing=existing,
        gamertags=gamertags,
        backup_worlds=backup_worlds,
        control_port=control_port,
        lock=lock,
    )
    _print(f"setup: plan ({plan.mode}) for instance {plan.root}:")
    for line in plan.lines:
        _print(f"  {line}")
    for warning in plan.warnings:
        _print(f"setup: WARN: {warning}")
    for failure in plan.failures:
        _print(f"setup: FAIL: {failure}")

    if not apply:
        if plan.ok:
            _print("setup: nothing was changed; re-run with --apply to execute this plan")
        else:
            _print("setup: nothing was changed; this plan cannot be applied until the FAIL items above are fixed")
        return 0

    if not plan.ok:
        return _fail("setup: refusing to apply (fix the FAIL items above)")
    if plan.mode == "existing" and not backup_worlds and not have_world_backup:
        return _fail(
            "setup: refusing to apply: --existing requires --i-have-a-world-backup "
            "(confirming worlds are backed up elsewhere) or --backup-worlds (copying worlds/ into the backup)"
        )

    if acquire_bds is None:

        def acquire_bds(server: Path) -> None:
            python = resolve_python(paths, environ, windows=windows)
            run_install(python, server)

    try:
        return _apply(
            paths,
            plan,
            acquire_bds=acquire_bds,
            installed_endstone_version=installed_endstone_version,
        )
    except (BdsError, ToolchainError) as error:
        return _fail(f"FAIL setup: {error}")


def _apply(
    paths: InstancePaths,
    plan: SetupPlan,
    *,
    acquire_bds: Callable[[Path], None],
    installed_endstone_version: Callable[[], str | None] | None,
) -> int:
    if plan.mode == "existing":
        result = create_backup(plan.server, paths.backups, worlds=plan.backup_worlds, reason="endbot setup --existing")
        _print(
            f"setup: backed up {len(result.files)} files into {result.directory} "
            "(manifest.json lists every file's SHA-256)"
        )

    if plan.bds.action != BDS_NONE:
        _print(f"setup: acquiring BDS through Endstone's acquisition step ({plan.bds.action}) ...")
        acquire_bds(plan.server)

    properties_path = plan.server / "server.properties"
    if plan.mode == "fresh":
        for message in edit_properties(properties_path, SAFETY_UPDATES):
            _print(f"setup: server.properties: {message}")
    try:
        verify_server_properties(properties_path)
    except (PreflightError, FileNotFoundError, OSError) as error:
        return _fail(f"FAIL setup: {properties_path}: {error} (required: online-mode=true, allow-cheats=false)")
    _print(f"setup: {properties_path}: verified online-mode=true, allow-cheats=false")

    create_state_dirs(paths)
    write_endbot_toml(paths, plan)
    _print(f"setup: wrote {paths.endbot_toml} and created state/{', state/'.join(STATE_SUBDIRS)}/")

    _print("setup: doctor after apply:")
    fatal = report_doctor(paths, installed_endstone_version)
    _print("setup: next steps:")
    _print("setup:   endbot doctor")
    _print("setup:   endbot start")
    controller_tags = ", ".join(plan.gamertags) or "none configured"
    _print(f"setup:   join once with each controller GamerTag ({controller_tags}) to bind it (section 3)")
    if fatal:
        return _fail("setup: FAIL: doctor still reports problems after apply (see above)")
    _print("setup: done; the first `endbot start` generates the owner keys and control token")
    return 0
