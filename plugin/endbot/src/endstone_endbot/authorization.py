"""Fail-closed player identity allowlist for the M0 control command."""

from dataclasses import dataclass, field
from uuid import UUID


@dataclass(frozen=True, slots=True)
class AllowedPlayers:
    uuids: frozenset[str] = field(default_factory=frozenset)
    xuids: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def from_config(cls, config: dict) -> "AllowedPlayers":
        configured_uuids = config.get("allowed-uuids", [])
        configured_xuids = config.get("allowed-xuids", [])
        if not isinstance(configured_uuids, list) or not isinstance(configured_xuids, list):
            raise TypeError("authorization allowlists must be arrays")

        uuids: set[str] = set()
        for value in configured_uuids:
            if not isinstance(value, str):
                raise TypeError("allowed UUIDs must be strings")
            uuids.add(str(UUID(value)))

        xuids: set[str] = set()
        for value in configured_xuids:
            if not isinstance(value, str):
                raise TypeError("allowed XUIDs must be strings")
            if not value.isascii() or not value.isdecimal():
                raise ValueError("allowed XUIDs must be decimal strings")
            xuids.add(value)
        return cls(frozenset(uuids), frozenset(xuids))

    def allows(self, player_uuid: str, player_xuid: str) -> bool:
        try:
            normalized_uuid = str(UUID(player_uuid))
        except ValueError:
            normalized_uuid = ""
        return normalized_uuid in self.uuids or (bool(player_xuid) and player_xuid in self.xuids)
