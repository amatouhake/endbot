"""Shared scripts in one versioned, atomically replaced plugin data file."""

import json
import os
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from endstone_endbot.groups import group_name
from endstone_endbot.i18n import LocalizedError


@dataclass(frozen=True)
class Script:
    name: str
    source: str
    revision: str


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
            or document.get("version") != 1
            or not isinstance(document.get("scripts"), list)
        ):
            raise ValueError("Invalid scripts file")
        # Lowering limits never deletes existing source; limits apply when saving/running.
        for row in document["scripts"]:
            if (
                not isinstance(row, dict)
                or not isinstance(row.get("source"), str)
                or not isinstance(row.get("revision"), str)
            ):
                raise ValueError("Invalid stored script")  # noqa: TRY004 - persistence validation API
            key = group_name(row["name"]).casefold()
            if key in self._scripts:
                raise ValueError("Duplicate stored script")
            self._scripts[key] = Script(row["name"], row["source"], row["revision"])

    def list(self):
        with self._lock:
            return tuple(sorted(self._scripts.values(), key=lambda s: s.name.casefold()))

    def get(self, name):
        group_name(name)
        with self._lock:
            if name.casefold() not in self._scripts:
                raise LocalizedError("Script {name} does not exist", name=name)
            return self._scripts[name.casefold()]

    def save(self, name, source, expected=None):
        group_name(name)
        if (
            not isinstance(source, str)
            or not source.strip()
            or len(source.encode("utf-8")) > self.config.max_script_bytes
        ):
            raise LocalizedError("Script source is empty or too large")
        with self._lock:
            current = self._scripts.get(name.casefold())
            if expected != (current.revision if current else None):
                raise LocalizedError("Script changed; reopen the menu")
            if current is None and len(self._scripts) >= self.config.max_scripts:
                raise LocalizedError("Stored script limit reached")
            scripts = {**self._scripts, name.casefold(): Script(name, source, str(uuid4()))}
            self._save(scripts)

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
        fd, temporary = tempfile.mkstemp(prefix=".scripts-", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump({"version": 1, "scripts": [vars(s) for s in scripts.values()]}, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            self._scripts = scripts
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
