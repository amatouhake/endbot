"""Server-stored scripts in one versioned, atomically replaced plugin data file.

Each script has an owner (the controller who created it), a visibility and a
bounded revision history. Only the owner (or the console) edits, deletes,
restores or changes visibility; `shared` scripts are runnable by every
controller, `private` ones only by the owner. Scripts saved before ownership
existed (file version 1) have no owner and stay shared; the first controller to
edit one becomes its owner.
"""

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


def _revision(row) -> Revision:
    if (not isinstance(row, dict) or not isinstance(row.get("revision"), str) or not isinstance(row.get("source"), str)
            or type(row.get("savedAt")) is not int):
        raise ValueError("Invalid stored script revision")
    return Revision(row["revision"], row["source"], row["savedAt"])


class ScriptStore:
    def __init__(self, path: Path, config):
        self.path, self.config = path, config
        self._lock = threading.RLock()
        self._scripts = {}
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

    def list(self):
        with self._lock:
            return tuple(sorted(self._scripts.values(), key=lambda s: s.name.casefold()))

    def get(self, name):
        group_name(name)
        with self._lock:
            if name.casefold() not in self._scripts:
                raise LocalizedError("Script {name} does not exist", name=name)
            return self._scripts[name.casefold()]

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
            script = self.get(name)
            if not 1 <= index <= len(script.history):
                raise LocalizedError("Script revision is out of range")
            return self.save(script.name, script.history[index - 1].source, expected)

    def set_visibility(self, name, visibility, expected):
        if visibility not in VISIBILITIES:
            raise LocalizedError("Script visibility must be private or shared")
        with self._lock:
            script = self.get(name)
            if script.revision != expected:
                raise LocalizedError("Script changed; reopen the menu")
            # Visibility is not a source change: keep the revision so open menus stay valid.
            self._save({**self._scripts, name.casefold(): replace(script, visibility=visibility)})

    def delete(self, name, expected):
        with self._lock:
            script = self.get(name)
            if script.revision != expected:
                raise LocalizedError("Script changed; reopen the menu")
            self._save({k: v for k, v in self._scripts.items() if k != name.casefold()})

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
