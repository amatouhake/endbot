"""The plugin's ``block.change``, ``bot.died`` and ``world.time`` source (BOT-022 steps 3b, 6b, 6c; EVENTS.md E3, E4).

endbotd sends the positions and kinds it wants watched (the interest set). This source,
on the server thread only:

- reads every watched position once every ``POLL_TICKS`` ticks, a quarter of
  them on each tick so the cost is spread evenly, and reports a difference from
  the last value it saw with cause ``poll`` (BLOCK_EVENTS_SPIKE.md#result);
- for a player breaking or placing a block at a watched position, reads it on
  the next tick and reports it with cause ``player-break`` or ``player-place``
  only if the block really changed, so a cancelled action is never reported;
- skips positions whose chunk is not loaded (an unloaded read returns air) and
  keeps their baseline, so a difference is reported late, as ``poll``, once the
  chunk loads again;
- never reports a newly watched position as changed: its first read, in its
  own quarter, is the baseline, so a large new interest set costs no more per
  tick than the poll does; a player action there before that read is still
  reported, from the block the event saw;
- when ``bot.died`` is wanted, reports a death of a player without an XUID (a
  Bot) with its death message key. A human's death is never reported;
- when ``world.time`` is wanted, reads ``level.time`` every ``TIME_POLL_TICKS``
  ticks and reports the phase of the day cycle when it changes. The first read
  of a new interest is the baseline, so only later changes are reported; the
  phase lasts at least 1000 ticks, well over the poll period.

Occurrences carry no owner and no human player's name (S1): ``bot`` is set
only when a Bot did it. One tick's occurrences go out as one ``events`` frame.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable

POLL_TICKS = 4
KIND = "block.change"
DEATH_KIND = "bot.died"
TIME_KIND = "world.time"
# 20 ticks: every phase lasts at least 1000 ticks, so a change is never missed.
TIME_POLL_TICKS = 20
# Endstone dimension names to the registry's dimension values.
DIMENSION_VALUES = {"Overworld": "overworld", "Nether": "nether", "TheEnd": "end"}
ENDSTONE_DIMENSIONS = {value: name for name, value in DIMENSION_VALUES.items()}
# Presence and public chat (step 6a): reported only for human players and only while endbotd asks for them.
PRESENCE_KINDS = frozenset({"player.join", "player.leave", "player.chat"})
CHAT_LIMIT = 256
CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def phase_at(time_of_day: int) -> str:
    """The day-cycle phase of a time of day 0-23999: day 0-11999, sunset, night, sunrise."""
    if time_of_day < 12000:
        return "day"
    if time_of_day < 13000:
        return "sunset"
    if time_of_day < 23000:
        return "night"
    return "sunrise"


class BlockChangeSource:
    """Polls watched positions, confirms player actions and reports Bot deaths; server thread only."""

    def __init__(self, server, report: Callable[[int, list[dict], int], object]) -> None:
        self._server = server
        self._report = report
        self.tick = 0
        # The interest set's kinds: block.change drives the poll, bot.died the death listener.
        self._kinds: set[str] = set()
        # (dimension value, x, y, z) -> (runtime id, block type) or None while its chunk has not been read.
        self._baseline: dict[tuple, tuple | None] = {}
        self._phases: list[list[tuple]] = [[] for _ in range(POLL_TICKS)]
        self._confirm: list[tuple[int, tuple, str, str | None, str | None]] = []
        self._pending: list[dict] = []
        self._dropped = 0
        self._dimensions: set[str] = set()
        # The last day-cycle phase read for world.time; None until a read is the baseline.
        self._time_phase: str | None = None

    @property
    def watched(self) -> int:
        return len(self._baseline)

    def apply_interest(self, interest: dict) -> None:
        """Replace the watched positions and kinds; new positions take a baseline, removed ones are forgotten."""
        kinds = interest.get("kinds") or ()
        previous, self._kinds = self._kinds, {kind for kind in kinds if isinstance(kind, str)}
        if TIME_KIND not in self._kinds or TIME_KIND not in previous:
            self._time_phase = None  # a new interest needs a fresh baseline
        # Occurrences of kinds nobody asks for any more are not sent (block changes are checked by position).
        self._pending = [item for item in self._pending if item.get("kind") == KIND or item.get("kind") in self._kinds]
        cells = set()
        if KIND in self._kinds:
            for group in interest.get("block") or ():
                dimension = group.get("dimension")
                if dimension not in ENDSTONE_DIMENSIONS:
                    continue
                for cell in group.get("cells") or ():
                    if (isinstance(cell, list) and len(cell) == 3
                            and all(type(value) is int for value in cell)):
                        cells.add((dimension, *cell))
        if cells == set(self._baseline):
            return  # the same positions (a resent set after a relink): keep baselines and phases
        self._dimensions = {key[0] for key in cells}
        # New positions get their baseline from their first phase read, never all at once on this tick.
        self._baseline = {key: self._baseline.get(key) for key in cells}
        phases: list[list[tuple]] = [[] for _ in range(POLL_TICKS)]
        for index, key in enumerate(sorted(cells)):
            phases[index % POLL_TICKS].append(key)
        self._phases = phases
        self._confirm = [item for item in self._confirm if item[1] in self._baseline]

    def player_changed(self, block, player, cause: str) -> None:
        """A break or place event at MONITOR priority: confirm what the world holds on the next tick."""
        if not self._baseline:
            return
        dimension = DIMENSION_VALUES.get(getattr(block.dimension, "name", None))
        key = (dimension, block.x, block.y, block.z)
        if key not in self._baseline:
            return
        bot = None if player is None or str(getattr(player, "xuid", "") or "") else str(player.name)
        # What the event saw before the action: the `from` when the position had no baseline read yet.
        before = getattr(block, "type", None)
        self._confirm.append((self.tick + 1, key, cause, bot, before if isinstance(before, str) else None))

    def player_presence(self, kind: str, player, message: str | None = None) -> None:
        """A human player joined, left or chatted publicly; Bots are left out (bot.* kinds cover them)."""
        if kind not in self._kinds or kind not in PRESENCE_KINDS or player is None:
            return
        if not str(getattr(player, "xuid", "") or ""):
            return
        item = {"kind": kind, "player": str(player.name)}
        if kind == "player.chat":
            item["message"] = CONTROL.sub(" ", str(message or ""))[:CHAT_LIMIT]
        self._pending.append(item)

    def player_died(self, player, message: str) -> None:
        """A PlayerDeathEvent at MONITOR priority: report it only for a Bot and only when bot.died is wanted."""
        if DEATH_KIND not in self._kinds or player is None:
            return
        if str(getattr(player, "xuid", "") or ""):
            return  # A human's death is never reported (BLOCK_EVENTS_SPIKE.md#presence-chat-and-death).
        name = str(getattr(player, "name", "") or "")
        if name:
            self._pending.append({"kind": DEATH_KIND, "bot": name, "message": str(message)})

    def on_tick(self) -> None:
        self.tick += 1
        if TIME_KIND in self._kinds and self.tick % TIME_POLL_TICKS == 0:
            self._check_time()
        if self._baseline:
            self._poll()
        else:
            self._confirm.clear()
        if self._pending or self._dropped:
            pending, dropped = self._pending, self._dropped
            self._pending, self._dropped = [], 0
            self._report(self.tick, pending, dropped)

    def _check_time(self) -> None:
        """Read the level's time of day and report a phase change after the baseline read."""
        try:
            time_of_day = int(self._server.level.time) % 24000
        except (AttributeError, TypeError, ValueError, RuntimeError):
            return
        phase = phase_at(time_of_day)
        if self._time_phase is None:
            self._time_phase = phase  # the first read of a new interest is the baseline
            return
        if phase == self._time_phase:
            return
        self._time_phase = phase
        self._pending.append({"kind": TIME_KIND, "phase": phase, "time": time_of_day})

    def _poll(self) -> None:
        due = [item for item in self._confirm if item[0] <= self.tick]
        phase = self._phases[self.tick % POLL_TICKS]
        if not (due or phase):
            return
        loaded = self._loaded()  # only on ticks that read something
        if due:
            self._confirm = [item for item in self._confirm if item[0] > self.tick]
            for _, key, cause, bot, before in due:
                self._check(key, loaded, cause, bot, before)
        for key in phase:
            self._check(key, loaded, "poll", None)

    def _loaded(self) -> dict[str, set]:
        loaded = {}
        for value in self._dimensions:
            name = ENDSTONE_DIMENSIONS[value]
            try:
                dimension = self._server.level.get_dimension(name)
                loaded[value] = (dimension, {(chunk.x, chunk.z) for chunk in dimension.loaded_chunks})
            except (AttributeError, RuntimeError, ValueError):
                continue
        return loaded

    def _read(self, key: tuple, loaded: dict) -> tuple | None:
        dimension_value, x, y, z = key
        entry = loaded.get(dimension_value)
        if entry is None or (math.floor(x / 16), math.floor(z / 16)) not in entry[1]:
            return None
        block = entry[0].get_block_at(x, y, z)
        return block.data.runtime_id, block.type

    def _check(self, key: tuple, loaded: dict, cause: str, bot: str | None, before: str | None = None) -> None:
        if key not in self._baseline:
            return
        current = self._read(key, loaded)
        if current is None:
            return  # unloaded: keep the baseline until the chunk loads again
        previous = self._baseline[key]
        self._baseline[key] = current
        if previous is None:
            # No baseline read yet: a poll only takes one; a confirmed player action reports from what it saw.
            if before is None or before == current[1]:
                return
            source = before
        elif previous[0] == current[0]:
            return
        else:
            source = previous[1]
        dimension, x, y, z = key
        item = {"kind": KIND, "x": x, "y": y, "z": z, "dimension": dimension,
                "from": source, "to": current[1], "cause": cause}
        if bot is not None:
            item["bot"] = bot
        self._pending.append(item)
