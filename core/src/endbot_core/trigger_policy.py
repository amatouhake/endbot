"""The per-trigger policy state machine (BOT-022 step 5a, docs/EVENTS.md E8, E9).

Time is injected: ``now`` is monotonic seconds and cooldown ticks are converted at
20 per second. The class holds no clock, thread or I/O, so the E8 table is tested
with explicit times and a restored ``last_start`` can hold the cooldown after an
endbotd restart while the minute window starts empty.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass

from endbot_core.trigger_store import policy_from

TICKS_PER_SECOND = 20.0
WINDOW_SECONDS = 60.0
MAX_PARALLEL = 2
RATE_LIMIT = "rate limit"


@dataclass(frozen=True, slots=True)
class Decision:
    """What happened to one occurrence; ``missed`` is reported only with a start."""

    action: str
    missed: int = 0
    reason: str | None = None


class TriggerPolicy:
    """One trigger's concurrency, cooldown and start-rate state."""

    def __init__(self, policy: dict, last_start: float | None = None) -> None:
        self._policy = policy_from(policy)
        if last_start is not None and (
            type(last_start) not in (int, float) or not math.isfinite(last_start)
        ):
            raise ValueError("Trigger last_start must be a finite number or null")
        self._concurrency = self._policy["concurrency"]
        self._cooldown = self._policy["cooldown"] / TICKS_PER_SECOND
        self._starts_per_minute = self._policy["startsPerMinute"]
        self._queue_limit = self._policy["queue"]
        self._active = 0
        self._pending: deque[float] = deque()
        self._missed_total = 0
        self._missed_since_start = 0
        self._last_start = last_start
        self._starts: deque[float] = deque()
        self._disabled = False

    @property
    def active(self) -> int:
        """How many runs from this trigger are running."""
        return self._active

    @property
    def pending(self) -> int:
        """How many occurrences are queued."""
        return len(self._pending)

    @property
    def missed_total(self) -> int:
        """Every occurrence missed since this policy was created."""
        return self._missed_total

    def carry_from(self, other: TriggerPolicy) -> TriggerPolicy:
        """Keep what ``other`` knew (runs still active, the start window, queued and missed occurrences) under this
        policy's settings, so changing or re-enabling a trigger never resets its start cap; a disable is not kept."""
        self._active = other._active
        self._starts = deque(other._starts)
        self._pending = deque(list(other._pending)[-self._queue_limit:]) if self._concurrency == "queue" else deque()
        self._missed_total = other._missed_total
        self._missed_since_start = other._missed_since_start
        if other._last_start is not None and (self._last_start is None or other._last_start > self._last_start):
            self._last_start = other._last_start
        return self

    def offer(self, now: float) -> Decision:
        """An occurrence arrived; decide whether to start, queue, drop or disable."""
        if self._disabled:
            return Decision("disable", reason=RATE_LIMIT)
        if self._concurrency == "queue":
            if self._pending or not self._available(now):
                self._enqueue(now)
                return Decision("queued")
            return self._start(now)
        if not self._available(now):
            self._drop()
            return Decision("missed")
        return self._start(now)

    def started(self, now: float) -> None:
        """The caller started a run for a ``start`` decision."""
        self._active += 1
        self._last_start = now
        self._starts.append(now)

    def finished(self, now: float) -> None:
        """One run from this trigger ended."""
        if self._active > 0:
            self._active -= 1

    def due(self, now: float) -> Decision | None:
        """Start the oldest queued occurrence when concurrency and cooldown allow it."""
        if self._disabled or self._concurrency != "queue" or not self._pending:
            return None
        if not self._available(now):
            return None
        if self._rate_limited(now):
            self._disabled = True
            return Decision("disable", reason=RATE_LIMIT)
        self._pending.popleft()
        return self._start_decision()

    def skipped_down(self) -> None:
        """An occurrence arrived while the link or BDS was down (S3): count it as missed."""
        self._drop()

    def _available(self, now: float) -> bool:
        limit = MAX_PARALLEL if self._concurrency == "parallel" else 1
        if self._active >= limit:
            return False
        return self._last_start is None or now - self._last_start >= self._cooldown

    def _rate_limited(self, now: float) -> bool:
        while self._starts and now - self._starts[0] >= WINDOW_SECONDS:
            self._starts.popleft()
        return len(self._starts) >= self._starts_per_minute

    def _start(self, now: float) -> Decision:
        if self._rate_limited(now):
            self._disabled = True
            return Decision("disable", reason=RATE_LIMIT)
        return self._start_decision()

    def _start_decision(self) -> Decision:
        missed = self._missed_since_start
        self._missed_since_start = 0
        return Decision("start", missed=missed)

    def _enqueue(self, now: float) -> None:
        if len(self._pending) >= self._queue_limit:
            self._pending.popleft()
            self._drop()
        self._pending.append(now)

    def _drop(self) -> None:
        self._missed_total += 1
        self._missed_since_start += 1
