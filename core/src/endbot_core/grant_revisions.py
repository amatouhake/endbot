"""Persistent grant revisions per controller identity (docs/ENDBOTD.md P4).

A revoke bumps the revision of every revoked identity, and work captured with an
older value is refused, so a regrant never revives it. The file is separate from
``controller-policy.json`` so a version that does not know it simply ignores it.
It is read only at start-up, when no work from before can still be running, which
is why a missing or reset file safely counts every revision as 0.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterable
from pathlib import Path
from uuid import UUID

FILE_NAME = "grant-revisions.json"


def load_grant_revisions(path: Path) -> dict[str, int]:
    try:
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise ValueError("Grant revisions must be a regular file")
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    if not isinstance(document, dict) or document.get("version") != 1:
        raise ValueError("Unsupported grant revisions version")
    if set(document) - {"version", "revisions"}:
        raise ValueError("Unknown grant revisions field")
    entries = document.get("revisions", {})
    if not isinstance(entries, dict):
        raise TypeError("Grant revisions must be an object")
    revisions = {}
    for identity, revision in entries.items():
        if type(revision) is not int or revision < 1:
            raise ValueError("Grant revision must be a positive integer")
        revisions[str(UUID(identity))] = revision
    if len(revisions) != len(entries):
        raise ValueError("Duplicate grant revision identity")
    return revisions


class GrantRevisions:
    """Revisions keyed by player UUID; ``path`` None keeps them in memory only."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self._revisions = load_grant_revisions(path) if path is not None else {}

    def get(self, identity: str) -> int:
        return self._revisions.get(identity, 0)

    def bump(self, identities: Iterable[str]) -> None:
        """Bump each identity, in memory first: a failed write must not leave old work authorized."""
        identities = set(identities)
        if not identities:
            return
        for identity in identities:
            self._revisions[identity] = self.get(identity) + 1
        if self.path is not None:
            self._save()

    def _save(self) -> None:
        if self.path.is_symlink():
            raise ValueError("Grant revisions must not be a symlink")
        document = {"version": 1, "revisions": dict(sorted(self._revisions.items()))}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".grant-revisions-", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(document, stream, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            Path(temporary).unlink(missing_ok=True)
