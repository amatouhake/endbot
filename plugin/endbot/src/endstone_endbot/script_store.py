"""Server-stored scripts in one versioned, atomically replaced plugin data file.

Each script has an owner (the controller who created it), a visibility and a
bounded revision history. Only the owner (or the console) edits, deletes,
restores or changes visibility; `shared` scripts are runnable by every
controller, `private` ones only by the owner. Scripts saved before ownership
existed (file version 1) have no owner and stay shared; the first controller to
edit one becomes its owner.

File scripts are `<name>.luau` files in the operator's folder, read at start-up
and on reload. They are shared, read-only (the file is the source of truth) and
never written to ``scripts.json``; a stored script of the same name wins.
"""

import hashlib
import json
import os
import tempfile
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path
from uuid import uuid4

from endstone_endbot.groups import group_name
from endstone_endbot.i18n import LocalizedError

HISTORY = 10
FILE_OWNER = "file"
FILE_SUFFIX = ".luau"
VISIBILITIES = ("private", "shared")
# New scripts and copies start private; the owner shares them explicitly.
DEFAULT_VISIBILITY = "private"


@dataclass(frozen=True)
class Revision:
    revision: str
    source: str
    saved_at: int  # Unix seconds


@dataclass(frozen=True)
class Script:
    name: str
    source: str
    revision: str
    owner: str | None = None  # "xuid:<xuid>", "player:<uuid>" or "console"; None: saved before ownership
    owner_name: str | None = None
    visibility: str = "shared"
    history: tuple[Revision, ...] = ()  # earlier sources, newest first
    saved_at: int = 0

    @property
    def file(self):
        """Loaded from the operator's script folder: read-only in game."""
        return self.owner == FILE_OWNER


def _revision(row) -> Revision:
    if (not isinstance(row, dict) or not isinstance(row.get("revision"), str) or not isinstance(row.get("source"), str)
            or type(row.get("savedAt")) is not int):
        raise ValueError("Invalid stored script revision")
    return Revision(row["revision"], row["source"], row["savedAt"])


def load_files(directory: Path, config):
    """``(scripts by casefolded name, problems)`` for the ``.luau`` files directly in ``directory``.

    A problem is ``(file name, LocalizedError)``; such files are skipped. A file's revision is its content
    hash, so reloading an unchanged file keeps open menus valid.
    """
    scripts, problems = {}, []
    if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
        raise LocalizedError("The script folder must be a regular directory")
    if not directory.exists():
        return scripts, problems
    for path in sorted(directory.iterdir(), key=lambda entry: entry.name.casefold()):
        if path.suffix.lower() != FILE_SUFFIX:
            continue
        try:
            name = group_name(path.stem)
            if path.is_symlink() or not path.is_file():
                raise LocalizedError("Script files must be regular files, not links")
            if path.stat().st_size > config.max_script_bytes:
                raise LocalizedError("Script source is empty or too large")
            try:
                source = path.read_bytes().decode("utf-8-sig")
            except UnicodeDecodeError as error:
                raise LocalizedError("Script files must be UTF-8 text") from error
            source = source.replace("\r\n", "\n").replace("\r", "\n")
            if not source.strip():
                raise LocalizedError("Script source is empty or too large")
            if name.casefold() in scripts:
                raise LocalizedError("Script {name} already exists", name=name)
        except (LocalizedError, ValueError, OSError) as error:
            problems.append((path.name, error))
            continue
        revision = "file:" + hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]
        scripts[name.casefold()] = Script(name, source, revision, FILE_OWNER, None, "shared", (),
                                          int(path.stat().st_mtime))
    return scripts, problems


class ScriptStore:
    def __init__(self, path: Path, config):
        self.path, self.config = path, config
        self._lock = threading.RLock()
        self._scripts = {}
        self._files = {}
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise ValueError("Scripts must be stored in a regular file")
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        if (
            not isinstance(document, dict)
            or document.get("version") not in {1, 2}
            or not isinstance(document.get("scripts"), list)
        ):
            raise ValueError("Invalid scripts file")
        # Lowering limits never deletes existing source; limits apply when saving/running.
        for row in document["scripts"]:
            if (
                not isinstance(row, dict)
                or not isinstance(row.get("source"), str)
                or not isinstance(row.get("revision"), str)
                or not isinstance(row.get("owner"), str | None)
                or not isinstance(row.get("ownerName"), str | None)
                or row.get("visibility", "shared") not in VISIBILITIES
                or not isinstance(row.get("history", []), list)
            ):
                raise ValueError("Invalid stored script")
            key = group_name(row["name"]).casefold()
            if key in self._scripts:
                raise ValueError("Duplicate stored script")
            self._scripts[key] = Script(
                row["name"], row["source"], row["revision"], row.get("owner"), row.get("ownerName"),
                row.get("visibility", "shared"), tuple(_revision(entry) for entry in row.get("history", [])[:HISTORY]),
                row.get("savedAt", 0) if type(row.get("savedAt", 0)) is int else 0,
            )

    def set_files(self, files):
        """Replace the file scripts; returns the names skipped because a stored script has them."""
        with self._lock:
            self._files = {key: script for key, script in files.items() if key not in self._scripts}
            return tuple(script.name for key, script in files.items() if key in self._scripts)

    def _all(self):
        return {**self._files, **self._scripts}

    def list(self):
        with self._lock:
            return tuple(sorted(self._all().values(), key=lambda s: s.name.casefold()))

    def get(self, name):
        group_name(name)
        with self._lock:
            scripts = self._all()
            if name.casefold() not in scripts:
                raise LocalizedError("Script {name} does not exist", name=name)
            return scripts[name.casefold()]

    def _checked(self, source):
        if (
            not isinstance(source, str)
            or not source.strip()
            or len(source.encode("utf-8")) > self.config.max_script_bytes
        ):
            raise LocalizedError("Script source is empty or too large")

    def save(self, name, source, expected=None, owner=None, owner_name=None):
        """Create (expected None) or update a script; an update keeps the old source in its history.

        A new script belongs to ``owner``; an unowned (pre-ownership) script is
        claimed by ``owner`` on its first edit. Callers check permissions.
        """
        group_name(name)
        self._checked(source)
        with self._lock:
            self._stored(name, must_exist=False)
            current = self._scripts.get(name.casefold())
            if expected != (current.revision if current else None):
                raise LocalizedError("Script changed; reopen the menu")
            if current is None and len(self._scripts) >= self.config.max_scripts:
                raise LocalizedError("Stored script limit reached")
            now = int(time.time())
            if current is None:
                script = Script(name, source, str(uuid4()), owner, owner_name, DEFAULT_VISIBILITY, (), now)
            else:
                previous = Revision(current.revision, current.source, current.saved_at)
                script = replace(current, source=source, revision=str(uuid4()), saved_at=now,
                                 history=(previous, *current.history)[:HISTORY],
                                 owner=current.owner or owner, owner_name=current.owner_name or owner_name)
            self._save({**self._scripts, name.casefold(): script})
            return script

    def restore(self, name, index, expected):
        """Make history entry ``index`` (1 = newest) the current source, as a new revision."""
        with self._lock:
            script = self._stored(name)
            if not 1 <= index <= len(script.history):
                raise LocalizedError("Script revision is out of range")
            return self.save(script.name, script.history[index - 1].source, expected)

    def set_visibility(self, name, visibility, expected):
        if visibility not in VISIBILITIES:
            raise LocalizedError("Script visibility must be private or shared")
        with self._lock:
            script = self._stored(name)
            if script.revision != expected:
                raise LocalizedError("Script changed; reopen the menu")
            # Visibility is not a source change: keep the revision so open menus stay valid.
            self._save({**self._scripts, name.casefold(): replace(script, visibility=visibility)})

    def rename(self, name, new_name, expected):
        """Rename keeping owner, visibility, revision and history; a case-only change is allowed."""
        group_name(new_name)
        with self._lock:
            script = self._stored(name)
            if script.revision != expected:
                raise LocalizedError("Script changed; reopen the menu")
            if new_name.casefold() != name.casefold() and new_name.casefold() in self._all():
                raise LocalizedError("Script {name} already exists", name=new_name)
            scripts = {k: v for k, v in self._scripts.items() if k != name.casefold()}
            self._save({**scripts, new_name.casefold(): replace(script, name=new_name)})

    def delete(self, name, expected):
        with self._lock:
            script = self._stored(name)
            if script.revision != expected:
                raise LocalizedError("Script changed; reopen the menu")
            self._save({k: v for k, v in self._scripts.items() if k != name.casefold()})

    def _stored(self, name, must_exist=True):
        """The stored script ``name`` (None if absent and allowed); a file script is never changed here."""
        if name.casefold() in self._files:
            raise LocalizedError("Script {name} comes from a file; edit the file, then /botscript reload",
                                 name=self._files[name.casefold()].name)
        if must_exist:
            return self.get(name)
        return self._scripts.get(name.casefold())

    def _save(self, scripts):
        if self.path.is_symlink():
            raise LocalizedError("Scripts file must not be a symlink")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        rows = [{
            "name": s.name, "source": s.source, "revision": s.revision, "owner": s.owner, "ownerName": s.owner_name,
            "visibility": s.visibility, "savedAt": s.saved_at,
            "history": [{"revision": r.revision, "source": r.source, "savedAt": r.saved_at} for r in s.history],
        } for s in scripts.values()]
        fd, temporary = tempfile.mkstemp(prefix=".scripts-", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump({"version": 2, "scripts": rows}, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            self._scripts = scripts
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
