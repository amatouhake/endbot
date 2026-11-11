"""Stored event triggers (BOT-022 step 5a, docs/EVENTS.md E6, E8).

``state/triggers.json`` holds the bindings between an event kind and a script.
The file is separate from the runtime profiles so a version that does not know
it simply ignores it: after a rollback the triggers stay on disk and do not run.
Every write validates the whole trigger and replaces the file atomically, so
endbotd never starts on a half-written store.
"""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
import threading
from dataclasses import dataclass, replace
from pathlib import Path
from uuid import UUID

FILE_NAME = "triggers.json"
SCHEMA_VERSION = 1
VISIBILITIES = ("private", "shared")
STATUSES = ("armed", "paused", "disabled")
CONCURRENCIES = ("skip", "queue", "parallel")
DEFAULT_POLICY = {"concurrency": "skip", "cooldown": 20, "startsPerMinute": 10, "queue": 4}
POLICY_BOUNDS = {"cooldown": (0, 72000), "startsPerMinute": (1, 60), "queue": (1, 16)}
MAX_OWNER_LENGTH = 128
MAX_OWNER_NAME_LENGTH = 64
MAX_TRIGGER_NAME_LENGTH = 32
MAX_SCRIPT_LENGTH = 32
MAX_ARGUMENTS = 32
MAX_ARGUMENT_LENGTH = 256
MAX_REASON_LENGTH = 128
MAX_LAST_ERROR_LENGTH = 256

_NAME = re.compile(r"[A-Za-z0-9_-]{1,32}\Z")
# category.action, and the wall-clock kind, which EVENTS.md names with one word (the registry schema agrees).
_KIND = re.compile(r"(?:[a-z]+\.[a-z][a-z_]*|schedule)\Z")
_TRIGGER_FIELDS = frozenset(
    {
        "name",
        "owner",
        "ownerName",
        "visibility",
        "kind",
        "filter",
        "script",
        "arguments",
        "approved",
        "status",
        "reason",
        "policy",
        "stats",
    }
)
_APPROVED_FIELDS = frozenset({"revision", "declaration", "paramsHash"})
# Who approved (step 5b): the owner's grant revision and player id, so a revoke and regrant never revive a trigger.
_APPROVED_OPTIONAL = frozenset({"grantRevision", "playerId"})
_STATS_FIELDS = frozenset({"starts", "missed", "skipped", "lastStart", "lastGap", "lastError"})
_STATS_ORDER = ("starts", "missed", "skipped", "lastStart", "lastGap", "lastError")


@dataclass(frozen=True)
class Trigger:
    """One stored trigger binding; the field order is the file order."""

    name: str
    owner: str
    owner_name: str | None
    visibility: str
    kind: str
    filter: dict
    script: str
    arguments: list[str]
    approved: dict
    status: str
    reason: str | None
    policy: dict
    stats: dict


def _validate_policy(policy) -> None:
    if not isinstance(policy, dict):
        raise ValueError("Trigger policy must be an object")  # noqa: TRY004 - persisted JSON schema validation
    missing = sorted(set(DEFAULT_POLICY) - set(policy))
    if missing:
        raise ValueError(f"Trigger policy is missing field {missing[0]!r}")
    policy_from(policy)


def policy_from(values: dict) -> dict:
    """Merge a partial policy over ``DEFAULT_POLICY`` and validate every bound."""
    if not isinstance(values, dict):
        raise ValueError("Trigger policy must be an object")  # noqa: TRY004 - persisted JSON schema validation
    unknown = sorted(set(values) - set(DEFAULT_POLICY))
    if unknown:
        raise ValueError(f"Unknown trigger policy field {unknown[0]!r}")
    policy = {**DEFAULT_POLICY, **values}
    if policy["concurrency"] not in CONCURRENCIES:
        raise ValueError("Trigger policy concurrency must be skip, queue or parallel")
    for field, (low, high) in POLICY_BOUNDS.items():
        value = policy[field]
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f"Trigger policy {field} must be an integer from {low} to {high}")
    return policy


def _validate_name(name) -> None:
    if not isinstance(name, str) or _NAME.fullmatch(name) is None:
        raise ValueError(
            f"Trigger name must be 1-{MAX_TRIGGER_NAME_LENGTH} letters, digits, underscores or hyphens"
        )


def _valid_owner(owner) -> bool:
    if not isinstance(owner, str) or not 0 < len(owner) <= MAX_OWNER_LENGTH:
        return False
    if owner == "console":
        return True
    if owner.startswith("xuid:"):
        return owner[5:].isascii() and owner[5:].isdecimal()
    if owner.startswith("player:"):
        try:
            UUID(owner[7:])
        except (AttributeError, TypeError, ValueError):
            return False
        return True
    return False


def _validate_owner(owner) -> None:
    if not _valid_owner(owner):
        raise ValueError("Trigger owner must be console, xuid:<digits> or player:<uuid>")


def _validate_owner_name(owner_name) -> None:
    if owner_name is not None and (
        not isinstance(owner_name, str) or len(owner_name) > MAX_OWNER_NAME_LENGTH
    ):
        raise ValueError(
            f"Trigger ownerName must be a string of at most {MAX_OWNER_NAME_LENGTH} characters or null"
        )


def _json_safe(value) -> bool:
    if value is None or type(value) in (bool, int, str):
        return True
    if type(value) is float:
        return math.isfinite(value)
    if isinstance(value, list):
        return all(_json_safe(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and _json_safe(item) for key, item in value.items())
    return False


def _validate_approved(approved) -> None:
    if not isinstance(approved, dict):
        raise ValueError("Trigger approved must be an object")  # noqa: TRY004 - persisted JSON schema validation
    unknown = sorted(set(approved) - _APPROVED_FIELDS - _APPROVED_OPTIONAL)
    if unknown:
        raise ValueError(f"Trigger approved has unknown field {unknown[0]!r}")
    missing = sorted(_APPROVED_FIELDS - set(approved))
    if missing:
        raise ValueError(f"Trigger approved is missing field {missing[0]!r}")
    if not isinstance(approved["revision"], str):
        raise ValueError("Trigger approved revision must be a string")  # noqa: TRY004 - persisted JSON schema
    declaration = approved["declaration"]
    if not isinstance(declaration, list) or not all(isinstance(item, str) for item in declaration):
        raise ValueError("Trigger approved declaration must be a list of strings")
    if not isinstance(approved["paramsHash"], str):
        raise ValueError("Trigger approved paramsHash must be a string")  # noqa: TRY004 - persisted JSON schema
    grant = approved.get("grantRevision")
    if grant is not None and (type(grant) is not int or grant < 0):
        raise ValueError("Trigger approved grantRevision must be a non-negative integer or null")
    player = approved.get("playerId")
    if player is not None and (not isinstance(player, str) or not 0 < len(player) <= 64):
        raise ValueError("Trigger approved playerId must be a string of at most 64 characters or null")


def _validate_stats(stats) -> None:
    if not isinstance(stats, dict):
        raise ValueError("Trigger stats must be an object")  # noqa: TRY004 - persisted JSON schema validation
    unknown = sorted(set(stats) - _STATS_FIELDS)
    if unknown:
        raise ValueError(f"Trigger stats has unknown field {unknown[0]!r}")
    missing = sorted(_STATS_FIELDS - set(stats))
    if missing:
        raise ValueError(f"Trigger stats is missing field {missing[0]!r}")
    for field in ("starts", "missed", "skipped"):
        value = stats[field]
        if type(value) is not int or value < 0:
            raise ValueError(f"Trigger stats {field} must be an integer of 0 or more")
    for field in ("lastStart", "lastGap"):
        value = stats[field]
        if value is not None and (type(value) not in (int, float) or not math.isfinite(value)):
            raise ValueError(f"Trigger stats {field} must be a finite number or null")
    error = stats["lastError"]
    if error is not None and (
        not isinstance(error, str) or len(error) > MAX_LAST_ERROR_LENGTH
    ):
        raise ValueError(
            f"Trigger stats lastError must be a string of at most {MAX_LAST_ERROR_LENGTH} characters or null"
        )


def _validate_trigger(trigger) -> None:
    if not isinstance(trigger, Trigger):
        raise ValueError("Trigger must be a Trigger")  # noqa: TRY004 - the store refuses a malformed change
    _validate_name(trigger.name)
    _validate_owner(trigger.owner)
    _validate_owner_name(trigger.owner_name)
    if trigger.visibility not in VISIBILITIES:
        raise ValueError("Trigger visibility must be private or shared")
    if not isinstance(trigger.kind, str) or _KIND.fullmatch(trigger.kind) is None:
        raise ValueError("Trigger kind must be an event kind named category.action, or schedule")
    if not isinstance(trigger.filter, dict) or not _json_safe(trigger.filter):
        raise ValueError("Trigger filter must be a JSON-safe object")
    if not isinstance(trigger.script, str) or not 1 <= len(trigger.script) <= MAX_SCRIPT_LENGTH:
        raise ValueError(f"Trigger script must be a string of 1-{MAX_SCRIPT_LENGTH} characters")
    arguments = trigger.arguments
    if not isinstance(arguments, list) or len(arguments) > MAX_ARGUMENTS:
        raise ValueError(f"Trigger arguments must be a list of at most {MAX_ARGUMENTS} strings")
    for argument in arguments:
        if not isinstance(argument, str) or len(argument) > MAX_ARGUMENT_LENGTH:
            raise ValueError(
                f"Trigger arguments must be strings of at most {MAX_ARGUMENT_LENGTH} characters"
            )
    _validate_approved(trigger.approved)
    if trigger.status not in STATUSES:
        raise ValueError("Trigger status must be armed, paused or disabled")
    if (
        trigger.reason is not None
        and (not isinstance(trigger.reason, str) or len(trigger.reason) > MAX_REASON_LENGTH)
    ):
        raise ValueError(f"Trigger reason must be a string of at most {MAX_REASON_LENGTH} characters or null")
    if trigger.status == "armed" and trigger.reason is not None:
        raise ValueError("Trigger reason must be null when armed")
    _validate_policy(trigger.policy)
    _validate_stats(trigger.stats)


def _trigger_from(row, index: int) -> Trigger:
    if not isinstance(row, dict):
        raise ValueError(f"Trigger {index} must be an object")  # noqa: TRY004 - persisted JSON schema validation
    unknown = sorted(set(row) - _TRIGGER_FIELDS)
    if unknown:
        raise ValueError(f"Trigger {index} has unknown field {unknown[0]!r}")
    missing = sorted(_TRIGGER_FIELDS - set(row))
    if missing:
        raise ValueError(f"Trigger {index} is missing field {missing[0]!r}")
    trigger = Trigger(
        name=row["name"],
        owner=row["owner"],
        owner_name=row["ownerName"],
        visibility=row["visibility"],
        kind=row["kind"],
        filter=row["filter"],
        script=row["script"],
        arguments=row["arguments"],
        approved=row["approved"],
        status=row["status"],
        reason=row["reason"],
        policy=row["policy"],
        stats=row["stats"],
    )
    try:
        _validate_trigger(trigger)
    except ValueError as error:
        raise ValueError(f"Trigger {index}: {error}") from error
    return trigger


def _trigger_json(trigger: Trigger) -> dict:
    return {
        "name": trigger.name,
        "owner": trigger.owner,
        "ownerName": trigger.owner_name,
        "visibility": trigger.visibility,
        "kind": trigger.kind,
        "filter": trigger.filter,
        "script": trigger.script,
        "arguments": list(trigger.arguments),
        "approved": {
            "revision": trigger.approved["revision"],
            "declaration": list(trigger.approved["declaration"]),
            "paramsHash": trigger.approved["paramsHash"],
            **{field: trigger.approved[field] for field in sorted(_APPROVED_OPTIONAL) if field in trigger.approved},
        },
        "status": trigger.status,
        "reason": trigger.reason,
        "policy": {field: trigger.policy[field] for field in DEFAULT_POLICY},
        "stats": {field: trigger.stats[field] for field in _STATS_ORDER},
    }


def load_triggers(path: Path) -> list[Trigger]:
    """Read ``triggers.json``; a missing file is empty, a symlink or bad file is refused."""
    try:
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise ValueError("Triggers must be stored in a regular file")
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except UnicodeDecodeError as error:
        raise ValueError("Triggers file must be UTF-8 text") from error
    try:
        document = json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError(f"Triggers file is not valid JSON: {error}") from error
    if not isinstance(document, dict):
        raise ValueError("Triggers file must be an object")  # noqa: TRY004 - persisted JSON schema validation
    if set(document) - {"schemaVersion", "triggers"}:
        unknown = min(set(document) - {"schemaVersion", "triggers"})
        raise ValueError(f"Unknown triggers file field {unknown!r}")
    if type(document.get("schemaVersion")) is not int or document["schemaVersion"] != SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported triggers schemaVersion {document.get('schemaVersion')!r}; expected {SCHEMA_VERSION}"
        )
    rows = document.get("triggers")
    if not isinstance(rows, list):
        raise ValueError("Triggers file must hold a triggers list")  # noqa: TRY004 - persisted JSON schema validation
    triggers: list[Trigger] = []
    keys = set()
    for index, row in enumerate(rows):
        trigger = _trigger_from(row, index)
        key = (trigger.owner, trigger.name)
        if key in keys:
            raise ValueError(f"Duplicate trigger {trigger.owner}/{trigger.name}")
        keys.add(key)
        triggers.append(trigger)
    return triggers


class TriggerStore:
    """Triggers keyed by ``(owner, name)``; ``path`` None keeps them in memory only."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path is not None else None
        self._lock = threading.RLock()
        loaded = load_triggers(self.path) if self.path is not None else []
        self._triggers = {(trigger.owner, trigger.name): trigger for trigger in loaded}
        # A change kept only in memory (stats) waiting for the next flush or durable change to reach the file.
        self._dirty = False

    def list(self) -> list[Trigger]:
        """Every trigger, sorted by owner and then name."""
        with self._lock:
            return [self._triggers[key] for key in sorted(self._triggers)]

    def get(self, owner: str, name: str) -> Trigger | None:
        key = self._key(owner, name)
        with self._lock:
            return self._triggers.get(key)

    def add(self, trigger: Trigger) -> None:
        _validate_trigger(trigger)
        key = (trigger.owner, trigger.name)
        with self._lock:
            if key in self._triggers:
                raise ValueError(f"Trigger {trigger.owner}/{trigger.name} already exists")
            self._commit({**self._triggers, key: trigger})

    def replace(self, trigger: Trigger) -> None:
        _validate_trigger(trigger)
        key = (trigger.owner, trigger.name)
        with self._lock:
            if key not in self._triggers:
                raise ValueError(f"Trigger {trigger.owner}/{trigger.name} does not exist")
            self._commit({**self._triggers, key: trigger})

    def remove(self, owner: str, name: str) -> bool:
        key = self._key(owner, name)
        with self._lock:
            if key not in self._triggers:
                return False
            remaining = dict(self._triggers)
            del remaining[key]
            self._commit(remaining)
            return True

    def update(self, owner: str, name: str, /, *, durable: bool = True, **changes) -> Trigger:
        """Change a trigger; ``durable=False`` keeps the change in memory until ``flush`` or the next durable one."""
        key = self._key(owner, name)
        with self._lock:
            current = self._triggers.get(key)
            if current is None:
                raise ValueError(f"Trigger {owner}/{name} does not exist")
            updated = replace(current, **changes)
            _validate_trigger(updated)
            new_key = (updated.owner, updated.name)
            if new_key != key and new_key in self._triggers:
                raise ValueError(f"Trigger {updated.owner}/{updated.name} already exists")
            remaining = dict(self._triggers)
            del remaining[key]
            remaining[new_key] = updated
            if durable:
                self._commit(remaining)
            else:
                self._triggers = remaining
                self._dirty = True
            return updated

    def flush(self) -> None:
        """Write changes kept in memory, if any."""
        with self._lock:
            if self._dirty:
                self._commit(dict(self._triggers))

    def count_armed(self, owner: str) -> int:
        _validate_owner(owner)
        with self._lock:
            return sum(
                1
                for trigger in self._triggers.values()
                if trigger.owner == owner and trigger.status == "armed"
            )

    def _key(self, owner: str, name: str) -> tuple[str, str]:
        _validate_owner(owner)
        _validate_name(name)
        return owner, name

    def _commit(self, triggers: dict[tuple[str, str], Trigger]) -> None:
        if self.path is not None:
            self._save(triggers)
        self._triggers = triggers
        self._dirty = False

    def _save(self, triggers: dict[tuple[str, str], Trigger]) -> None:
        if self.path.is_symlink():
            raise ValueError("Triggers must not be a symlink")
        document = {
            "schemaVersion": SCHEMA_VERSION,
            "triggers": [_trigger_json(triggers[key]) for key in sorted(triggers)],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".triggers-", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(document, stream, indent=2, ensure_ascii=False)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            Path(temporary).unlink(missing_ok=True)
