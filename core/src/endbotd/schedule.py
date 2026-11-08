"""The ``schedule`` event kind: endbotd raises it on the wall clock (BOT-022 step 6c, EVENTS.md E4, E9).

A watch or trigger names ``every`` (an integer and ``s``, ``m`` or ``h``, from 10s to 24h,
counted from the moment the subscription was made) or ``at`` (``HH:MM`` in endbotd's local
time, fired once a day), or both. Each subscription therefore has its own due moments, while
its occurrence carries the fired local time in ``at`` rather than any filter value. The hub's
normal matcher compares an occurrence field with the filter and so cannot select the due
subscription.

Design: :class:`ScheduleTimer` keeps one schedule per live ``schedule`` subscription and, when
one is due, calls ``OccurrenceHub.deliver_to`` to deliver one occurrence to that subscription
only. That private delivery path bypasses filter matching for this kind alone;
``raise_occurrence`` and every other kind are unchanged. The alternative -- one occurrence per
due subscription with a private field the shared matcher compares -- would put a schedule-only
rule in the common matcher and expose the field to subscribers, so this is the simpler design.

The timer runs on its own daemon thread, started and stopped by ``EndbotService``. It keeps no
schedule bookkeeping while no ``schedule`` subscription exists. It checks due schedules every
``tick_seconds``; a late pass fires one occurrence per due schedule and skips the slots it
missed instead of bursting. Schedule occurrences do not depend on the plugin link: they are
raised with BDS stopped, where the trigger engine skips and counts starts (S3) while watches
still receive them.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from endbot_core.watches import parse_duration, parse_time

KIND = "schedule"
# How often a running timer checks its due schedules and how often an idle one looks for interest.
TICK_SECONDS = 0.25
IDLE_SECONDS = 0.25


@dataclass
class _Schedule:
    """One subscription's due moments; ``every`` and ``at`` may both be set."""

    subscription: object
    every: int | None = None
    next_every: float | None = None
    at: tuple[int, int] | None = None
    next_at: datetime | None = None


def next_time(now: datetime, hour: int, minute: int) -> datetime:
    """The first local ``hour:minute`` strictly after ``now`` (seconds and below dropped)."""
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return candidate if candidate > now else candidate + timedelta(days=1)


def _iso(seconds: float) -> str:
    """A due moment as ISO 8601 local time with seconds precision."""
    return datetime.fromtimestamp(seconds).isoformat(timespec="seconds")


class ScheduleTimer:
    """Raises ``schedule`` occurrences for the subscriptions that are due."""

    def __init__(self, hub, *, clock: Callable[[], float] = time.time,
                 tick_seconds: float = TICK_SECONDS, idle_seconds: float = IDLE_SECONDS,
                 log: Callable[[str, str], None] | None = None) -> None:
        self._hub = hub
        self._clock = clock
        self._tick_seconds = tick_seconds
        self._idle_seconds = idle_seconds
        self._log = log or (lambda level, message: None)
        # Subscription -> its schedule. Subscription has identity equality and hashing.
        self._schedules: dict[object, _Schedule] = {}
        self._closed = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def scheduled(self) -> int:
        """How many subscriptions the timer is following."""
        return len(self._schedules)

    def start(self) -> ScheduleTimer:
        self._thread = threading.Thread(target=self._loop, name="endbotd-schedule", daemon=True)
        self._thread.start()
        return self

    def close(self) -> None:
        self._closed.set()
        if self._thread is not None:
            self._thread.join(5)
        self._schedules.clear()

    def _loop(self) -> None:
        while not self._closed.is_set():
            try:
                self.poll()
            except Exception as error:  # noqa: BLE001 - one bad schedule must not stop the timer
                self._log("warning", f"schedule timer: {type(error).__name__}: {error}")
            wait = self._tick_seconds if self._schedules else self._idle_seconds
            if self._closed.wait(wait):
                return

    def poll(self) -> int:
        """One pass: adopt new subscriptions, fire the due schedules, forget gone subscriptions.

        Returns how many occurrences were delivered. Tests drive this directly with an injected
        clock, so no real sleeping is needed.
        """
        if not self._hub.has_interest(KIND):
            self._schedules.clear()
            return 0
        subscriptions = self._hub.subscriptions(KIND)
        live = set(subscriptions)
        for subscription in list(self._schedules):
            if subscription not in live:
                del self._schedules[subscription]
        now = self._clock()
        delivered = 0
        for subscription in subscriptions:
            schedule = self._schedules.get(subscription)
            if schedule is None:
                schedule = self._adopt(subscription, now)
                if schedule is None:
                    continue
                self._schedules[subscription] = schedule
            delivered += self._fire(schedule, now)
        return delivered

    def _adopt(self, subscription, now: float) -> _Schedule | None:
        filter_ = subscription.filter
        every = at = None
        if "every" in filter_:
            try:
                every = parse_duration(filter_["every"])
            except ValueError:
                self._log("warning", f"schedule watcher has an invalid every filter: "
                                     f"{str(filter_['every'])[:64]!r}")
        if "at" in filter_:
            try:
                at = parse_time(filter_["at"])
            except ValueError:
                self._log("warning", f"schedule watcher has an invalid at filter: "
                                     f"{str(filter_['at'])[:64]!r}")
        if every is None and at is None:
            return None
        schedule = _Schedule(subscription, every=every, at=at)
        if every is not None:
            schedule.next_every = now + every
        if at is not None:
            schedule.next_at = next_time(datetime.fromtimestamp(now), *at)
        return schedule

    def _fire(self, schedule: _Schedule, now: float) -> int:
        delivered = 0
        if schedule.next_every is not None and now >= schedule.next_every:
            delivered += self._hub.deliver_to(schedule.subscription,
                                              {"kind": KIND, "at": _iso(schedule.next_every)})
            # Skip slots a late pass missed rather than starting a burst of runs.
            while schedule.next_every <= now:
                schedule.next_every += schedule.every
        if schedule.next_at is not None and now >= schedule.next_at.timestamp():
            delivered += self._hub.deliver_to(schedule.subscription,
                                              {"kind": KIND, "at": schedule.next_at.isoformat(timespec="seconds")})
            while schedule.next_at.timestamp() <= now:
                schedule.next_at += timedelta(days=1)
        return delivered
