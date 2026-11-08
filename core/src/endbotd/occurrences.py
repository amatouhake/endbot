"""endbotd's event fan-out (BOT-022, EVENTS.md E4).

Watches and triggers subscribe here with an event kind and a filter. The hub
keeps one interest set, the union of what every subscription needs from the
plugin, and sends it whenever it changes and after every new plugin link.
Occurrences arrive without an owner; the hub matches each against the
subscriptions of its kind and hands a copy to every one that matches.

Gaps: a source is in a gap from a link drop, a new epoch, a report with
``dropped > 0``, or a restart of the source, until its next report. Every
subscription fed by that source is told, once per gap, so a waiting run can
say ``source restarted``. Occurrences during a gap are never replayed (S4).

Names: an *event kind* is a registry entry, an *occurrence* one delivered
instance; ``endbot_core.events.Event`` stays the Event log entry.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

from endbot_core import areas
from endbot_core.i18n import LocalizedError

DEFAULT_MAX_WATCHED_BLOCKS = 4096


class Subscription:
    """One watch's or trigger's interest in one kind; delivery goes through its callbacks."""

    __slots__ = ("active", "cells", "dimension", "filter", "kind", "on_gap", "on_item", "source")

    def __init__(self, kind: str, source: str, filter_: dict, cells: frozenset, dimension: str | None,
                 on_item: Callable[[dict], None], on_gap: Callable[[str], None]) -> None:
        self.kind = kind
        self.source = source
        self.filter = filter_
        self.cells = cells
        self.dimension = dimension
        self.on_item = on_item
        self.on_gap = on_gap
        self.active = True


def _area_filters(spec: dict) -> list[str]:
    return [name for name, field in spec["filters"].items() if field["type"] == "area"]


def _dimension(spec: dict, filter_: dict) -> str | None:
    for name, field in spec["filters"].items():
        if field["type"] == "dimension":
            value = filter_.get(name, field.get("default"))
            return value if isinstance(value, str) else None
    return None


def resolve_filter(spec: dict, filter_: dict) -> dict:
    """The filter with every area resolved to integer cells; relative areas are refused (S7)."""
    resolved = dict(filter_)
    for name in _area_filters(spec):
        area = filter_.get(name)
        if area is None:
            continue
        if areas.has_relative(area):
            raise LocalizedError("A watched area needs absolute coordinates; build it from a position")
        if area.get("within") is None:
            raise LocalizedError("A watched area needs a shape")
        resolved[name] = areas.resolve_area(area, [0, 0, 0])
    return resolved


def area_cells(spec: dict, filter_: dict) -> frozenset:
    """The cells a resolved filter watches, refused over the kind's ``maxBlocks`` (S7)."""
    cells = set()
    for name in _area_filters(spec):
        area = filter_.get(name)
        if area is None:
            continue
        found = set(areas.cells(area))
        limit = spec["filters"][name].get("maxBlocks")
        if limit is not None and len(found) > limit:
            raise LocalizedError("A watched area may hold at most {limit} blocks; this one holds {count}",
                                 limit=limit, count=len(found))
        cells |= found
    return frozenset(cells)


def matches(spec: dict, subscription: Subscription, item: dict) -> bool:
    """Whether one occurrence passes a subscription's filter."""
    if subscription.dimension is not None and item.get("dimension", subscription.dimension) != subscription.dimension:
        return False
    for name, value in subscription.filter.items():
        field = spec["filters"].get(name)
        if field is None:
            return False
        kind = field["type"]
        if kind == "dimension":
            continue  # compared above, with the filter's default
        if kind == "area":
            position = (item.get("x"), item.get("y"), item.get("z"))
            if not all(type(value_) is int for value_ in position) or not areas.contains(value, position):
                return False
        elif item.get(name) != value:
            return False
    return True


class OccurrenceHub:
    """Subscriptions, the interest set and gap tracking for every event source."""

    def __init__(self, kinds: dict, send_interest: Callable[[dict], object], *,
                 max_watched_blocks: int = DEFAULT_MAX_WATCHED_BLOCKS,
                 log: Callable[[str, str], None] | None = None) -> None:
        self._kinds = kinds
        self._send_interest = send_interest
        self._max_watched_blocks = max_watched_blocks
        self._log = log or (lambda level, message: None)
        self._lock = threading.Lock()
        self._subscriptions: dict[str, list[Subscription]] = {}
        self._revision = 0
        self._epoch: str | None = None
        self._unknown: set[str] = set()

    # -- subscriptions ---------------------------------------------------

    def subscribe(self, kind: str, filter_: dict, on_item: Callable[[dict], None],
                  on_gap: Callable[[str], None]) -> Subscription:
        """Start delivering ``kind`` occurrences that pass ``filter_``; filters are validated by the caller."""
        spec = self._kinds.get(kind)
        if spec is None:
            raise LocalizedError("Unknown event kind {kind}", kind=kind)
        filter_ = resolve_filter(spec, filter_)
        cells = area_cells(spec, filter_)
        dimension = _dimension(spec, filter_)
        subscription = Subscription(kind, spec["source"], filter_, cells, dimension, on_item, on_gap)
        with self._lock:
            if cells:
                watched = self._watched_cells()
                total = len(watched | {(dimension, *cell) for cell in cells})
                if total > self._max_watched_blocks:
                    raise LocalizedError(
                        "The server watches at most {limit} blocks; this watch would make it {count}",
                        limit=self._max_watched_blocks, count=total)
            self._subscriptions.setdefault(kind, []).append(subscription)
            interest = self._next_interest() if spec["source"] == "plugin" else None
        if interest is not None:
            self._send_interest(interest)
        return subscription

    def unsubscribe(self, subscription: Subscription) -> None:
        with self._lock:
            subscription.active = False
            listed = self._subscriptions.get(subscription.kind, [])
            if subscription not in listed:
                return
            listed.remove(subscription)
            if not listed:
                del self._subscriptions[subscription.kind]
            interest = self._next_interest() if subscription.source == "plugin" else None
        if interest is not None:
            self._send_interest(interest)

    def _watched_cells(self) -> set:
        return {(subscription.dimension, *cell) for listed in self._subscriptions.values()
                for subscription in listed for cell in subscription.cells}

    def watched_blocks(self) -> int:
        with self._lock:
            return len(self._watched_cells())

    def _next_interest(self) -> dict:
        self._revision += 1
        return self._interest()

    def _interest(self) -> dict:
        by_dimension: dict[str, set] = {}
        for listed in self._subscriptions.values():
            for subscription in listed:
                if subscription.source == "plugin" and subscription.cells:
                    by_dimension.setdefault(subscription.dimension or "overworld", set()).update(subscription.cells)
        kinds = sorted(kind for kind, listed in self._subscriptions.items()
                       if listed and listed[0].source == "plugin")
        return {"revision": self._revision,
                "block": [{"dimension": dimension, "cells": [list(cell) for cell in sorted(cells)]}
                          for dimension, cells in sorted(by_dimension.items())],
                "kinds": kinds}

    def interest(self) -> dict:
        with self._lock:
            return self._interest()

    # -- sources ---------------------------------------------------------

    def link_changed(self, epoch: str | None) -> None:
        """A plugin linked (its epoch) or went away (None): a gap for plugin-fed subscriptions, then the set."""
        with self._lock:
            previous, self._epoch = self._epoch, epoch
            interest = self._interest() if epoch is not None else None
        if epoch is None or (previous is not None and previous != epoch):
            self.gap("plugin", "source restarted")
        if interest is not None:
            self._send_interest(interest)

    def accept(self, events: dict) -> int:
        """One validated ``events`` frame from the plugin; returns how many deliveries it made."""
        if events["dropped"]:
            self.gap("plugin", "occurrences dropped")
        delivered = 0
        for item in events["items"]:
            delivered += self.raise_occurrence("plugin", item)
        return delivered

    def raise_occurrence(self, source: str, item: dict) -> int:
        """Deliver one occurrence of a registry kind from ``source``; unknown kinds are logged once and dropped."""
        kind = item.get("kind")
        spec = self._kinds.get(kind)
        if spec is None or spec["source"] != source:
            if kind not in self._unknown:
                self._unknown.add(kind)
                self._log("warning", f"ignored an occurrence of {kind!r} from {source}")
            return 0
        with self._lock:
            listed = list(self._subscriptions.get(kind, ()))
        delivered = 0
        for subscription in listed:
            if subscription.active and matches(spec, subscription, item):
                subscription.on_item(dict(item))
                delivered += 1
        return delivered

    def gap(self, source: str, reason: str) -> None:
        """Tell every subscription fed by ``source`` that occurrences may have been missed."""
        with self._lock:
            listed = [subscription for subscriptions in self._subscriptions.values()
                      for subscription in subscriptions if subscription.source == source]
        for subscription in listed:
            if subscription.active:
                subscription.on_gap(reason)
