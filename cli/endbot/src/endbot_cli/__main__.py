"""``endbot`` command-line entry point (docs/OPERATIONS.md)."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from endbot_cli.commands import run_console, run_controllers_reset, run_start, run_stop
from endbot_cli.doctor import DoctorContext, exit_code, run_doctor
from endbot_cli.instance import InstanceError, InstancePaths, resolve_instance_root

PLACEHOLDER_COMMANDS = {
    "setup": "create or adopt an instance (section 6)",
    "update": "install a new application version (section 6a)",
}


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
    for name, help_text in PLACEHOLDER_COMMANDS.items():
        subcommands.add_parser(name, help=f"{help_text} - not implemented yet")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command in PLACEHOLDER_COMMANDS:
        print(f"endbot {args.command}: not implemented yet", file=sys.stderr)
        return 2
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
    raise AssertionError(f"unhandled command {args.command!r}")


if __name__ == "__main__":
    raise SystemExit(main())
