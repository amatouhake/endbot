"""Persistent controller roles per Xbox identity (BOT-024 3a, docs/PERMISSIONS.md S1).

A grant with no entry is a **scripter**, which keeps today's behaviour and is the
migration rule. The file is separate from ``controller-policy.json`` so a version
that does not know it simply ignores it: after a rollback everyone acts as a
scripter again. Revoked grants keep no role, so a regrant starts from the default.

A grant can be made before its GamerTag has joined. Its role then waits in
``pending``, keyed by the casefolded GamerTag, until the first authenticated join
binds that name to an XUID.
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
MAX_GAMERTAG_LENGTH = 256


def _is_gamertag(value: object) -> bool:
    return isinstance(value, str) and 0 < len(value) <= MAX_GAMERTAG_LENGTH and value.isprintable()


def _load(path: Path) -> tuple[dict[str, str], dict[str, str]]:
    try:
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise ValueError("Controller roles must be a regular file")
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}, {}
    if not isinstance(document, dict) or document.get("version") != 1:
        raise ValueError("Unsupported controller roles version")
    if set(document) - {"version", "roles", "pending"}:
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
    entries = document.get("pending", {})
    if not isinstance(entries, dict):
        raise TypeError("Pending controller roles must be an object")
    pending = {}
    for gamertag, role in entries.items():
        if not _is_gamertag(gamertag):
            raise ValueError("Pending controller GamerTag must be non-empty printable text of at most 256 characters")
        if not isinstance(role, str) or role not in ROLES:
            raise ValueError("Controller role must be scripter or controller")
        pending[gamertag.casefold()] = role
    return roles, pending


def load_controller_roles(path: Path) -> dict[str, str]:
    """The XUID map; pending entries are read by :func:`load_pending_controller_roles`."""
    return _load(path)[0]


def load_pending_controller_roles(path: Path) -> dict[str, str]:
    """Roles waiting for their GamerTag's first authenticated join, keyed by casefolded name."""
    return _load(path)[1]


class ControllerRoles:
    """Roles keyed by XUID, plus pending roles keyed by casefolded GamerTag; ``path`` None is memory only."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self._roles, self._pending = _load(path) if path is not None else ({}, {})

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

    def set_pending(self, gamertag: str, role: str) -> None:
        """Record a role for a GamerTag that has not bound an XUID yet."""
        if not _is_gamertag(gamertag):
            raise ValueError("Pending controller GamerTag must be non-empty printable text of at most 256 characters")
        if not isinstance(role, str) or role not in ROLES:
            raise ValueError("Controller role must be scripter or controller")
        self._pending[gamertag.casefold()] = role
        if self.path is not None:
            self._save()

    def forget_pending(self, gamertags: Iterable[str]) -> None:
        """Remove pending roles by GamerTag."""
        changed = False
        for gamertag in set(gamertags):
            if isinstance(gamertag, str) and self._pending.pop(gamertag.casefold(), None) is not None:
                changed = True
        if changed and self.path is not None:
            self._save()

    def pending_role(self, gamertag: str) -> str | None:
        if not isinstance(gamertag, str):
            return None
        return self._pending.get(gamertag.casefold())

    def entries(self) -> dict[str, str]:
        return dict(self._roles)

    def pending_entries(self) -> dict[str, str]:
        return dict(self._pending)

    def _save(self) -> None:
        if self.path.is_symlink():
            raise ValueError("Controller roles must not be a symlink")
        document = {"version": 1, "roles": dict(sorted(self._roles.items()))}
        if self._pending:
            document["pending"] = dict(sorted(self._pending.items()))
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
