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

NON_MINING_TARGETS = {
    "minecraft:air",
    "minecraft:cave_air",
    "minecraft:void_air",
    "minecraft:water",
    "minecraft:flowing_water",
    "minecraft:lava",
    "minecraft:flowing_lava",
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

    def default_spawn(self, sender) -> dict[str, object] | None:
        """Place a player-issued spawn at its caller.

        The console has no position; None leaves placement to BDS, which puts a
        new identity at the world spawn point and returns a known one to where it
        last was. Endstone 0.11 exposes no world spawn location to teleport to.
        """
        if not self._is_player(sender):
            return None
        return self._snapshot(sender.location)

    def resolve_spawn_placement(self, placement: dict[str, object], sender) -> dict[str, object]:
        reference = self._snapshot(sender.location) if self._is_player(sender) else None
        resolved = self._resolve_destination(placement, reference)
        self._location(resolved)
        return resolved

    def queue_spawn_placement(self, identity_id: str, placement: dict[str, object]) -> bool:
        """Queue placement, or apply it if the Bot won the join race already.

        This method and ``apply_pending_placement`` are only called on the BDS
        server thread.  Looking up the player after publishing the placement
        therefore closes both possible event orders without a name-based
        fallback: either the join handler consumes the queued UUID entry, or
        this call observes the already-joined UUID and consumes it itself.
        """
        identity = UUID(identity_id)
        self._pending_placements[identity] = placement
        player = self.server.get_player(identity)
        if player is None:
            return False
        return self.apply_pending_placement(player)

    def clear_pending_placement(self, identity_id: str) -> None:
        self._pending_placements.pop(UUID(identity_id), None)

    def apply_pending_placement(self, player) -> bool:
        placement = self._pending_placements.get(player.unique_id)
        if not placement:
            return False
        if not player.teleport(self._location(placement)):
            return False
        # A teleport event may synchronously publish a newer request. Consume
        # only the placement that was actually applied.
        if self._pending_placements.get(player.unique_id) is placement:
            self._pending_placements.pop(player.unique_id, None)
        return True

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
            reference = self._snapshot(sender.location) if self._is_player(sender) else None
            destination = self._resolve_destination(
                parameters,
                reference,
                default_dimension=bot.location.dimension.name,
            )
            if "facingTarget" in parameters:
                target = self.server.get_player(str(parameters["facingTarget"]))
                if target is None:
                    raise ValueError(f"Player {parameters['facingTarget']} is not online")
                destination["rotation"] = self._facing(destination["coordinates"], self._coordinates(target.location))
            elif "facingCoordinates" in parameters:
                facing = self._resolve_coordinates(parameters["facingCoordinates"], reference)
                destination["rotation"] = self._facing(destination["coordinates"], facing)
            location = self._location(destination)
            if not bot.teleport(location):
                raise RuntimeError("Endstone rejected the teleport")
        return f"{location.dimension.name} {location.x:.2f} {location.y:.2f} {location.z:.2f}"

    def look_at(self, identity_id: str, coordinates: list[object]) -> tuple[float, float]:
        bot = self.server.get_player(UUID(identity_id))
        if bot is None:
            raise ValueError("Bot is not present in the world")
        target = self._resolve_coordinates(coordinates, bot.location)
        return self._facing(self._coordinates(bot.location), target)

    def resolve_interaction(self, identity_id: str, parameters: dict[str, object], sender) -> dict[str, object]:
        """Resolve a client block click without changing the world in the plugin."""
        bot = self.server.get_player(UUID(identity_id))
        if bot is None:
            raise ValueError("Bot is not present in the world")
        reference = self._snapshot(sender.location) if self._is_player(sender) else None
        coordinates = self._resolve_coordinates(parameters["coordinates"], reference)
        block_position = [math.floor(value) for value in coordinates]
        block = bot.location.dimension.get_block_at(*block_position)
        return {
            "blockPosition": block_position,
            "blockRuntimeId": int(block.data.runtime_id),
            "face": int(parameters["face"]),
        }

    def assert_name_available(self, name: str, identity_id: str | None = None) -> None:
        player = self.server.get_player(name)
        if player is not None and (identity_id is None or str(player.unique_id) != str(identity_id)):
            raise ValueError(f"An online player named {name} already exists")

    @staticmethod
    def _ray_block(dimension, origin, direction, distance):
        """Voxel ray cast; returns the first non-air/liquid cell and entry face."""
        cell = [math.floor(value) for value in origin]
        steps = [1 if value > 0 else -1 for value in direction]
        delta = [abs(1 / value) if value else math.inf for value in direction]
        crossings = [
            ((cell[i] + (1 if steps[i] > 0 else 0) - origin[i]) / direction[i]) if direction[i] else math.inf
            for i in range(3)
        ]
        travelled, face = 0.0, 1
        while travelled <= distance:
            block = dimension.get_block_at(*cell)
            if block.type not in NON_MINING_TARGETS:
                return list(cell), block, face
            axis = min(range(3), key=lambda index: crossings[index])
            travelled = crossings[axis]
            cell[axis] += steps[axis]
            face = ((4, 5), (0, 1), (2, 3))[axis][0 if steps[axis] > 0 else 1]
            crossings[axis] += delta[axis]
        return None

    def resolve_mining(self, identity_id: str, parameters: dict[str, object], sender) -> dict[str, object] | None:
        """Observe a reachable target; all destruction stays in BDS gameplay."""
        bot = self.server.get_player(UUID(identity_id))
        if bot is None:
            raise ValueError("Bot is not present in the world")
        location = bot.location
        eye = [float(location.x), float(location.y) + 1.62, float(location.z)]
        dimension = location.dimension
        if "coordinates" in parameters:
            target = [math.floor(value) for value in self._resolve_coordinates(parameters["coordinates"], location)]
            center = [value + 0.5 for value in target]
            offsets = [eye[i] - center[i] for i in range(3)]
            axis = max(range(3), key=lambda index: abs(offsets[index]))
            face = int(parameters.get("face", ((5, 4), (1, 0), (3, 2))[axis][0 if offsets[axis] > 0 else 1]))
            hit = list(center)
            hit[(1, 1, 2, 2, 0, 0)[face]] += 0.499 if face % 2 else -0.499
            # Face numbering uses Y, Z, X order; parity is down/north/west versus up/south/east.
            direction = [hit[i] - eye[i] for i in range(3)]
            distance = math.sqrt(sum(value * value for value in direction))
            if distance > 5.0:
                raise ValueError("Target block is out of reach (maximum 5 blocks)")
            if distance < 0.001:
                raise ValueError("Target block overlaps the Bot's eye position")
            direction = [value / distance for value in direction]
            found = self._ray_block(dimension, eye, direction, distance + 0.01)
            if found is None or found[0] != target:
                raise ValueError("Target is air/liquid or blocked by another block")
            block = found[1]
        else:
            yaw, pitch = math.radians(location.yaw), math.radians(location.pitch)
            direction = [-math.sin(yaw) * math.cos(pitch), -math.sin(pitch), math.cos(yaw) * math.cos(pitch)]
            found = self._ray_block(dimension, eye, direction, 5.0)
            if found is None:
                if parameters.get("continuous"):
                    return None
                raise ValueError("No block in the Bot's line of sight within 5 blocks")
            target, block, face = found
            hit = [value + 0.5 for value in target]
        if block.type == "minecraft:bedrock":
            if parameters.get("continuous"):
                return None
            raise ValueError("Bedrock cannot be destroyed in Survival")
        yaw, pitch = self._facing(eye, hit)
        if parameters.get("continuous"):
            yaw, pitch = float(location.yaw), float(location.pitch)
        return {
            "blockPosition": target,
            "blockRuntimeId": int(block.data.runtime_id),
            "blockName": block.type,
            "face": face,
            "yaw": yaw,
            "pitch": pitch,
            "dimension": dimension.name,
        }

    def _resolve_destination(self, parameters, reference=None, default_dimension=None):
        coordinates = self._resolve_coordinates(parameters["coordinates"], reference)
        reference_dimension = reference.get("dimension", "") if isinstance(reference, dict) else ""
        dimension = str(parameters.get("dimension") or default_dimension or reference_dimension)
        if not dimension:
            raise ValueError("A dimension is required for this command source")
        result = {"coordinates": coordinates, "dimension": DIMENSIONS.get(dimension.lower(), dimension)}
        if "rotation" in parameters:
            result["rotation"] = list(parameters["rotation"])
        elif reference is not None:
            result["rotation"] = list(reference["rotation"])
        else:
            result["rotation"] = [0.0, 0.0]
        return result

    def _resolve_coordinates(self, values, reference):
        resolved = []
        if isinstance(reference, dict):
            current = reference["coordinates"]
        else:
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
