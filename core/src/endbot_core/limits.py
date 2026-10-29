"""Bot slot budget: how many Bots may join while keeping room for players.

Operator-facing configuration validation lives here, outside the player-error
modules, so its English messages stay untranslated (like ``script_config.py``).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BotLimits:
    """How many Bots may join, keeping server slots free for players.

    ``max_bots`` 0 means "no separate Bot cap" (server capacity still applies);
    a positive value caps online/connecting Bots. ``reserved_player_slots``
    keeps that many slots free for players. Both 0 restores the previous
    behavior of no limit at all.
    """

    max_bots: int = 0
    reserved_player_slots: int = 1

    @classmethod
    def from_config(cls, table: object) -> BotLimits:
        if table is None:
            return cls()
        if not isinstance(table, dict):
            raise ValueError("[limits] must be a table")  # noqa: TRY004 - configuration validation API
        unknown = set(table) - {"max-bots", "reserved-player-slots"}
        if unknown:
            raise ValueError(f"unknown [limits] key: {min(unknown)}")

        def whole(key: str, default: int) -> int:
            value = table.get(key, default)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"[limits] {key} must be an integer")  # noqa: TRY004 - configuration validation API
            return value

        max_bots = whole("max-bots", 0)
        reserved = whole("reserved-player-slots", 1)
        if max_bots < 0:
            raise ValueError("[limits] max-bots must be 0 or a positive integer")
        if reserved < 0:
            raise ValueError("[limits] reserved-player-slots must be 0 or more")
        return cls(max_bots, reserved)

    @property
    def enabled(self) -> bool:
        return self.max_bots != 0 or self.reserved_player_slots > 0
