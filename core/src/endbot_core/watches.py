"""Watches: a run waits for occurrences of an event kind (BOT-022 step 4, EVENTS.md E5).

``validate_filter`` checks a script's ``watch(kind, filter)`` against the
registry before anything is subscribed: the kind exists, every filter name and
value fits its declared type, one ``require`` set is complete, and the kind's
capability is basic or in the run's ``requires {}``. Errors name the field or
the capability.

``WatchBuffer`` is what endbotd's fan-out delivers into. Buffering starts when
the watch is created, not at the first wait, so nothing is lost between waits;
it keeps at most ``BUFFER_LIMIT`` occurrences, dropping the oldest and counting
the drops in ``missed``. A gap (the source restarted or dropped occurrences)
makes the next wait return once ``ok = false, code = "unavailable"`` with the
reason, then waiting continues; a script that cannot tolerate a gap re-reads
with ``block()``.
"""

from __future__ import annotations

import re
import threading
import time
from collections import deque
from collections.abc import Callable

from endbot_core import areas
from endbot_core.i18n import LocalizedError
from endbot_core.operations import EVENTS
from endbot_core.script_params import BASIC_CAPABILITIES

BUFFER_LIMIT = 64
MAX_WATCHES_PER_RUN = 16
DIMENSIONS = ("overworld", "nether", "end")
BLOCK = re.compile(r"(?:[a-z0-9_.-]+:)?[a-z0-9_./-]+")
NAME = re.compile(r"[A-Za-z0-9_ ]{1,32}")


def _block(value) -> str | None:
    if not isinstance(value, str) or not BLOCK.fullmatch(value.lower()):
        return None
    value = value.lower()
    return value if ":" in value else f"minecraft:{value}"


def _value(name: str, field: dict, value):
    """One filter value in canonical form; LocalizedError names the field when it does not fit."""
    kind = field["type"]
    if kind == "area":
        if not isinstance(value, str) or not value.startswith("within "):
            raise LocalizedError("Watch filter {field} must be an area (area.cell, area.box or area.sphere)",
                                 field=name)
        try:
            return areas.parse_area(value)
        except LocalizedError as error:
            raise LocalizedError("Watch filter {field}: {reason}", field=name, reason=str(error)) from None
    if kind == "block":
        block = _block(value)
        if block is None:
            raise LocalizedError("Watch filter {field} must be a block name", field=name)
        return block
    if kind == "dimension":
        if value not in DIMENSIONS:
            raise LocalizedError("Watch filter {field} must be one of {choices}", field=name,
                                 choices=", ".join(DIMENSIONS))
        return value
    if kind in ("bot", "player"):
        if not isinstance(value, str) or not NAME.fullmatch(value):
            raise LocalizedError("Watch filter {field} must be a name", field=name)
        return value
    if kind == "integer":
        if type(value) not in (int, float) or value != int(value) or abs(value) > 2147483647:
            raise LocalizedError("Watch filter {field} must be a whole number", field=name)
        return int(value)
    if kind in ("string", "duration", "time"):
        if not isinstance(value, str) or not 0 < len(value) <= 64:
            raise LocalizedError("Watch filter {field} must be text", field=name)
        return value
    raise LocalizedError("Watch filter {field} has an unknown type", field=name)


def check_kind(kind: object, requires, kinds: dict | None = None) -> dict:
    """The registry entry of ``kind``, refused when unknown or when its capability is not declared."""
    kinds = EVENTS if kinds is None else kinds
    if not isinstance(kind, str) or kind not in kinds:
        raise LocalizedError("Unknown event kind {kind}; known kinds: {known}", kind=str(kind)[:64],
                             known=", ".join(sorted(kinds)) or "none")
    spec = kinds[kind]
    capability = spec["capability"]
    if capability not in BASIC_CAPABILITIES and capability not in (requires or ()):
        raise LocalizedError("Watching {kind} needs {capability} in requires {{}}", kind=kind, capability=capability)
    return spec


def validate_filter(kind: object, filter_: object, requires, kinds: dict | None = None) -> tuple[str, dict]:
    """The canonical (kind, filter) for a watch or trigger; LocalizedError names what is wrong."""
    spec = check_kind(kind, requires, kinds)
    if filter_ is None:
        filter_ = {}
    if not isinstance(filter_, dict):
        raise LocalizedError("A watch filter must be a table")
    canonical = {}
    for name, value in filter_.items():
        field = spec["filters"].get(name)
        if field is None:
            raise LocalizedError("{kind} has no filter {field}; filters: {known}", kind=kind, field=str(name)[:32],
                                 known=", ".join(sorted(spec["filters"])))
        canonical[name] = _value(name, field, value)
    if spec["require"] and not any(all(name in canonical for name in names) for names in spec["require"]):
        raise LocalizedError("{kind} needs a filter: {choices}", kind=kind,
                             choices=" or ".join(", ".join(names) for names in spec["require"]))
    return kind, canonical


class WatchBuffer:
    """Occurrences for one watch, from the fan-out thread to the run's wait."""

    def __init__(self, kind: str, limit: int = BUFFER_LIMIT) -> None:
        self.kind = kind
        self._limit = limit
        self._items: deque = deque()
        self._missed = 0
        self._gap: str | None = None
        self._ready = threading.Condition()

    def on_item(self, item: dict) -> None:
        with self._ready:
            if len(self._items) >= self._limit:
                self._items.popleft()
                self._missed += 1
            self._items.append(item)
            self._ready.notify_all()

    def on_gap(self, reason: str) -> None:
        with self._ready:
            self._gap = reason
            self._ready.notify_all()

    def wait(self, seconds: float, alive: Callable[[], None], slice_seconds: float = 0.1) -> dict:
        """The next occurrence, the pending gap, or a timeout after ``seconds``; ``alive`` raises to stop."""
        end = time.monotonic() + seconds
        while True:
            with self._ready:
                if self._gap is not None:
                    reason, self._gap = self._gap, None
                    return {"ok": False, "code": "unavailable", "reason": reason, "missed": self._missed}
                if self._items:
                    item = self._items.popleft()
                    missed, self._missed = self._missed, 0
                    return {"ok": True, "code": "ok", "reason": "occurred", "data": item, "missed": missed}
                remaining = end - time.monotonic()
                if remaining <= 0:
                    return {"ok": False, "code": "timeout", "reason": "timeout", "missed": self._missed}
                self._ready.wait(min(slice_seconds, remaining))
            alive()
