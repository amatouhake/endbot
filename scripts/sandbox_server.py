"""One reusable BDS directory for local validation runs.

Windows Firewall records an allow/block decision per executable path, so a fresh
BDS directory per run raises a new prompt every time. A reusable sandbox keeps
``bedrock_server.exe`` at one path and resets everything a run creates (worlds,
plugin data, Endstone config, logs) before each use. The locked BDS is acquired
through Endstone only on first use or when the lock moves to another version.

A sandbox is a directory this module created: it carries a ``.endbot-sandbox``
marker with the installed version, the distribution's top-level entries and
pristine copies of the files BDS edits. Any other non-empty directory, such as an
operator instance, is refused. One run may hold a sandbox at a time.

Prepare or reset one for a manual run with::

    python scripts/sandbox_server.py %USERPROFILE%\\endbot-dev\\sandbox\\server
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "cli" / "endbot" / "src"))

from endbot_cli.bds import install_locked_bds
from endbot_cli.processes import process_alive

MARKER = ".endbot-sandbox"
MANIFEST = "manifest.json"
LOCK = "run.lock"
PRISTINE = "pristine"
# Distribution files that runs edit in place; restored before every run.
EDITED_FILES = ("server.properties", "allowlist.json", "permissions.json")


class SandboxError(RuntimeError):
    pass


def locked_bds_version() -> str:
    from endstone import __minecraft_version__

    return str(__minecraft_version__)


def _clear(server: Path, keep: set[str]) -> None:
    for entry in server.iterdir():
        if entry.name in keep:
            continue
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry)
        else:
            entry.unlink()


def _install(server: Path, marker: Path, version: str, install: Callable[[Path], None]) -> None:
    _clear(server, {MARKER})
    (marker / MANIFEST).unlink(missing_ok=True)
    install(server)
    pristine = marker / PRISTINE
    shutil.rmtree(pristine, ignore_errors=True)
    pristine.mkdir()
    for name in EDITED_FILES:
        if (server / name).is_file():
            shutil.copy2(server / name, pristine / name)
    entries = sorted(entry.name for entry in server.iterdir() if entry.name != MARKER)
    # Written last: a manifest proves the install finished.
    (marker / MANIFEST).write_text(json.dumps({"version": version, "entries": entries}, indent=2) + "\n",
                                   encoding="utf-8")


def _reset(server: Path, marker: Path, manifest: dict) -> None:
    _clear(server, {MARKER, *manifest["entries"]})
    for name in EDITED_FILES:
        source = marker / PRISTINE / name
        if source.is_file():
            shutil.copy2(source, server / name)
        else:
            (server / name).unlink(missing_ok=True)


def _acquire(marker: Path) -> Path:
    lock = marker / LOCK
    try:
        holder = int(lock.read_text(encoding="utf-8").strip())
    except (FileNotFoundError, ValueError):
        holder = None
    if holder is not None and holder != os.getpid() and process_alive(holder):
        raise SandboxError(f"{marker.parent} is in use by process {holder}; wait for that run or stop it")
    lock.write_text(str(os.getpid()), encoding="utf-8")
    return lock


@contextmanager
def reusable_server(
    server: Path,
    *,
    install: Callable[[Path], None] = install_locked_bds,
    version: Callable[[], str] = locked_bds_version,
) -> Iterator[Path]:
    """Yield ``server`` with the locked BDS installed and no state from earlier runs."""

    server = server.resolve()
    marker = server / MARKER
    if server.exists() and not server.is_dir():
        raise SandboxError(f"{server} is not a directory")
    if server.is_dir() and any(server.iterdir()) and not marker.is_dir():
        raise SandboxError(f"{server} is not an Endbot sandbox; refusing to reset it (use an empty directory)")
    marker.mkdir(parents=True, exist_ok=True)
    lock = _acquire(marker)
    try:
        expected = version()
        try:
            manifest = json.loads((marker / MANIFEST).read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            manifest = None
        if manifest is None or manifest.get("version") != expected:
            _install(server, marker, expected, install)
        else:
            _reset(server, marker, manifest)
        yield server
    finally:
        lock.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("server", type=Path, help="sandbox BDS directory (created when missing)")
    args = parser.parse_args(argv)
    try:
        with reusable_server(args.server) as server:
            print(f"sandbox ready: {server}")
    except SandboxError as error:
        print(f"sandbox: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
