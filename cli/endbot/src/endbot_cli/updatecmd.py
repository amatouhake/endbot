"""``endbot update`` — install a new application version or roll back (section 6a).

A platform bundle archive (``.zip`` on Windows, ``.tar.gz`` on Linux) produced
by the release pipeline holds one ``endbot-<version>/`` directory with
``app/<newversion>/`` (the private Python with patched Endstone + plugin + CLI,
the private Node.js, and the runtime) plus the ``endbot`` / ``endbot.cmd``
launcher. Update extracts only
those, never touching ``state/``, ``endbot.toml``, the server directory, or the
current application, runs the NEW version's doctor against the instance with
the new interpreter, and switches ``app/current`` (a text file) atomically only
when that doctor is clean. A ``bds-version`` FAIL is handled specially: the
section 6 backup runs first, then the new version's Endstone acquisition step
moves the server to the new lock. Any other doctor FAIL keeps the old version
current. ``--rollback`` switches ``app/current`` back to the most recent
previous version directory still present in ``app/``.
"""

from __future__ import annotations

import os
import posixpath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from endbot_cli.backup import create_backup
from endbot_cli.bds import BdsError, run_install
from endbot_cli.commands import first_start_tolerable
from endbot_cli.config import ConfigError, load_config
from endbot_cli.fsutil import atomic_write_text
from endbot_cli.instance import LAUNCHER_NAMES, InstancePaths
from endbot_cli.runstate import RUNNING, STALE, clean_supervisor_pid, inspect_supervisor

SUMS_NAME = "SHA256SUMS"
APP_PREFIX = "app"
CURRENT_NAME = "current"
DOCTOR_LINE = re.compile(r"^(PASS|WARN|FAIL|SKIP) ([a-z0-9-]+): ")
_SUMS_LINE = re.compile(r"^([0-9a-fA-F]{64})[ \t]+\*?(.+)$")


class UpdateError(RuntimeError):
    """The update cannot proceed; the message names the problem and the fix."""


def _print(message: str) -> None:
    print(message, flush=True)


def _fail(message: str) -> int:
    print(message, file=sys.stderr, flush=True)
    return 1


def _on_windows(windows: bool | None) -> bool:
    return os.name == "nt" if windows is None else windows


def _python_name(windows: bool) -> str:
    return "python/python.exe" if windows else "python/bin/python3"


# ---------------------------------------------------------------------------
# Archive inspection and extraction (zip-slip safe; no symlinks)


@dataclass(frozen=True, slots=True)
class BundleMember:
    name: str  # exactly as stored in the archive
    parts: tuple[str, ...]  # relative to the bundle root (the single top-level directory is stripped)
    is_dir: bool
    mode: int | None = None  # POSIX permission bits recorded in the archive, when present
    link: str | None = None  # symlink target (tar bundles only)


@dataclass(frozen=True, slots=True)
class BundleInfo:
    version: str
    launchers: tuple[str, ...]


def _safe_parts(name: str) -> tuple[str, ...] | None:
    normalized = name.replace("\\", "/")
    if normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized):
        return None
    parts = tuple(part for part in normalized.split("/") if part != "")
    if not parts or any(part in (".", "..") for part in parts):
        return None
    return parts


def _safe_link(parts: tuple[str, ...], link: str) -> bool:
    """True when a symlink stays inside its ``app/<version>/`` tree."""

    if not link or link.startswith("/") or re.match(r"^[A-Za-z]:", link) or "\\" in link:
        return False
    if len(parts) < 3 or parts[0] != APP_PREFIX:
        return False
    resolved = posixpath.normpath(posixpath.join(*parts[2:-1], link) if len(parts) > 3 else link)
    return resolved != ".." and not resolved.startswith("../") and not resolved.startswith("/")


def _strip_root(members: list[BundleMember]) -> list[BundleMember]:
    """Drop the release pipeline's single top-level ``endbot-<version>/`` directory."""

    roots = {member.parts[0] for member in members}
    if len(roots) != 1:
        return members
    root = next(iter(roots))
    if root == APP_PREFIX or root in LAUNCHER_NAMES or not any(len(member.parts) > 1 for member in members):
        return members
    return [
        BundleMember(member.name, member.parts[1:], member.is_dir, member.mode, member.link)
        for member in members
        if len(member.parts) > 1
    ]


def read_members(archive: Path) -> tuple[BundleMember, ...]:
    """Return every archive member with validated path parts relative to the bundle root."""

    members: list[BundleMember] = []
    if _is_zip(archive):
        with zipfile.ZipFile(archive) as bundle:
            for info in bundle.infolist():
                parts = _safe_parts(info.filename)
                if parts is None:
                    raise UpdateError(f"{archive}: member {info.filename!r} is not a safe relative path; refusing")
                mode = (info.external_attr >> 16) & 0o7777 or None
                members.append(BundleMember(info.filename, parts, info.is_dir(), mode))
    else:
        with tarfile.open(archive, "r:*") as bundle:
            for info in bundle.getmembers():
                if not (info.isfile() or info.isdir() or info.issym()):
                    raise UpdateError(
                        f"{archive}: member {info.name!r} is not a file, directory, or symlink; refusing"
                    )
                parts = _safe_parts(info.name)
                if parts is None:
                    raise UpdateError(f"{archive}: member {info.name!r} is not a safe relative path; refusing")
                link = info.linkname if info.issym() else None
                members.append(BundleMember(info.name, parts, info.isdir(), info.mode & 0o7777, link))
    members = _strip_root(members)
    if not members:
        raise UpdateError(f"{archive}: the archive is empty")
    for member in members:
        if member.link is not None and not _safe_link(member.parts, member.link):
            raise UpdateError(f"{archive}: symlink {member.name!r} -> {member.link!r} leaves its application tree")
    return tuple(members)


def _is_zip(archive: Path) -> bool:
    return archive.name.lower().endswith(".zip")


def inspect_bundle(members: Sequence[BundleMember], archive: Path) -> BundleInfo:
    """Return the single ``app/<version>/`` and the launcher names in the bundle."""

    versions = sorted(
        # app/current is a file in real bundles; only directories holding content name versions.
        {member.parts[1] for member in members if member.parts[0] == APP_PREFIX and len(member.parts) >= 3}
    )
    if len(versions) != 1:
        raise UpdateError(
            f"{archive}: expected exactly one app/<version>/ directory, found {versions or 'none'}; "
            "use a platform bundle produced by the release pipeline"
        )
    version = versions[0]
    if version == CURRENT_NAME:
        raise UpdateError(f"{archive}: app/{CURRENT_NAME}/ is not a valid application version name")
    launchers = tuple(
        sorted({member.parts[0] for member in members if len(member.parts) == 1 and member.parts[0] in LAUNCHER_NAMES})
    )
    if not launchers:
        raise UpdateError(f"{archive}: the bundle has no {' / '.join(sorted(LAUNCHER_NAMES))} launcher at its root")
    return BundleInfo(version=version, launchers=launchers)


def _write_member(target: Path, member: BundleMember, source) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "wb") as handle:
        shutil.copyfileobj(source, handle)
    if os.name != "nt" and member.mode:
        os.chmod(target, member.mode & 0o777)


def extract_bundle(
    archive: Path, members: Sequence[BundleMember], info: BundleInfo, *, app_target: Path, launcher_dir: Path
) -> None:
    """Extract ``app/<version>/`` into ``app_target`` and the launchers into ``launcher_dir``.

    Permission bits recorded in the archive are kept (the private Python,
    Node.js, and the Linux launcher must stay executable), and symlinks are
    recreated only after ``read_members`` proved they stay inside the tree.
    """

    wanted = (APP_PREFIX, info.version)
    by_name = {member.name: member for member in members}
    if _is_zip(archive):
        with zipfile.ZipFile(archive) as bundle:
            for member in members:
                target = _member_target(member, wanted, app_target, launcher_dir, info)
                if target is None or member.is_dir:
                    continue
                with bundle.open(member.name) as source:
                    _write_member(target, member, source)
    else:
        with tarfile.open(archive, "r:*") as bundle:
            for item in bundle.getmembers():
                member = by_name.get(item.name)
                if member is None:
                    continue
                target = _member_target(member, wanted, app_target, launcher_dir, info)
                if target is None or member.is_dir:
                    continue
                if member.link is not None:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    os.symlink(member.link, target)
                    continue
                source = bundle.extractfile(item)
                if source is None:
                    raise UpdateError(f"{archive}: member {item.name!r} cannot be read")
                with source:
                    _write_member(target, member, source)


def _member_target(
    member: BundleMember,
    wanted: tuple[str, str],
    app_target: Path,
    launcher_dir: Path,
    info: BundleInfo,
) -> Path | None:
    parts = member.parts
    if parts[:2] == wanted:
        return app_target.joinpath(*parts[2:]) if len(parts) >= 3 else None
    if len(parts) == 1 and parts[0] in info.launchers:
        return launcher_dir / parts[0]
    return None


# ---------------------------------------------------------------------------
# SHA256SUMS verification


def load_sums(path: Path) -> dict[str, str]:
    """Parse a ``sha256sum``-format file into ``{basename: hex digest}``."""

    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as error:
        raise UpdateError(f"{path} cannot be read: {error}; pass --sums PATH or fix the file") from error
    entries: dict[str, str] = {}
    for line in text.splitlines():
        match = _SUMS_LINE.match(line.strip())
        if match:
            entries[Path(match.group(2).strip().replace("\\", "/")).name] = match.group(1).lower()
    return entries


def sha256_of(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_against_sums(archive: Path, sums_path: Path) -> str:
    """Return the archive's SHA-256; raise :class:`UpdateError` on any mismatch."""

    entries = load_sums(sums_path)
    expected = entries.get(archive.name)
    if expected is None:
        raise UpdateError(f"{sums_path} has no entry for {archive.name}; cannot verify the archive — refusing")
    actual = sha256_of(archive)
    if actual != expected:
        raise UpdateError(
            f"{archive.name} does not match {sums_path} (expected {expected}, found {actual}); "
            "the archive may be corrupted or tampered with — refusing"
        )
    return actual


# ---------------------------------------------------------------------------
# New-version doctor


def run_doctor_subprocess(
    python: Path, instance_root: Path, *, runner: Callable[..., object] = subprocess.run
) -> tuple[int, list[str]]:
    """Run the NEW version's doctor with the new interpreter (section 6a)."""

    command = [str(python), "-m", "endbot_cli", "--instance", str(instance_root), "doctor"]
    try:
        completed = runner(command, check=False, capture_output=True, encoding="utf-8", errors="replace")
    except OSError as error:
        raise UpdateError(f"cannot run the new version's doctor ({command[0]}): {error}") from error
    lines = (completed.stdout or "").splitlines()
    lines += [f"(stderr) {line}" for line in (completed.stderr or "").splitlines() if line.strip()]
    return completed.returncode, lines


def parse_doctor_lines(lines: Sequence[str]) -> list[tuple[str, str, str]]:
    """Return ``(status, name, message)`` for every parsed doctor line."""

    results: list[tuple[str, str, str]] = []
    for line in lines:
        match = DOCTOR_LINE.match(line)
        if match:
            results.append((match.group(1), match.group(2), line[match.end() :]))
    return results


def blocking_failures(results: Sequence[tuple[str, str, str]], paths: InstancePaths) -> list[tuple[str, str, str]]:
    """FAILs that block the switch; first-start-tolerable FAILs are allowed (section 6a)."""

    return [result for result in results if result[0] == "FAIL" and not first_start_tolerable(result[1], paths)]


# ---------------------------------------------------------------------------
# Commands


def _check_refusals(paths: InstancePaths) -> int | None:
    state = inspect_supervisor(paths.state_run)
    if state.status == RUNNING:
        return _fail(f"FAIL update: a supervisor (pid {state.pid}) is running; run `endbot stop` first")
    if state.status == STALE:
        _print(f"WARN update: stale {paths.state_run / 'supervisor.pid'} (pid {state.pid} is not running); removed")
        clean_supervisor_pid(paths.state_run)
    if not paths.endbot_toml.exists():
        return _fail(f"FAIL update: {paths.endbot_toml} is missing; run `endbot setup` before updating")
    return None


def run_update(
    paths: InstancePaths,
    archive: Path,
    *,
    sums: Path | None = None,
    force: bool = False,
    backup_worlds: bool = False,
    windows: bool | None = None,
    doctor: Callable[[Path, Path], tuple[int, list[str]]] | None = None,
    acquire: Callable[[Path, Path], None] | None = None,
) -> int:
    """Run the section 6a update sequence (see the module docstring)."""

    refusal = _check_refusals(paths)
    if refusal is not None:
        return refusal
    on_windows = _on_windows(windows)
    doctor = run_doctor_subprocess if doctor is None else doctor
    if acquire is None:

        def acquire(python: Path, server: Path) -> None:
            run_install(python, server)

    try:
        return _run_update(
            paths,
            Path(archive),
            sums=None if sums is None else Path(sums),
            force=force,
            backup_worlds=backup_worlds,
            windows=on_windows,
            doctor=doctor,
            acquire=acquire,
        )
    except (UpdateError, BdsError, ConfigError) as error:
        return _fail(f"FAIL update: {error}")


def _run_update(
    paths: InstancePaths,
    archive: Path,
    *,
    sums: Path | None,
    force: bool,
    backup_worlds: bool,
    windows: bool,
    doctor: Callable[[Path, Path], tuple[int, list[str]]],
    acquire: Callable[[Path, Path], None],
) -> int:
    if not archive.is_file():
        raise UpdateError(f"{archive} does not exist or is not a file")
    accepted = (".zip",) if windows else (".tar.gz", ".tgz")
    if not archive.name.lower().endswith(tuple(accepted)):
        raise UpdateError(f"{archive} is not a platform bundle ({' or '.join(accepted)} on this platform)")

    if sums is not None:
        _print(f"update: verifying {archive.name} against {sums}")
        verify_against_sums(archive, sums)
    else:
        default_sums = archive.parent / SUMS_NAME
        if default_sums.is_file():
            _print(f"update: verifying {archive.name} against {default_sums}")
            verify_against_sums(archive, default_sums)
        else:
            _print(f"update: WARN: no {SUMS_NAME} beside the archive; integrity is NOT verified")

    members = read_members(archive)
    info = inspect_bundle(members, archive)

    current = _read_current(paths)
    target = paths.root / "app" / info.version
    if info.version == current:
        raise UpdateError(
            f"app/{info.version} is the active version (app/current); refusing to replace the application in use "
            "(switch to another version first, or remove the directory yourself)"
        )
    if target.exists() and not force:
        raise UpdateError(f"{target} already exists; re-run with `endbot update --force` to replace it")
    if target.exists():
        shutil.rmtree(target)

    (paths.root / "app").mkdir(parents=True, exist_ok=True)
    launcher_dir = Path(tempfile.mkdtemp(prefix="endbot-update-launcher-"))
    try:
        _print(f"update: extracting app/{info.version}/ from {archive.name} into {target}")
        extract_bundle(archive, members, info, app_target=target, launcher_dir=launcher_dir)
        new_python = target / _python_name(windows)
        if not new_python.is_file():
            raise UpdateError(
                f"{archive}: {_python_name(windows)} is missing from app/{info.version}/; "
                f"not a platform bundle — {target} is kept for inspection"
            )

        _print(f"update: running app/{info.version}'s doctor against {paths.root} ({new_python})")
        returncode, lines = doctor(new_python, paths.root)
        for line in lines:
            _print(line)
        if returncode not in (0, 1):
            raise UpdateError(
                f"the new version's doctor exited with {returncode}; keeping app/current = "
                f"{current or '(unset)'} ({target} is kept for inspection)"
            )
        results = parse_doctor_lines(lines)
        if returncode == 1 and not any(status == "FAIL" for status, _name, _message in results):
            raise UpdateError(
                "the new version's doctor failed in an unrecognized output format; "
                f"keeping app/current = {current or '(unset)'} ({target} is kept for inspection)"
            )
        blocking = blocking_failures(results, paths)
        if blocking and all(name == "bds-version" for _status, name, _message in blocking):
            _bds_change(paths, new_python, backup_worlds=backup_worlds, acquire=acquire)
            blocking = _recheck(paths, new_python, doctor)
        if blocking:
            _print(
                f"update: FAIL: the new version's doctor reports problems; keeping app/current = {current or '(unset)'}"
            )
            for status, name, message in blocking:
                _print(f"update:   {status} {name}: {message}")
            _print(f"update: fix the problems and re-run (or remove {target})")
            return 1

        previous = current or "(none)"
        atomic_write_text(paths.app_current, f"{info.version}\n")
        for name in info.launchers:
            source = launcher_dir / name
            destination = paths.root / name
            shutil.copyfile(source, destination)
            if os.name != "nt" and not name.lower().endswith(".cmd"):
                os.chmod(destination, 0o755)
        _print(f"update: app/current now names {info.version} (previous {previous} is kept in app/)")
        _print(f"update: launcher {', '.join(info.launchers)} replaced from the archive")
        _print("update: next steps: endbot doctor, then endbot start")
        _print("update: note: `endbot update --rollback` switches app/current back to the most recent previous version")
        return 0
    finally:
        shutil.rmtree(launcher_dir, ignore_errors=True)


def _read_current(paths: InstancePaths) -> str | None:
    try:
        return paths.app_current.read_text(encoding="utf-8").strip() or None
    except FileNotFoundError:
        return None
    except OSError as error:
        raise UpdateError(f"{paths.app_current} cannot be read: {error}; fix the file permissions") from error


def _bds_change(
    paths: InstancePaths,
    new_python: Path,
    *,
    backup_worlds: bool,
    acquire: Callable[[Path, Path], None],
) -> None:
    """Section 6 backup, then the new version's Endstone acquisition step (section 6a)."""

    config = load_config(paths.endbot_toml)
    server = config.server.resolve(paths.root)
    _print("update: the new version locks a different BDS version (doctor reports a bds-version FAIL)")
    _print(
        "update: Endstone will overwrite the server executable and the vanilla behavior_packs/, resource_packs/, "
        "definitions/ entries it ships; worlds, server.properties values, allowlist.json, and permissions.json are kept"
    )
    result = create_backup(server, paths.backups, worlds=backup_worlds, reason="endbot update (BDS version change)")
    _print(
        f"update: backed up {len(result.files)} files into {result.directory} "
        "(manifest.json lists every file's SHA-256)"
    )
    _print(f"update: running the new version's Endstone acquisition step for {server}")
    acquire(new_python, server)


def _recheck(
    paths: InstancePaths,
    new_python: Path,
    doctor: Callable[[Path, Path], tuple[int, list[str]]],
) -> list[tuple[str, str, str]]:
    """Re-run the new doctor after the BDS change; return the blocking FAILs."""

    _print("update: re-running the new version's doctor after the BDS change")
    returncode, lines = doctor(new_python, paths.root)
    for line in lines:
        _print(line)
    if returncode not in (0, 1):
        raise UpdateError(
            f"the new version's doctor exited with {returncode} after the BDS change; keeping the old version"
        )
    results = parse_doctor_lines(lines)
    if returncode == 1 and not any(status == "FAIL" for status, _name, _message in results):
        raise UpdateError(
            "the new version's doctor failed in an unrecognized output format after the BDS change; "
            "keeping the old version"
        )
    return blocking_failures(results, paths)


def run_rollback(paths: InstancePaths, windows: bool | None = None) -> int:
    """Switch ``app/current`` back to the most recent previous version present."""

    del windows
    refusal = _check_refusals(paths)
    if refusal is not None:
        return refusal
    try:
        current = _read_current(paths)
    except UpdateError as error:
        return _fail(f"FAIL update: {error}")
    if current is None:
        return _fail(f"FAIL update: {paths.app_current} is missing or empty; there is no active version to roll back")
    app_root = paths.root / "app"
    if not app_root.is_dir():
        return _fail(f"FAIL update: {app_root} does not exist; there is no previous version to roll back to")
    candidates = [entry for entry in app_root.iterdir() if entry.is_dir() and entry.name != current]
    if not candidates:
        return _fail(f"FAIL update: no previous version directory is present in {app_root} (only {current})")
    previous = max(candidates, key=lambda entry: (entry.stat().st_mtime, entry.name))
    atomic_write_text(paths.app_current, f"{previous.name}\n")
    _print(f"update: app/current now names {previous.name} (was {current}; {current} is kept in app/)")
    _print("update: next steps: endbot doctor, then endbot start")
    _print("update: note: doctor refuses to start when the server's BDS does not match the rolled-back version's lock")
    return 0
