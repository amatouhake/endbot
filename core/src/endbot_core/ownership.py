"""Bot and Group ownership by identity (BOT-024 4a, docs/PERMISSIONS.md S2, S7).

The creator is the owner and each Bot or Group is ``shared`` or ``private``. An
identity with no entry is unowned and shared, which is the migration rule for
everything created before ownership existed. The file is separate from the
runtime profiles and ``groups.json`` so a version that does not know it simply
ignores it: after a rollback, private Bots and Groups are shared again.

Nothing here refuses an operation yet (that is 4b); the store only records owners
and visibility. The console creates Bots and Groups that stay unowned.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from uuid import UUID

FILE_NAME = "ownership.json"
VISIBILITIES = ("shared", "private")
KINDS = ("bots", "groups")
OWNER_PREFIXES = ("xuid:", "player:")
MAX_OWNER_LENGTH = 128
MAX_OWNER_NAME_LENGTH = 64


def owner_key(principal) -> str | None:
    """The stable owner identity of a principal: None for the console, else XUID or player UUID.

    This is the same key ``ScriptService.owner_key`` stores for scripts.
    """
    if principal is None:
        return None
    player_id = getattr(principal, "player_id", None)
    xuid = getattr(principal, "xuid", None)
    if player_id is None and xuid is None:
        return None
    if xuid:
        return f"xuid:{xuid}"
    return f"player:{player_id}"


def _kind(kind: str) -> str:
    if kind not in KINDS:
        raise ValueError("Ownership kind must be bots or groups")
    return kind


def _identity(identity) -> str:
    try:
        return str(UUID(identity if isinstance(identity, str) else str(identity)))
    except (AttributeError, TypeError, ValueError) as error:
        raise ValueError("Ownership identity must be a UUID") from error


def _is_owner(owner) -> bool:
    if not isinstance(owner, str) or not 0 < len(owner) <= MAX_OWNER_LENGTH:
        return False
    return owner.startswith(OWNER_PREFIXES) and owner not in OWNER_PREFIXES


def _is_owner_name(owner_name) -> bool:
    return owner_name is None or (isinstance(owner_name, str) and len(owner_name) <= MAX_OWNER_NAME_LENGTH)


@dataclass(frozen=True)
class Ownership:
    owner: str
    owner_name: str | None
    visibility: str


def _entry(value) -> Ownership:
    if not isinstance(value, dict):
        raise TypeError("Ownership entry must be an object")
    if set(value) - {"owner", "ownerName", "visibility"}:
        raise ValueError("Unknown ownership entry field")
    owner = value.get("owner")
    if not _is_owner(owner):
        raise ValueError("Ownership owner must start with xuid: or player:")
    owner_name = value.get("ownerName")
    if not _is_owner_name(owner_name):
        raise ValueError("Ownership ownerName must be a string of at most 64 characters or null")
    visibility = value.get("visibility")
    if visibility not in VISIBILITIES:
        raise ValueError("Ownership visibility must be shared or private")
    return Ownership(owner, owner_name, visibility)


def load_ownership(path: Path) -> dict[str, dict[str, Ownership]]:
    """Read ``ownership.json``; a missing file is empty, a symlink or bad file is refused."""
    try:
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise ValueError("Ownership must be a regular file")
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {kind: {} for kind in KINDS}
    if not isinstance(document, dict) or document.get("version") != 1:
        raise ValueError("Unsupported ownership version")
    if set(document) - {"version", *KINDS}:
        raise ValueError("Unknown ownership field")
    entries: dict[str, dict[str, Ownership]] = {}
    for kind in KINDS:
        table = document.get(kind, {})
        if not isinstance(table, dict):
            raise TypeError(f"Ownership {kind} must be an object")
        parsed: dict[str, Ownership] = {}
        for key, value in table.items():
            identity = _identity(key)
            if identity in parsed:
                raise ValueError("Duplicate ownership identity")
            parsed[identity] = _entry(value)
        entries[kind] = parsed
    return entries


class OwnershipStore:
    """Owners and visibility keyed by identity UUID; ``path`` None keeps them in memory only."""

    def __init__(self, path: Path | None = None, *, bots_default: str = "shared",
                 groups_default: str = "shared") -> None:
        for kind, value in (("bots", bots_default), ("groups", groups_default)):
            if value not in VISIBILITIES:
                raise ValueError(f"Ownership {kind}-default must be shared or private")
        self.path = Path(path) if path is not None else None
        self._defaults = {"bots": bots_default, "groups": groups_default}
        self._entries = (load_ownership(self.path) if self.path is not None
                         else {kind: {} for kind in KINDS})

    def get(self, kind: str, identity: str) -> Ownership | None:
        return self._entries[_kind(kind)].get(_identity(identity))

    def record(self, kind: str, identity: str, principal) -> None:
        """Record ``principal`` as the owner unless it is the console or an entry already exists."""
        kind = _kind(kind)
        owner = owner_key(principal)
        if owner is None:
            return
        identity = _identity(identity)
        if identity in self._entries[kind]:
            return
        name = getattr(principal, "name", None)
        if not isinstance(name, str) or len(name) > MAX_OWNER_NAME_LENGTH:
            name = None
        self._entries[kind][identity] = Ownership(owner, name, self._defaults[kind])
        if self.path is not None:
            self._save()

    def set_visibility(self, kind: str, identity: str, visibility: str) -> None:
        kind = _kind(kind)
        if visibility not in VISIBILITIES:
            raise ValueError("Ownership visibility must be shared or private")
        identity = _identity(identity)
        entry = self._entries[kind].get(identity)
        if entry is None:
            raise ValueError("Ownership entry does not exist")
        self._entries[kind][identity] = replace(entry, visibility=visibility)
        if self.path is not None:
            self._save()

    def set_owner(self, kind: str, identity: str, owner: str, owner_name: str | None) -> None:
        """Assign an owner (the console's 4c reassignment); validates the stored values."""
        kind = _kind(kind)
        if not _is_owner(owner):
            raise ValueError("Ownership owner must start with xuid: or player:")
        if not _is_owner_name(owner_name):
            raise ValueError("Ownership ownerName must be a string of at most 64 characters or null")
        identity = _identity(identity)
        entry = self._entries[kind].get(identity)
        visibility = entry.visibility if entry is not None else self._defaults[kind]
        self._entries[kind][identity] = Ownership(owner, owner_name, visibility)
        if self.path is not None:
            self._save()

    def forget(self, kind: str, identity: str) -> None:
        kind = _kind(kind)
        if self._entries[kind].pop(_identity(identity), None) is not None and self.path is not None:
            self._save()

    def visible_to(self, kind: str, identity: str, principal) -> bool:
        """Whether ``principal`` may see the item (4b uses it; S4, S12)."""
        entry = self.get(kind, identity)
        if entry is None or entry.visibility == "shared":
            return True
        key = owner_key(principal)
        return key is None or entry.owner == key

    def is_owner(self, kind: str, identity: str, principal) -> bool:
        """Whether ``principal`` is the console or the item's owner; unowned items count as owned."""
        entry = self.get(kind, identity)
        if entry is None:
            return True
        key = owner_key(principal)
        return key is None or entry.owner == key

    def _save(self) -> None:
        if self.path.is_symlink():
            raise ValueError("Ownership must not be a symlink")
        document = {"version": 1}
        for kind in KINDS:
            document[kind] = {
                identity: {"owner": entry.owner, "ownerName": entry.owner_name, "visibility": entry.visibility}
                for identity, entry in sorted(self._entries[kind].items())
            }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".ownership-", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(document, stream, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            Path(temporary).unlink(missing_ok=True)
