"""Identity-first registry boundary for future controllers and persistence."""

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class BotIdentity:
    identity_id: UUID
    name: str


class BotRegistry:
    """Index bots by immutable UUID while exposing unique, user-facing names."""

    def __init__(self) -> None:
        self._by_id: dict[UUID, BotIdentity] = {}
        self._name_to_id: dict[str, UUID] = {}

    def register(self, identity: BotIdentity) -> None:
        if not identity.name or len(identity.name.encode("utf-8")) > 64:
            raise ValueError("bot name must contain between 1 and 64 UTF-8 bytes")
        normalized_name = identity.name.casefold()
        if identity.identity_id in self._by_id:
            raise ValueError("bot UUID is already registered")
        if normalized_name in self._name_to_id:
            raise ValueError("bot name is already registered")
        self._by_id[identity.identity_id] = identity
        self._name_to_id[normalized_name] = identity.identity_id

    def get_by_name(self, name: str) -> BotIdentity | None:
        identity_id = self._name_to_id.get(name.casefold())
        return self._by_id.get(identity_id) if identity_id is not None else None

    def get_by_id(self, identity_id: UUID) -> BotIdentity | None:
        return self._by_id.get(identity_id)

