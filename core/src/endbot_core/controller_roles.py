"""Persistent controller roles per Xbox identity (BOT-024 3a, docs/PERMISSIONS.md S1).

A grant with no entry is a **scripter**, which keeps today's behaviour and is the
migration rule. The file is separate from ``controller-policy.json`` so a version
that does not know it simply ignores it: after a rollback everyone acts as a
scripter again. Revoked grants keep no role, so a regrant starts from the default.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterable
from pathlib import Path

from endbot_core.enrollment import _is_xuid

FILE_NAME = "controller-roles.json"
ROLES = ("scripter", "controller")
DEFAULT_ROLE = "scripter"


def load_controller_roles(path: Path) -> dict[str, str]:
    try:
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise ValueError("Controller roles must be a regular file")
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    if not isinstance(document, dict) or document.get("version") != 1:
        raise ValueError("Unsupported controller roles version")
    if set(document) - {"version", "roles"}:
        raise ValueError("Unknown controller roles field")
    entries = document.get("roles", {})
    if not isinstance(entries, dict):
        raise TypeError("Controller roles must be an object")
    roles = {}
    for xuid, role in entries.items():
        if not _is_xuid(xuid):
            raise ValueError("Controller role identity must be an XUID")
        if not isinstance(role, str) or role not in ROLES:
            raise ValueError("Controller role must be scripter or controller")
        roles[xuid] = role
    return roles


class ControllerRoles:
    """Roles keyed by XUID; ``path`` None keeps them in memory only."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self._roles = load_controller_roles(path) if path is not None else {}

    def role(self, xuid: str) -> str:
        return self._roles.get(xuid, DEFAULT_ROLE)

    def set(self, xuid: str, role: str) -> None:
        """Record a role, in memory first: a failed write must not lose the change."""
        if not _is_xuid(xuid):
            raise ValueError("Controller role identity must be an XUID")
        if not isinstance(role, str) or role not in ROLES:
            raise ValueError("Controller role must be scripter or controller")
        self._roles[xuid] = role
        if self.path is not None:
            self._save()

    def forget(self, xuids: Iterable[str]) -> None:
        """Remove each XUID's role (S1: a revoked grant keeps none)."""
        changed = False
        for xuid in set(xuids):
            if self._roles.pop(xuid, None) is not None:
                changed = True
        if changed and self.path is not None:
            self._save()

    def entries(self) -> dict[str, str]:
        return dict(self._roles)

    def _save(self) -> None:
        if self.path.is_symlink():
            raise ValueError("Controller roles must not be a symlink")
        document = {"version": 1, "roles": dict(sorted(self._roles.items()))}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".controller-roles-", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(document, stream, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            Path(temporary).unlink(missing_ok=True)
