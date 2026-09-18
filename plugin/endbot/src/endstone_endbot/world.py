"""Endstone-facing world observation and teleport adapter."""

from __future__ import annotations

import math
from uuid import UUID

from endstone.level import Location


DIMENSIONS = {
    "overworld": "Overworld",
    "minecraft:overworld": "Overworld",
    "nether": "Nether",
    "minecraft:nether": "Nether",
    "end": "TheEnd",
    "the_end": "TheEnd",
    "minecraft:the_end": "TheEnd",
}


class EndstoneWorld:
    def __init__(self, server) -> None:
        self.server = server
        self._pending_placements: dict[UUID, dict[str, object]] = {}

    @staticmethod
    def _is_player(sender) -> bool:
        return hasattr(sender, "unique_id") and hasattr(sender, "location")

    @staticmethod
    def _snapshot(location) -> dict[str, object]:
        return {
            "coordinates": [float(location.x), float(location.y), float(location.z)],
            "rotation": [float(location.yaw), float(location.pitch)],
            "dimension": location.dimension.name,
        }

    def default_spawn(self, sender) -> dict[str, object]:
        if not self._is_player(sender):
            raise ValueError("Console spawn requires: spawn at <x> <y> <z> [in <dimension>]")
        return self._snapshot(sender.location)

    def queue_spawn_placement(self, identity_id: str, placement: dict[str, object], sender) -> None:
        reference = sender.location if self._is_player(sender) else None
        self._pending_placements[UUID(identity_id)] = self._resolve_destination(placement, reference)

    def apply_pending_placement(self, player) -> bool:
        placement = self._pending_placements.pop(player.unique_id, None)
        if not placement:
            return False
        return bool(player.teleport(self._location(placement)))

    def observe(self, identity_id: str) -> dict[str, object] | None:
        player = self.server.get_player(UUID(identity_id))
        if player is None:
            return None
        location = player.location
        return {
            "dimension": location.dimension.name,
            "x": float(location.x),
            "y": float(location.y),
            "z": float(location.z),
            "yaw": float(location.yaw),
            "pitch": float(location.pitch),
        }

    def teleport(self, identity_id: str, parameters: dict[str, object], sender) -> str:
        bot = self.server.get_player(UUID(identity_id))
        if bot is None:
            raise ValueError("Bot is not present in the world")
        target_name = parameters.get("target")
        if target_name:
            if str(target_name).lower() == "me":
                if not self._is_player(sender):
                    raise ValueError("tp me requires a player command source")
                target = sender
            else:
                target = self.server.get_player(str(target_name))
                if target is None:
                    raise ValueError(f"Player {target_name} is not online")
            if not bot.teleport(target):
                raise RuntimeError("Endstone rejected the teleport")
            location = target.location
        else:
            reference = sender.location if self._is_player(sender) else None
            destination = self._resolve_destination(parameters, reference, default_dimension=bot.location.dimension.name)
            if "facingTarget" in parameters:
                target = self.server.get_player(str(parameters["facingTarget"]))
                if target is None:
                    raise ValueError(f"Player {parameters['facingTarget']} is not online")
                destination["rotation"] = self._facing(destination["coordinates"], self._coordinates(target.location))
            elif "facingCoordinates" in parameters:
                facing = self._resolve_coordinates(parameters["facingCoordinates"], bot.location)
                destination["rotation"] = self._facing(destination["coordinates"], facing)
            location = self._location(destination)
            if not bot.teleport(location):
                raise RuntimeError("Endstone rejected the teleport")
        return f"{location.dimension.name} {location.x:.2f} {location.y:.2f} {location.z:.2f}"

    def look_at(self, name: str, coordinates: list[object]) -> tuple[float, float]:
        player = self.server.get_player(name)
        if player is None:
            raise ValueError(f"Bot {name} is not present in the world")
        target = self._resolve_coordinates(coordinates, player.location)
        return self._facing(self._coordinates(player.location), target)

    def assert_name_available(self, name: str) -> None:
        if self.server.get_player(name) is not None:
            raise ValueError(f"An online player named {name} already exists")

    def _resolve_destination(self, parameters, reference=None, default_dimension=None):
        coordinates = self._resolve_coordinates(parameters["coordinates"], reference)
        dimension = str(parameters.get("dimension") or default_dimension or getattr(reference.dimension, "name", ""))
        if not dimension:
            raise ValueError("A dimension is required for this command source")
        result = {"coordinates": coordinates, "dimension": DIMENSIONS.get(dimension.lower(), dimension)}
        if "rotation" in parameters:
            result["rotation"] = list(parameters["rotation"])
        elif reference is not None:
            result["rotation"] = [float(reference.yaw), float(reference.pitch)]
        else:
            result["rotation"] = [0.0, 0.0]
        return result

    def _resolve_coordinates(self, values, reference):
        resolved = []
        current = self._coordinates(reference) if reference is not None else None
        for index, value in enumerate(values):
            if isinstance(value, (list, tuple)) and value[0] == "relative":
                if current is None:
                    raise ValueError("Relative coordinates require a player command source")
                resolved.append(current[index] + float(value[1]))
            else:
                resolved.append(float(value))
        return resolved

    @staticmethod
    def _coordinates(location):
        return [float(location.x), float(location.y), float(location.z)]

    @staticmethod
    def _facing(origin, target):
        dx, dy, dz = (target[index] - origin[index] for index in range(3))
        horizontal = math.hypot(dx, dz)
        return math.degrees(math.atan2(-dx, dz)) % 360, -math.degrees(math.atan2(dy, horizontal))

    def _location(self, placement):
        try:
            dimension = self.server.level.get_dimension(placement["dimension"])
        except (KeyError, ValueError) as error:
            raise ValueError(f"Unknown dimension: {placement['dimension']}") from error
        if dimension is None:
            raise ValueError(f"Unknown dimension: {placement['dimension']}")
        x, y, z = placement["coordinates"]
        yaw, pitch = placement["rotation"]
        return Location(dimension, x, y, z, pitch, yaw)
