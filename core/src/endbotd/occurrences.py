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
import time
from collections.abc import Callable

from endbot_core import areas
from endbot_core.i18n import LocalizedError

# BLOCK_EVENTS_SPIKE.md#result: a quarter of these read per tick costs about 1 to 2 ms.
DEFAULT_MAX_WATCHED_BLOCKS = 2048
# The interest set lists every watched cell in one link frame (1 MiB); at about 25 bytes a cell this keeps it
# well inside, and is the most [events] max-watched-blocks may be set to.
MAX_WATCHED_BLOCKS = 16384
# Coordinates an occurrence may carry; anything beyond the world is not a block.
WORLD_LIMIT = 30_000_000
# Distinct unknown kinds logged before the hub stops recording them (a misbehaving source cannot grow memory).
MAX_UNKNOWN_KINDS = 32


class Subscription:
    """One watch's or trigger's interest in one kind; delivery goes through its callbacks."""

    __slots__ = ("active", "cells", "created", "dimension", "filter", "kind", "on_gap", "on_item", "source")

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
        self.created = time.perf_counter()


def _area_filters(spec: dict) -> list[str]:
    return [name for name, field in spec["filters"].items() if field["type"] == "area"]


def _dimension(spec: dict, filter_: dict) -> str | None:
    for name, field in spec["filters"].items():
        if field["type"] == "dimension":
            value = filter_.get(name, field.get("default"))
            return value if isinstance(value, str) else None
    return None


def resolve_filter(kind: str, spec: dict, filter_: dict) -> dict:
    """The filter with every area resolved to integer cells; relative areas are refused (S7)."""
    resolved = dict(filter_)
    if spec["require"] and not any(all(filter_.get(name) is not None for name in names)
                                   for names in spec["require"]):
        raise LocalizedError("{kind} needs a filter: {choices}", kind=kind,
                             choices=" or ".join(", ".join(names) for names in spec["require"]))
    for name in _area_filters(spec):
        if name not in filter_:
            continue
        area = filter_[name]
        if not isinstance(area, dict):
            raise LocalizedError("A watched area needs a shape")
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
    # Fail closed: an occurrence without a dimension matches no dimension filter.
    if subscription.dimension is not None and item.get("dimension") != subscription.dimension:
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
            if (not all(type(value_) is int and abs(value_) <= WORLD_LIMIT for value_ in position)
                    or not areas.contains(value, position)):
                return False
        elif item.get(name) != value:
            return False
    return True


class OccurrenceHub:
    """Subscriptions, the interest set and gap tracking for every event source."""

    def __init__(self, kinds: dict, send_interest: Callable[[dict], object], *,
                 max_watched_blocks: int = DEFAULT_MAX_WATCHED_BLOCKS, turned_off=(),
                 log: Callable[[str, str], None] | None = None) -> None:
        self._kinds = kinds
        self._send_interest = send_interest
        self._max_watched_blocks = max_watched_blocks
        # Kinds the operator has not turned on (player.chat without [events] chat = true): refused at subscribe.
        self._turned_off = frozenset(turned_off)
        self._log = log or (lambda level, message: None)
        self._lock = threading.Lock()
        # Held while an interest set is computed and sent, so sets reach the plugin in revision order.
        self._send_lock = threading.Lock()
        self._subscriptions: dict[str, list[Subscription]] = {}
        self._revision = 0
        self._epoch: str | None = None
        # The plugin's boot id from its last hello: a new one means BDS started again (server.started).
        self._boot: str | None = None
        self._unknown: set[str] = set()

    @property
    def kinds(self) -> dict:
        return self._kinds

    # -- subscriptions ---------------------------------------------------

    def subscribe(self, kind: str, filter_: dict, on_item: Callable[[dict], None],
                  on_gap: Callable[[str], None]) -> Subscription:
        """Start delivering ``kind`` occurrences that pass ``filter_``; filters are validated by the caller."""
        spec = self._kinds.get(kind)
        if spec is None:
            raise LocalizedError("Unknown event kind {kind}", kind=kind)
        if kind in self._turned_off:
            raise LocalizedError("{kind} is turned off on this server; the operator turns it on in [events]",
                                 kind=kind)
        filter_ = resolve_filter(kind, spec, filter_)
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
        if spec["source"] == "plugin":
            self._publish()
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
        if subscription.source == "plugin":
            self._publish()

    def _watched_cells(self) -> set:
        return {(subscription.dimension, *cell) for listed in self._subscriptions.values()
                for subscription in listed for cell in subscription.cells}

    def watched_blocks(self) -> int:
        with self._lock:
            return len(self._watched_cells())

    def has_interest(self, *kinds: str) -> bool:
        """Whether any live subscription wants one of ``kinds``; the runtime poll starts and stops with this."""
        with self._lock:
            return any(self._subscriptions.get(kind) for kind in kinds)

    def _publish(self) -> None:
        """Send the current set with a new revision; one send at a time, so a later set never arrives first."""
        with self._send_lock:
            with self._lock:
                if self._epoch is None:
                    return  # no plugin: the next link gets the set
                self._revision += 1
                interest = self._interest()
            try:
                self._send_interest(interest)
            except Exception as error:  # noqa: BLE001 - a failed send is logged; the next change or link resends
                self._log("warning", f"event interest set not sent: {type(error).__name__}: {error}")

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

    def link_changed(self, epoch: str | None, closed: str | None = None, *, boot: str | None = None) -> None:
        """A plugin linked (its epoch and boot id) or the link of epoch ``closed`` went away (None).

        A close that arrives after a newer link replaced it changes nothing. A gap is recorded for plugin-fed
        subscriptions first, then the current set is sent, so a watch opened or closed by a gap callback is in it.
        ``boot`` is given only for the first link of a plugin process: ``server.started`` is raised once for it,
        so a reconnect, or endbotd restarting under a running BDS, raises nothing (EVENTS.md E9).
        """
        with self._lock:
            if epoch is None and closed is not None and closed != self._epoch:
                return
            previous, self._epoch = self._epoch, epoch
        if epoch is not None and boot is not None and boot != self._boot:
            self._boot = boot
            self.raise_occurrence("endbotd", {"kind": "server.started"})
        if epoch is None or (previous is not None and previous != epoch):
            self.gap("plugin", "source restarted")
        if epoch is not None:
            self._publish()

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
            label = str(kind)[:64]
            if label not in self._unknown and len(self._unknown) < MAX_UNKNOWN_KINDS:
                self._unknown.add(label)
                self._log("warning", f"ignored an occurrence of {label!r} from {source}")
            return 0
        # A source that sees changes only between two samples says when the change could have begun (``_since``,
        # perf_counter seconds): a subscription made after that may have been made after the change, so it is skipped.
        since = item.pop("_since", None) if isinstance(item, dict) else None
        with self._lock:
            listed = list(self._subscriptions.get(kind, ()))
        delivered = 0
        for subscription in listed:
            if since is not None and subscription.created > since:
                continue
            if subscription.active and matches(spec, subscription, item):
                if self._call(subscription.on_item, dict(item)):
                    delivered += 1
        return delivered

    def _call(self, callback, value) -> bool:
        """One subscriber's callback; its failure is logged and never reaches the others or the link."""
        try:
            callback(value)
        except Exception as error:  # noqa: BLE001 - isolate subscribers
            self._log("warning", f"an event subscriber failed: {type(error).__name__}: {error}")
            return False
        return True

    def gap(self, source: str, reason: str) -> None:
        """Tell every subscription fed by ``source`` that occurrences may have been missed."""
        with self._lock:
            listed = [subscription for subscriptions in self._subscriptions.values()
                      for subscription in subscriptions if subscription.source == source]
        for subscription in listed:
            if subscription.active:
                self._call(subscription.on_gap, reason)
