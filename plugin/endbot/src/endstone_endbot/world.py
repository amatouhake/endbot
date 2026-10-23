"""Endstone-facing world observation and teleport adapter."""

from __future__ import annotations

import math
from uuid import UUID

from endstone.level import Location

from endstone_endbot.i18n import LocalizedError
from endstone_endbot.inventory_sync import inventory_snapshot, item_summary

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


# Dismount exits. The SDK exposes no collision shapes, so passability is a
# conservative name-based list: unknown blocks count as solid (not an exit).
PASSABLE_BLOCKS = {
    "minecraft:air", "minecraft:cave_air", "minecraft:void_air", "minecraft:light_block",
    "minecraft:water", "minecraft:flowing_water", "minecraft:short_grass", "minecraft:tall_grass",
    "minecraft:grass", "minecraft:fern", "minecraft:large_fern", "minecraft:deadbush", "minecraft:seagrass",
    "minecraft:kelp", "minecraft:vine", "minecraft:glow_lichen", "minecraft:snow_layer", "minecraft:reeds",
    "minecraft:sweet_berry_bush", "minecraft:wheat", "minecraft:carrots", "minecraft:potatoes",
    "minecraft:beetroot", "minecraft:redstone_wire", "minecraft:tripwire", "minecraft:pink_petals",
}
PASSABLE_PARTS = ("rail", "torch", "button", "lever", "pressure_plate", "sapling", "_sign", "banner", "carpet",
                  "flower", "tulip", "dandelion", "poppy", "orchid", "allium", "bluet", "daisy", "lily_of_the_valley",
                  "rose", "lilac", "peony", "mushroom")
NON_FLOOR_BLOCKS = {"minecraft:water", "minecraft:flowing_water", "minecraft:lava", "minecraft:flowing_lava"}
HAZARD_BLOCKS = {"minecraft:lava", "minecraft:flowing_lava", "minecraft:fire", "minecraft:soul_fire"}


def _passable(block_type: str) -> bool:
    if block_type in PASSABLE_BLOCKS:
        return True
    name = block_type.removeprefix("minecraft:")
    return not name.endswith("_block") and any(part in name for part in PASSABLE_PARTS)


def _exit_cells(forward: tuple[int, int], vehicle_type: str) -> list[tuple[int, int]]:
    """Neighbour cells in vanilla priority relative to a cardinal forward direction.

    Minecart (Minecraft Wiki, Minecart#Dismounting): right, left, rear right,
    rear left, front right, front left, rear, front, relative to travel. Boats
    place the rider in its look direction, so front comes first there. Other
    rideables use Bedrock's default "valid ground position around the entity".
    """
    fx, fz = forward
    rx, rz = -fz, fx  # yaw 0 faces +Z (south); the right hand is then -X
    right, left, rear, front = (rx, rz), (-rx, -rz), (-fx, -fz), (fx, fz)
    order = [right, left, (rear[0] + rx, rear[1] + rz), (rear[0] - rx, rear[1] - rz),
             (front[0] + rx, front[1] + rz), (front[0] - rx, front[1] - rz), rear, front]
    if vehicle_type.endswith("boat"):
        order = [front, (front[0] + rx, front[1] + rz), (front[0] - rx, front[1] - rz), right, left,
                 (rear[0] + rx, rear[1] + rz), (rear[0] - rx, rear[1] - rz), rear]
    return order


# Read-only queries (BOT-014).
REACH = 6.0
SEARCH_RADIUS_MAX = 12
SEARCH_LIMIT_MAX = 64
DIMENSION_HEIGHTS = {"Overworld": (-64, 319), "Nether": (0, 127), "TheEnd": (0, 255)}
FACE_NAMES = ("down", "up", "north", "south", "west", "east")
NEIGHBOURS = ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))


def _state_value(value):
    return value if isinstance(value, (bool, int, float, str)) else str(value)


def _exposed(dimension, x, y, z) -> bool:
    """A block a player could see: at least one face touches a passable block."""
    return any(_passable(str(dimension.get_block_at(x + dx, y + dy, z + dz).type)) for dx, dy, dz in NEIGHBOURS)


# Half the collision width of common rideables (Bedrock entity definitions); a
# rider placed inside this box stands on the vehicle instead of the block.
VEHICLE_HALF_WIDTHS = {"minecraft:minecart": 0.49, "minecraft:boat": 0.7, "minecraft:chest_boat": 0.7,
                       "minecraft:pig": 0.45, "minecraft:strider": 0.45, "minecraft:llama": 0.45,
                       "minecraft:trader_llama": 0.45, "minecraft:camel": 0.85}
DEFAULT_VEHICLE_HALF_WIDTH = 0.7
PLAYER_HALF_WIDTH = 0.3
# Keep a margin from both the vehicle and the cell edges: a box that merely
# touches another counts as resting on it, so the Bot would not fall.
CLEARANCE = 0.05


def _clear_of_vehicle(cell: tuple[int, int], vehicle: tuple[float, float], half: float) -> tuple[float, float] | None:
    """A point whose player box lies inside the cell and clear of the vehicle box, nearest the cell centre."""
    gap = half + PLAYER_HALF_WIDTH + CLEARANCE
    point = [cell[0] + 0.5, cell[1] + 0.5]
    if all(abs(point[axis] - vehicle[axis]) >= gap for axis in (0, 1)):
        return tuple(point)
    for axis in (0, 1):
        if abs(point[axis] - vehicle[axis]) >= gap:
            return tuple(point)
        low = cell[axis] + PLAYER_HALF_WIDTH + CLEARANCE
        high = cell[axis] + 1 - PLAYER_HALF_WIDTH - CLEARANCE
        target = vehicle[axis] + (gap if point[axis] >= vehicle[axis] else -gap)
        if low <= target <= high:
            point[axis] = target
            return tuple(point)
    return None


# Entities a player can stand on; a Bot resting on one is supported by it.
STANDABLE_HALF_WIDTHS = {"minecraft:boat": 0.7, "minecraft:chest_boat": 0.7, "minecraft:shulker": 0.5}


def unsupported(player) -> bool:
    """True when a Bot standing exactly on a block top has no solid block under its feet box.

    Conservative: fractional feet heights (slabs, carpets, farmland), water,
    flight and standing on a boat or shulker are never reported.
    """
    location = player.location
    x, y, z = float(location.x), float(location.y), float(location.z)
    if abs(y - round(y)) > 0.001 or player.is_in_water or getattr(player, "is_flying", False):
        return False
    dimension = location.dimension
    floor_y = round(y) - 1
    edge = PLAYER_HALF_WIDTH - 0.001
    cells = {(math.floor(x + dx), math.floor(z + dz)) for dx in (-edge, edge) for dz in (-edge, edge)}
    for cell_x, cell_z in cells:
        floor = str(dimension.get_block_at(cell_x, floor_y, cell_z).type)
        if not _passable(floor) and floor not in NON_FLOOR_BLOCKS:
            return False
    for actor in dimension.actors:
        half = STANDABLE_HALF_WIDTHS.get(actor.type)
        if half is None:
            continue
        actor_location = actor.location
        if (abs(float(actor_location.x) - x) < half + PLAYER_HALF_WIDTH
                and abs(float(actor_location.z) - z) < half + PLAYER_HALF_WIDTH
                and -0.1 <= y - float(actor_location.y) <= 1.1):
            return False
    return True


def _cardinal(yaw: float) -> tuple[int, int]:
    radians = math.radians(round(yaw / 90) * 90)
    return round(-math.sin(radians)), round(math.cos(radians))


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
        held = player.inventory.item_in_main_hand
        return {
            "dimension": location.dimension.name,
            "x": float(location.x),
            "y": float(location.y),
            "z": float(location.z),
            "yaw": float(location.yaw),
            "pitch": float(location.pitch),
            "health": float(player.health),
            "maxHealth": float(player.max_health),
            "onGround": bool(player.is_on_ground),
            "inWater": bool(player.is_in_water),
            "selectedSlot": int(player.inventory.held_item_slot) + 1,
            "held": None if held is None or held.amount <= 0 or held.type.id == "minecraft:air"
            else item_summary(held),
        }

    def describe_entities(self, identity_id: str, runtime_ids: list[str]) -> dict[str, dict]:
        """BDS details for entities the Bot sees: position, health and name tag where they exist."""
        bot = self.server.get_player(UUID(identity_id))
        if bot is None:
            return {}
        wanted = {str(value) for value in runtime_ids}
        details = {}
        for actor in bot.location.dimension.actors:
            key = str(actor.runtime_id)
            if key not in wanted:
                continue
            location = actor.location
            health = getattr(actor, "health", None)
            details[key] = {
                "position": {"x": float(location.x), "y": float(location.y), "z": float(location.z)},
                "health": None if health is None else float(health),
                "maxHealth": None if health is None else float(actor.max_health),
                "nameTag": str(actor.name_tag) or None,
            }
        return details

    def inventory(self, name: str, identity_id: str | None = None) -> dict:
        """Read a live local-auth Bot without contacting the runtime."""
        player = self.server.get_player(UUID(identity_id) if identity_id else name)
        # With online-mode=true ordinary players have Xbox identities, as in
        # the hotbar publisher. Do not expose a human inventory via /bot.
        if player is None or str(player.xuid) or player.name.casefold() != name.casefold():
            raise ValueError("Bot is not present in the world")
        return {"name": player.name, "slots": inventory_snapshot(player)}

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
                    raise LocalizedError("Player {name} is not online", name=target_name)
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
                    raise LocalizedError("Player {name} is not online", name=parameters["facingTarget"])
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
        """Resolve a client block click without changing the world in the plugin.

        Without a face, the face toward the Bot's eyes is clicked, as for destroy.
        """
        bot = self.server.get_player(UUID(identity_id))
        if bot is None:
            raise ValueError("Bot is not present in the world")
        reference = self._snapshot(sender.location) if self._is_player(sender) else None
        coordinates = self._resolve_coordinates(parameters["coordinates"], reference)
        block_position = [math.floor(value) for value in coordinates]
        block = bot.location.dimension.get_block_at(*block_position)
        face = parameters.get("face")
        if face is None:
            face = self._face_toward(bot.location, block_position)
        return {
            "blockPosition": block_position,
            "blockRuntimeId": int(block.data.runtime_id),
            "face": int(face),
        }

    @staticmethod
    def _face_toward(location, cell) -> int:
        eye = [float(location.x), float(location.y) + 1.62, float(location.z)]
        offsets = [eye[index] - (cell[index] + 0.5) for index in range(3)]
        axis = max(range(3), key=lambda index: abs(offsets[index]))
        # Face numbering uses Y, Z, X order; parity is down/north/west versus up/south/east.
        return ((5, 4), (1, 0), (3, 2))[axis][0 if offsets[axis] > 0 else 1]

    def reachable_block(self, identity_id: str, block_type: str, reach: float) -> list[int]:
        """The nearest block of a type the Bot can see and reach (eye to the facing side); x-ray never."""
        bot = self._bot(identity_id)
        location = bot.location
        eye = [float(location.x), float(location.y) + 1.62, float(location.z)]
        candidates = self.find_blocks(identity_id, block_type, math.ceil(reach) + 1, SEARCH_LIMIT_MAX)
        candidates.sort(key=lambda entry: math.dist(eye, (entry["x"] + 0.5, entry["y"] + 0.5, entry["z"] + 0.5)))
        for entry in candidates:
            cell = [entry["x"], entry["y"], entry["z"]]
            center = [value + 0.5 for value in cell]
            distance = math.dist(eye, center)
            if distance > reach + 0.5 or distance < 0.001:
                continue
            direction = [(center[index] - eye[index]) / distance for index in range(3)]
            found = self._ray_block(location.dimension, eye, direction, distance + 0.5)
            if found is not None and found[0] == cell:
                return cell
        raise LocalizedError("No {block} in sight within {reach} blocks",
                             block=block_type.removeprefix("minecraft:"), reach=f"{reach:g}")

    def _bot(self, identity_id: str):
        bot = self.server.get_player(UUID(identity_id))
        if bot is None:
            raise ValueError("Bot is not present in the world")
        return bot

    def target(self, identity_id: str) -> dict:
        """What the Bot's crosshair hits within reach: the nearer of a block and a non-player entity."""
        bot = self._bot(identity_id)
        location = bot.location
        eye = [float(location.x), float(location.y) + 1.62, float(location.z)]
        yaw, pitch = math.radians(float(location.yaw)), math.radians(float(location.pitch))
        direction = [-math.sin(yaw) * math.cos(pitch), -math.sin(pitch), math.cos(yaw) * math.cos(pitch)]
        dimension = location.dimension
        found = self._ray_block(dimension, eye, direction, REACH)
        result = {"kind": None, "block": None, "entity": None, "distance": None}
        if found is not None:
            cell, block, face = found
            distance = math.dist(eye, [value + 0.5 for value in cell])
            result = {"kind": "block", "entity": None, "distance": round(distance, 1),
                      "block": {"x": cell[0], "y": cell[1], "z": cell[2], "face": FACE_NAMES[face],
                                "type": str(block.type)}}
        for actor in dimension.actors:
            if actor.type == "minecraft:player":
                continue
            center = [float(actor.location.x), float(actor.location.y) + 0.6, float(actor.location.z)]
            offset = [center[index] - eye[index] for index in range(3)]
            along = sum(offset[index] * direction[index] for index in range(3))
            if along <= 0 or along > REACH:
                continue
            miss = math.dist(offset, [direction[index] * along for index in range(3)])
            if miss <= 0.6 and (result["distance"] is None or along < result["distance"]):
                result = {"kind": "entity", "block": None, "distance": round(along, 1),
                          "entity": {"runtimeId": str(actor.runtime_id), "type": actor.type}}
        return result

    def block_info(self, identity_id: str, coordinates: list[object]) -> dict:
        """One block's type and states; `~` is relative to the Bot. Any position is allowed."""
        bot = self._bot(identity_id)
        position = [math.floor(value) for value in self._resolve_coordinates(coordinates, bot.location)]
        block = bot.location.dimension.get_block_at(*position)
        states = getattr(block.data, "block_states", None) or {}
        return {"x": position[0], "y": position[1], "z": position[2], "type": str(block.type),
                "states": {str(key): _state_value(value) for key, value in dict(states).items()}}

    def _known_block(self, block_type: str) -> None:
        try:
            self.server.create_block_data(block_type)
        except (RuntimeError, ValueError) as error:
            raise LocalizedError("Unknown block type: {block}", block=block_type.removeprefix("minecraft:")) from error

    def find_blocks(self, identity_id: str, block_type: str, radius: int, limit: int) -> list[dict]:
        """Nearest exposed blocks of one type around the Bot; hidden blocks are never reported."""
        bot = self._bot(identity_id)
        self._known_block(block_type)
        location = bot.location
        dimension = location.dimension
        low, high = DIMENSION_HEIGHTS.get(dimension.name, (-64, 319))
        origin = [math.floor(float(location.x)), math.floor(float(location.y)), math.floor(float(location.z))]
        feet = [float(location.x), float(location.y), float(location.z)]
        found = []
        for x in range(origin[0] - radius, origin[0] + radius + 1):
            for y in range(max(low, origin[1] - radius), min(high, origin[1] + radius) + 1):
                for z in range(origin[2] - radius, origin[2] + radius + 1):
                    if str(dimension.get_block_at(x, y, z).type) != block_type:
                        continue
                    distance = math.dist(feet, (x + 0.5, y + 0.5, z + 0.5))
                    if distance <= radius and _exposed(dimension, x, y, z):
                        found.append({"x": x, "y": y, "z": z, "distance": round(distance, 1)})
        found.sort(key=lambda entry: entry["distance"])
        return found[:limit]

    def resolve_dismount(self, identity_id: str, vehicle_runtime_id: str | None) -> dict[str, float] | None:
        """Pick a vanilla-style exit next to the vehicle, as a feet position.

        Returns None when the Bot or vehicle is not observable or no exit is
        free; the Bot then stays where the vehicle was, as before.
        """
        bot = self.server.get_player(UUID(identity_id))
        if bot is None or vehicle_runtime_id is None:
            return None
        dimension = bot.location.dimension
        vehicle = next((actor for actor in dimension.actors if str(actor.runtime_id) == str(vehicle_runtime_id)), None)
        if vehicle is None:
            return None
        location = vehicle.location
        if vehicle.type.endswith("boat"):
            yaw = float(bot.location.yaw)
        else:
            velocity = vehicle.velocity
            moving = math.hypot(float(velocity.x), float(velocity.z)) > 0.01
            yaw = math.degrees(math.atan2(-float(velocity.x), float(velocity.z))) if moving else float(location.yaw)
        base = (math.floor(location.x), math.floor(location.y), math.floor(location.z))

        def block(x, y, z):
            return str(dimension.get_block_at(x, y, z).type)

        for dy in (0, 1, -1):
            for dx, dz in _exit_cells(_cardinal(yaw), vehicle.type):
                x, y, z = base[0] + dx, base[1] + dy, base[2] + dz
                floor, feet, head = block(x, y - 1, z), block(x, y, z), block(x, y + 1, z)
                if (_passable(floor) or floor in NON_FLOOR_BLOCKS or floor in HAZARD_BLOCKS
                        or not _passable(feet) or not _passable(head)):
                    continue
                point = _clear_of_vehicle((x, z), (float(location.x), float(location.z)),
                                          VEHICLE_HALF_WIDTHS.get(vehicle.type, DEFAULT_VEHICLE_HALF_WIDTH))
                if point is None:
                    continue
                return {"x": point[0], "y": float(y), "z": point[1]}
        return None

    def assert_name_available(self, name: str, identity_id: str | None = None) -> None:
        player = self.server.get_player(name)
        if player is not None and (identity_id is None or str(player.unique_id) != str(identity_id)):
            raise LocalizedError("An online player named {name} already exists", name=name)

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
            raise LocalizedError("Unknown dimension: {dimension}", dimension=placement["dimension"]) from error
        if dimension is None:
            raise LocalizedError("Unknown dimension: {dimension}", dimension=placement["dimension"])
        x, y, z = placement["coordinates"]
        yaw, pitch = placement["rotation"]
        return Location(dimension, x, y, z, pitch, yaw)
