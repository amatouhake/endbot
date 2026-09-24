"""Bundled ``endstone.lock`` data and BDS version-string comparison.

``src/endbot_cli/endstone.lock.json`` is a copy of the repository's
``endstone.lock`` so the CLI package works without the repository. The unit
test ``tests/test_lock.py`` fails when the two files drift; regenerate the copy
whenever the lock is updated.

Endstone writes ``<server>/version.txt`` as ``str(minecraft_version)`` (e.g.
``26.51``) while the lock records the full BDS version (e.g. ``1.26.51.1``).
Comparison therefore normalizes a leading ``1.`` away on both sides and then
compares up to the first three components, treating missing trailing
components on the shorter side as matching (``26.51`` equals ``26.51.1``).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


class LockError(ValueError):
    """The bundled lock data is missing or malformed."""


@dataclass(frozen=True, slots=True)
class LockData:
    endstone_package_version: str
    bds_version: str


def packaged_lock_path() -> Path:
    return Path(__file__).with_name("endstone.lock.json")


def load_lock(path: Path | None = None) -> LockData:
    lock_path = packaged_lock_path() if path is None else path
    try:
        document = json.loads(lock_path.read_text(encoding="utf-8"))
    except OSError as error:
        raise LockError(f"{lock_path}: lock data cannot be read: {error}") from error
    except ValueError as error:
        raise LockError(f"{lock_path}: lock data is invalid JSON: {error}") from error
    try:
        package_version = document["endstone"]["package_version"]
        bds_version = document["bds"]["version"]
    except (KeyError, TypeError) as error:
        raise LockError(f"{lock_path}: lock data is missing {error}; regenerate it from endstone.lock") from error
    if not isinstance(package_version, str) or not isinstance(bds_version, str):
        raise LockError(f"{lock_path}: endstone.package_version and bds.version must be strings")
    return LockData(endstone_package_version=package_version, bds_version=bds_version)


def normalize_bds_version(version: str) -> tuple[int, ...]:
    """Return version components with a leading ``1.`` removed.

    A leading ``1.`` is dropped only when at least two components remain
    (``1.26.51.1`` -> ``(26, 51, 1)``, ``26.51`` -> ``(26, 51)``).
    """

    parts = version.strip().split(".")
    if not parts or not all(part.isdecimal() for part in parts):
        raise LockError(f"version {version!r} is not a dotted numeric BDS version")
    numbers = [int(part) for part in parts]
    if len(numbers) > 2 and numbers[0] == 1:
        numbers = numbers[1:]
    return tuple(numbers)


def bds_versions_match(found: str, expected: str) -> bool:
    """Compare two BDS version strings on their first three components."""

    found_parts = normalize_bds_version(found)[:3]
    expected_parts = normalize_bds_version(expected)[:3]
    shared = min(len(found_parts), len(expected_parts))
    return found_parts[:shared] == expected_parts[:shared]
