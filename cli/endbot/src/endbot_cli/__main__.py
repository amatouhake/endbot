"""``endbot`` command-line entry point (docs/OPERATIONS.md)."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from endbot_cli.doctor import DoctorContext, exit_code, run_doctor
from endbot_cli.instance import InstanceError, InstancePaths, resolve_instance_root

PLACEHOLDER_COMMANDS = {
    "setup": "create or adopt an instance (section 6)",
    "start": "start runtime and BDS (section 5a)",
    "stop": "stop BDS and the runtime (section 5a)",
    "update": "install a new application version (section 6a)",
    "controllers": "manage controller enrollment (section 3)",
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
    for name, help_text in PLACEHOLDER_COMMANDS.items():
        subcommands.add_parser(name, help=f"{help_text} - not implemented yet")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command != "doctor":
        print(f"endbot {args.command}: not implemented yet", file=sys.stderr)
        return 2
    try:
        root, _source = resolve_instance_root(args.instance)
    except InstanceError as error:
        print(f"endbot: FAIL: {error}", file=sys.stderr)
        return 1
    results = run_doctor(DoctorContext(paths=InstancePaths.for_root(root), live=args.live))
    for result in results:
        print(result.line())
    return exit_code(results)


if __name__ == "__main__":
    raise SystemExit(main())
