"""``endbot`` command-line entry point (docs/OPERATIONS.md)."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from endbot_cli.commands import (
    first_start_tolerable,
    run_console,
    run_controllers_live,
    run_controllers_reset,
    run_start,
    run_stop,
    run_web_deploy,
    run_web_revoke,
    run_web_rotate_key,
)
from endbot_cli.config import TRANSPORTS
from endbot_cli.doctor import FAIL, WARN, CheckResult, DoctorContext, exit_code, run_doctor
from endbot_cli.instance import InstanceError, InstancePaths, resolve_instance_root
from endbot_cli.runstate import LAST_EXIT
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
    start = subcommands.add_parser("start", help="start the runtime and BDS and supervise them (section 5a)")
    start.add_argument(
        "--exit-with-bds",
        action="store_true",
        help="with [web] enabled, stop everything when BDS exits instead of keeping endbotd running",
    )
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
    for operation in ("add", "remove"):
        action = controllers_subcommands.add_parser(operation, help=f"{operation} a controller while BDS runs")
        action.add_argument("target", help="GamerTag (remove also accepts xuid:<id> or uuid:<id>)")
        if operation == "add":
            action.add_argument(
                "--role",
                choices=("scripter", "controller"),
                default=None,
                help="role for the grant (default for a new grant: controller)",
            )
    controllers_subcommands.add_parser("list", help="list effective controller configuration on the BDS console")
    reset = controllers_subcommands.add_parser("reset", help="return one controller binding to pending (section 3)")
    reset.add_argument("gamertag", help="the GamerTag whose binding should be removed")
    web = subcommands.add_parser("web", help="administer the Endbot web (docs/WEB.md)")
    web_subcommands = web.add_subparsers(dest="web_command", required=True)
    revoke = web_subcommands.add_parser(
        "revoke", help="delete one player's web account, passkeys and sessions; the controller grant stays"
    )
    revoke.add_argument("target", nargs="?", help="GamerTag or xuid:<id>")
    revoke.add_argument(
        "--site-sessions", action="store_true", help="instead, end every session issued through the trusted site"
    )
    deploy = web_subcommands.add_parser(
        "deploy", help="upload the Worker to your Cloudflare account; the token is prompted, never an argument"
    )
    deploy.add_argument("--account-id", help="Cloudflare account id; kept in endbot.toml after the first deploy")
    deploy.add_argument("--worker-name", help="Worker name (default: endbot)")
    deploy.add_argument(
        "--domain", help="custom domain instead of the workers.dev subdomain; a later plain deploy keeps it"
    )
    deploy.add_argument("--zone-id", help="the Cloudflare zone of --domain")
    deploy.add_argument(
        "--confirm-domain-change",
        action="store_true",
        help="accept that a new origin deletes every passkey and session",
    )
    deploy.add_argument(
        "--replace-worker",
        action="store_true",
        help="replace an existing Worker of that name that this instance did not deploy",
    )
    deploy.add_argument("--bundle", type=Path, help="a prebuilt web bundle (default: the installed one)")
    web_subcommands.add_parser("rotate-key", help="replace the link key; the token is prompted, never an argument")
    setup = subcommands.add_parser("setup", help="create or adopt an instance; prints the full plan first (section 6)")
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
        "--transport",
        choices=TRANSPORTS,
        default=None,
        help="Bot/BDS transport (nethernet only; BDS 1.26.60 refuses players on RakNet)",
    )
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
        if not (paths.state_run / LAST_EXIT).exists():
            # Setup tells operators to run doctor before their first start; the
            # runtime only creates the owner keys and control token during that
            # start, so their absence is expected until an instance has run once.
            results = [
                CheckResult(WARN, result.name, f"{result.message} (expected before the first `endbot start`)")
                if result.status == FAIL and first_start_tolerable(result.name, paths)
                else result
                for result in results
            ]
        for result in results:
            print(result.line())
        return exit_code(results)
    if args.command == "start":
        return run_start(paths, exit_with_bds=args.exit_with_bds)
    if args.command == "stop":
        return run_stop(paths, timeout=args.timeout)
    if args.command == "console":
        return run_console(paths, " ".join(args.line))
    if args.command == "web" and args.web_command == "deploy":
        return run_web_deploy(paths, account_id=args.account_id, worker_name=args.worker_name, domain=args.domain,
                              zone_id=args.zone_id, confirm_domain_change=args.confirm_domain_change,
                              replace_worker=args.replace_worker, bundle=args.bundle)
    if args.command == "web" and args.web_command == "rotate-key":
        return run_web_rotate_key(paths)
    if args.command == "web":
        return run_web_revoke(paths, args.target, site_sessions=args.site_sessions)
    if args.command == "controllers" and args.controllers_command == "reset":
        return run_controllers_reset(paths, args.gamertag)
    if args.command == "controllers":
        return run_controllers_live(
            paths, args.controllers_command, getattr(args, "target", None), getattr(args, "role", None)
        )
    if args.command == "setup":
        return run_setup(
            paths,
            fresh=args.fresh,
            existing=args.existing,
            gamertags=args.controller,
            transport=args.transport,
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
