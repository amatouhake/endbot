"""Section 6 backups of operator-owned BDS files, with a SHA-256 manifest.

``endbot setup --existing`` and the ``endbot update`` BDS-change path copy the
operator-owned BDS files that Endstone may overwrite into
``<instance>/backups/<UTC timestamp>/`` before touching anything, and write a
``manifest.json`` there listing every backed-up file with its SHA-256 hash so a
restore can be verified. Worlds are copied only when explicitly requested
(``--backup-worlds``); they are large and Endstone never overwrites them.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from endbot_cli.fsutil import atomic_write_text

# Operator-owned files Endstone's `_download` may rewrite or leave alone; all are
# worth keeping before any acquisition step (docs/OPERATIONS.md section 6).
FILE_ENTRIES = ("server.properties", "allowlist.json", "permissions.json", "endstone.toml", "packetlimitconfig.json")
TREE_ENTRIES = ("behavior_packs", "resource_packs", "definitions")
EXECUTABLE_NAMES = ("bedrock_server.exe", "bedrock_server")
WORLD_ENTRY = "worlds"

MANIFEST_NAME = "manifest.json"
MANIFEST_SCHEMA = 1


@dataclass(frozen=True, slots=True)
class BackupEntry:
    """One source to copy: ``relative`` is its path inside the backup directory."""

    relative: str
    source: Path
    kind: str  # "file" | "tree"


@dataclass(frozen=True, slots=True)
class BackupResult:
    directory: Path
    manifest: Path
    files: tuple[str, ...]  # every backed-up file, relative to ``directory``
    missing: tuple[str, ...]  # standard entries that were absent (not copied)


def collect_backup_entries(server: Path, *, worlds: bool = False) -> tuple[BackupEntry, ...]:
    """Return the section 6 backup set that exists under ``server``."""

    server = Path(server)
    entries: list[BackupEntry] = []
    for name in FILE_ENTRIES:
        if (server / name).is_file():
            entries.append(BackupEntry(name, server / name, "file"))
    for name in TREE_ENTRIES:
        if (server / name).is_dir():
            entries.append(BackupEntry(name, server / name, "tree"))
    for name in EXECUTABLE_NAMES:
        if (server / name).is_file():
            entries.append(BackupEntry(name, server / name, "file"))
    if worlds and (server / WORLD_ENTRY).is_dir():
        entries.append(BackupEntry(WORLD_ENTRY, server / WORLD_ENTRY, "tree"))
    return tuple(entries)


def describe_entries(entries: Sequence[BackupEntry]) -> str:
    """Short human list of what will be backed up (plan output, section 6)."""

    return ", ".join(f"{entry.relative}/" if entry.kind == "tree" else entry.relative for entry in entries)


def missing_entries(server: Path, entries: Sequence[BackupEntry], *, worlds: bool = False) -> tuple[str, ...]:
    """Standard section 6 entries that do not exist under ``server``."""

    found = {entry.relative for entry in entries}
    missing = [name for name in (*FILE_ENTRIES, *TREE_ENTRIES) if name not in found]
    if not any(name in found for name in EXECUTABLE_NAMES):
        missing.append("server executable")
    if worlds and WORLD_ENTRY not in found:
        missing.append(WORLD_ENTRY)
    return tuple(missing)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _unique_directory(backups_root: Path, stamp: str) -> Path:
    candidate = backups_root / stamp
    counter = 2
    while candidate.exists():
        candidate = backups_root / f"{stamp}-{counter}"
        counter += 1
    return candidate


def create_backup(
    server: Path,
    backups_root: Path,
    *,
    worlds: bool = False,
    reason: str = "",
    now: datetime | None = None,
) -> BackupResult:
    """Copy the section 6 set out of ``server`` and write ``manifest.json``.

    ``now`` is a test hook; the directory name is a UTC ``YYYYMMDD-HHMMSSZ``
    timestamp (with a numeric suffix when needed).
    """

    server = Path(server)
    entries = collect_backup_entries(server, worlds=worlds)
    stamp_dt = now or datetime.now(timezone.utc)
    stamp = stamp_dt.strftime("%Y%m%d-%H%M%S") + "Z"
    directory = _unique_directory(Path(backups_root), stamp)
    directory.mkdir(parents=True)

    for entry in entries:
        target = directory / entry.relative
        if entry.kind == "tree":
            shutil.copytree(entry.source, target)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(entry.source, target)

    files: list[dict] = []
    for path in sorted(directory.rglob("*")):
        if path.is_file() and path.name != MANIFEST_NAME:
            files.append(
                {
                    "path": path.relative_to(directory).as_posix(),
                    "sha256": _sha256(path),
                    "bytes": path.stat().st_size,
                }
            )
    manifest_document = {
        "schema": MANIFEST_SCHEMA,
        "created": stamp_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "server": str(server),
        "reason": reason,
        "worlds-included": worlds,
        "files": files,
    }
    manifest = directory / MANIFEST_NAME
    atomic_write_text(manifest, json.dumps(manifest_document, indent=2) + "\n")
    return BackupResult(
        directory=directory,
        manifest=manifest,
        files=tuple(entry["path"] for entry in files),
        missing=missing_entries(server, entries, worlds=worlds),
    )
