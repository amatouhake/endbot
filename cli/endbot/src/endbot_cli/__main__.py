"""``endbot`` command-line entry point (docs/OPERATIONS.md)."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from endbot_cli.commands import run_console, run_controllers_reset, run_start, run_stop
from endbot_cli.doctor import DoctorContext, exit_code, run_doctor
from endbot_cli.instance import InstanceError, InstancePaths, resolve_instance_root
from endbot_cli.setupcmd import run_setup
from endbot_cli.updatecmd import run_rollback, run_update


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="endbot", description="Endbot operator CLI")
    parser.add_argument(
        "--instance",
        type=Path,
        default=None,
        help="instance directory (default: $ENDBOT_INSTANCE, else the launcher directory, else the working directory)",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)
    doctor = subcommands.add_parser("doctor", help="report instance health without changing anything (section 7)")
    doctor.add_argument(
        "--live",
        action="store_true",
        help="also probe runtime reachability on the control port (only while the runtime is expected to be up)",
    )
    subcommands.add_parser("start", help="start the runtime and BDS and supervise them (section 5a)")
    stop = subcommands.add_parser("stop", help="stop BDS and the runtime (section 5a)")
    stop.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="seconds to wait for the supervisor to exit (default: 120)",
    )
    console = subcommands.add_parser("console", help="send one line to the BDS console (section 5a)")
    console.add_argument("line", nargs="+", help="the console line to send (e.g. 'say hello')")
    controllers = subcommands.add_parser("controllers", help="manage controller enrollment (section 3)")
    controllers_subcommands = controllers.add_subparsers(dest="controllers_command", required=True)
    reset = controllers_subcommands.add_parser("reset", help="return one controller binding to pending (section 3)")
    reset.add_argument("gamertag", help="the GamerTag whose binding should be removed")
    setup = subcommands.add_parser(
        "setup", help="create or adopt an instance; prints the full plan first (section 6)"
    )
    mode = setup.add_mutually_exclusive_group(required=True)
    mode.add_argument("--fresh", action="store_true", help="create <instance>/server and download the locked BDS")
    mode.add_argument(
        "--existing",
        type=Path,
        metavar="PATH",
        help="adopt the vanilla BDS (or earlier Endstone) directory at PATH",
    )
    setup.add_argument(
        "--controller",
        action="append",
        default=[],
        metavar="GAMERTAG",
        help="Xbox GamerTag of a controller to enroll (repeatable, section 3)",
    )
    setup.add_argument("--apply", action="store_true", help="execute the printed plan (without it, only print)")
    setup.add_argument(
        "--backup-worlds", action="store_true", help="copy worlds/ into the setup backup (--existing only)"
    )
    setup.add_argument(
        "--i-have-a-world-backup",
        action="store_true",
        help="confirm worlds are backed up elsewhere; required by --apply unless --backup-worlds",
    )
    update = subcommands.add_parser(
        "update", help="install a new application version from a bundle archive (section 6a)"
    )
    update.add_argument(
        "archive",
        nargs="?",
        type=Path,
        help="platform bundle archive (.zip on Windows, .tar.gz on Linux)",
    )
    update.add_argument(
        "--sums",
        type=Path,
        default=None,
        metavar="PATH",
        help="SHA256SUMS to verify against (default: SHA256SUMS beside the archive)",
    )
    update.add_argument("--force", action="store_true", help="replace an already extracted app/<version>/")
    update.add_argument(
        "--backup-worlds", action="store_true", help="copy worlds/ into the backup when BDS must change"
    )
    update.add_argument(
        "--rollback", action="store_true", help="switch app/current back to the most recent previous version"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        root, _source = resolve_instance_root(args.instance)
    except InstanceError as error:
        print(f"endbot: FAIL: {error}", file=sys.stderr)
        return 1
    paths = InstancePaths.for_root(root)
    if args.command == "doctor":
        results = run_doctor(DoctorContext(paths=paths, live=args.live))
        for result in results:
            print(result.line())
        return exit_code(results)
    if args.command == "start":
        return run_start(paths)
    if args.command == "stop":
        return run_stop(paths, timeout=args.timeout)
    if args.command == "console":
        return run_console(paths, " ".join(args.line))
    if args.command == "controllers" and args.controllers_command == "reset":
        return run_controllers_reset(paths, args.gamertag)
    if args.command == "setup":
        return run_setup(
            paths,
            fresh=args.fresh,
            existing=args.existing,
            gamertags=args.controller,
            apply=args.apply,
            backup_worlds=args.backup_worlds,
            have_world_backup=args.i_have_a_world_backup,
        )
    if args.command == "update":
        if args.rollback == (args.archive is not None):
            print("endbot update: provide exactly one of <archive> or --rollback", file=sys.stderr)
            return 2
        if args.rollback:
            return run_rollback(paths)
        return run_update(
            paths,
            args.archive,
            sums=args.sums,
            force=args.force,
            backup_worlds=args.backup_worlds,
        )
    raise AssertionError(f"unhandled command {args.command!r}")


if __name__ == "__main__":
    raise SystemExit(main())
