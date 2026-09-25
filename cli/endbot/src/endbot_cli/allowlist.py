"""BDS allow-list handling (``<server>/allowlist.json``, docs/OPERATIONS.md sections 4, 6, 7).

``allowlist.json`` is an operator-owned BDS file: the format is whatever
``allowlist add <name>`` writes on the server console, a JSON list of entry
objects. Endbot never rewrites existing entries; it only appends the entry
``{"ignoresPlayerLimit": false, "name": "<GamerTag>"}`` (no ``xuid`` — BDS fills
it on the first join) and only through the explicit, previewed ``setup --fresh``
path. A file that is not a JSON list of objects is never overwritten: the
caller decides how loudly to complain.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path

from endbot_cli.fsutil import atomic_write_text


class AllowlistError(ValueError):
    """``allowlist.json`` cannot be read or is not a JSON list of entry objects."""


def _entry_name(entry: Mapping[str, object]) -> str | None:
    name = entry.get("name")
    return name if isinstance(name, str) else None


def read_allowlist(path: Path) -> list[dict]:
    """Return the entry list; a missing file reads as empty.

    Raises :class:`AllowlistError` when the file is unreadable or is not a JSON
    list of objects (Endbot never overwrites such a file).
    """

    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return []
    except (OSError, UnicodeDecodeError) as error:
        raise AllowlistError(f"{path} cannot be read: {error}; fix the file yourself (Endbot never overwrites it)")
    try:
        document = json.loads(text)
    except json.JSONDecodeError as error:
        raise AllowlistError(
            f"{path} is not valid JSON: {error}; repair the file yourself (Endbot never overwrites it)"
        )
    if not isinstance(document, list) or any(not isinstance(entry, dict) for entry in document):
        raise AllowlistError(
            f"{path} is not a JSON list of entry objects (what `allowlist add <name>` writes); "
            "repair the file yourself (Endbot never overwrites it)"
        )
    return document


def entry_matches(entry: Mapping[str, object], gamertag: str, xuid: str | None = None) -> bool:
    """True when the entry lists ``gamertag`` (case-insensitive) or its bound ``xuid``."""

    name = _entry_name(entry)
    if name is not None and name.casefold() == gamertag.casefold():
        return True
    if xuid is not None:
        entry_xuid = entry.get("xuid")
        if entry_xuid is not None and str(entry_xuid) == str(xuid):
            return True
    return False


def missing_gamertags(entries: Sequence[Mapping[str, object]], gamertags: Sequence[str]) -> list[str]:
    """Return the gamertags with no name-matching entry, in order (duplicates collapse)."""

    missing: list[str] = []
    seen: list[Mapping[str, object]] = list(entries)
    for tag in gamertags:
        if any(entry_matches(entry, tag) for entry in seen):
            continue
        seen.append({"name": tag})
        missing.append(tag)
    return missing


def add_gamertags(path: Path, gamertags: Sequence[str]) -> list[str]:
    """Append one entry per not-yet-listed gamertag and return the ones added.

    Existing entries and their order are preserved; the file is written
    atomically (2-space indented JSON) only when something is added. Raises
    :class:`AllowlistError` when the file is unreadable or malformed.
    """

    entries = read_allowlist(path)
    added: list[str] = []
    for tag in gamertags:
        if any(entry_matches(entry, tag) for entry in entries):
            continue
        entries.append({"ignoresPlayerLimit": False, "name": tag})
        added.append(tag)
    if added:
        atomic_write_text(Path(path), json.dumps(entries, indent=2) + "\n")
    return added
