"""The plugin's ``block.change`` source (BOT-022 step 3b, EVENTS.md E3).

endbotd sends the positions it wants watched (the interest set). This source,
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
- never reports a newly watched position as changed: its first read is the
  baseline.

Occurrences carry no owner and no human player's name (S1): ``bot`` is set
only when a Bot did it. One tick's occurrences go out as one ``events`` frame.
"""

from __future__ import annotations

import math
from collections.abc import Callable

POLL_TICKS = 4
KIND = "block.change"
# Endstone dimension names to the registry's dimension values.
DIMENSION_VALUES = {"Overworld": "overworld", "Nether": "nether", "TheEnd": "the_end"}
ENDSTONE_DIMENSIONS = {value: name for name, value in DIMENSION_VALUES.items()}


class BlockChangeSource:
    """Polls watched positions and confirms player actions; every method runs on the server thread."""

    def __init__(self, server, report: Callable[[int, list[dict], int], object]) -> None:
        self._server = server
        self._report = report
        self.tick = 0
        # (dimension value, x, y, z) -> (runtime id, block type) or None while its chunk has not been read.
        self._baseline: dict[tuple, tuple | None] = {}
        self._phases: list[list[tuple]] = [[] for _ in range(POLL_TICKS)]
        self._confirm: list[tuple[int, tuple, str, str | None]] = []
        self._pending: list[dict] = []
        self._dropped = 0
        self._dimensions: set[str] = set()

    @property
    def watched(self) -> int:
        return len(self._baseline)

    def apply_interest(self, interest: dict) -> None:
        """Replace the watched positions; new ones take a baseline, removed ones are forgotten."""
        cells = set()
        if KIND in (interest.get("kinds") or ()):
            for group in interest.get("block") or ():
                dimension = group.get("dimension")
                if dimension not in ENDSTONE_DIMENSIONS:
                    continue
                for cell in group.get("cells") or ():
                    if (isinstance(cell, list) and len(cell) == 3
                            and all(type(value) is int for value in cell)):
                        cells.add((dimension, *cell))
        self._dimensions = {key[0] for key in cells}
        loaded = self._loaded() if cells - set(self._baseline) else {}
        baseline = {}
        for key in cells:
            baseline[key] = self._baseline[key] if key in self._baseline else self._read(key, loaded)
        self._baseline = baseline
        phases: list[list[tuple]] = [[] for _ in range(POLL_TICKS)]
        for index, key in enumerate(sorted(cells)):
            phases[index % POLL_TICKS].append(key)
        self._phases = phases
        self._confirm = [item for item in self._confirm if item[1] in baseline]

    def player_changed(self, block, player, cause: str) -> None:
        """A break or place event at MONITOR priority: confirm what the world holds on the next tick."""
        if not self._baseline:
            return
        dimension = DIMENSION_VALUES.get(getattr(block.dimension, "name", None))
        key = (dimension, block.x, block.y, block.z)
        if key not in self._baseline:
            return
        bot = None if player is None or str(getattr(player, "xuid", "") or "") else str(player.name)
        self._confirm.append((self.tick + 1, key, cause, bot))

    def on_tick(self) -> None:
        self.tick += 1
        if not self._baseline:
            self._confirm.clear()
            return
        loaded = self._loaded()
        due = [item for item in self._confirm if item[0] <= self.tick]
        if due:
            self._confirm = [item for item in self._confirm if item[0] > self.tick]
            for _, key, cause, bot in due:
                self._check(key, loaded, cause, bot)
        for key in self._phases[self.tick % POLL_TICKS]:
            self._check(key, loaded, "poll", None)
        if self._pending or self._dropped:
            pending, dropped = self._pending, self._dropped
            self._pending, self._dropped = [], 0
            self._report(self.tick, pending, dropped)

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

    def _check(self, key: tuple, loaded: dict, cause: str, bot: str | None) -> None:
        if key not in self._baseline:
            return
        current = self._read(key, loaded)
        if current is None:
            return  # unloaded: keep the baseline until the chunk loads again
        previous = self._baseline[key]
        self._baseline[key] = current
        if previous is None or previous[0] == current[0]:
            return
        dimension, x, y, z = key
        item = {"kind": KIND, "x": x, "y": y, "z": z, "dimension": dimension,
                "from": previous[1], "to": current[1], "cause": cause}
        if bot is not None:
            item["bot"] = bot
        self._pending.append(item)
