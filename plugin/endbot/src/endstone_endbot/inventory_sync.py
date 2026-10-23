"""Read-only full inventory observations over the Bot's existing connection.

Uses Player.send_packet, not a script engine or experimental/Beta API. The
protocol-2193 ScriptCustomEvent envelope is just a pair of strings. No item is
created, assigned, or removed here; transactions still run through BDS.
"""

from __future__ import annotations

import base64
import json
import time

EVENT_NAME = "endbot:inventory-v2"
PACKET_ID = 0x75
MAX_PAYLOAD = 60 * 1024
MAX_INVENTORY_BYTES = 2 * 1024 * 1024
CHUNK_BYTES = 40 * 1024

# Screen labels, kept separate from SDK property names and protocol selectors.
EQUIPMENT_SLOTS = (
    ("offhand", "item_in_off_hand"),
    ("head", "helmet"),
    ("chest", "chestplate"),
    ("legs", "leggings"),
    ("feet", "boots"),
)


def inventory_snapshot(player) -> list[dict]:
    """Copy non-empty server slots into plain data on the server thread."""
    inventory = player.inventory
    slots = [(str(slot + 1), inventory.get_item(slot)) for slot in range(36)]
    slots.extend((label, getattr(inventory, attribute)) for label, attribute in EQUIPMENT_SLOTS)
    return [
        {"slot": label, **item_summary(item)}
        for label, item in slots
        if item is not None and item.amount > 0 and item.type.id != "minecraft:air"
    ]


def item_summary(item) -> dict:
    """Name and count, plus remaining/max durability for damageable items."""
    summary = {"name": item.type.id, "count": item.amount}
    maximum = int(getattr(item.type, "max_durability", 0) or 0)
    if maximum > 0:
        damage = int(item.item_meta.damage) if item.item_meta is not None else 0
        summary["durability"] = {"remaining": max(0, maximum - damage), "max": maximum}
    return summary


def typed_nbt(tag):
    """Keep NBT types and 64-bit integers intact across JSON."""
    kind = type(tag).__name__
    scalars = {
        "ByteTag": "byte",
        "ShortTag": "short",
        "IntTag": "int",
        "LongTag": "long",
        "FloatTag": "float",
        "DoubleTag": "double",
        "StringTag": "string",
    }
    if kind in scalars:
        value = str(tag.value) if kind == "LongTag" else tag.value
        return {"type": scalars[kind], "value": value}
    if kind == "CompoundTag":
        return {"type": "compound", "value": {key: typed_nbt(value) for key, value in tag.items()}}
    if kind == "ListTag":
        values = [typed_nbt(value) for value in tag]
        element_type = values[0]["type"] if values else "end"
        if any(value["type"] != element_type for value in values):
            raise ValueError("Inventory NBT list has inconsistent types")
        return {"type": "list", "value": {"type": element_type, "value": [value["value"] for value in values]}}
    if kind in {"ByteArrayTag", "IntArrayTag"}:
        values = [value if value < 128 else value - 256 for value in tag] if kind == "ByteArrayTag" else list(tag)
        return {"type": "byteArray" if kind == "ByteArrayTag" else "intArray", "value": values}
    raise ValueError(f"Unsupported inventory NBT type: {kind}")


def _string(value: str) -> bytes:
    data = value.encode("utf-8")
    size, prefix = len(data), bytearray()
    while size > 127:
        prefix.append((size & 127) | 128)
        size >>= 7
    prefix.append(size)
    return bytes(prefix) + data


def hotbar_snapshot(player, server, block_ids: dict | None = None, *, full: bool = False) -> dict:
    inventory = player.inventory
    slots = []
    items = [inventory.get_item(slot) for slot in range(36 if full else 9)]
    if full:
        items.extend(getattr(inventory, attribute) for _, attribute in EQUIPMENT_SLOTS)
    for item in items:
        if item is None or item.amount <= 0:
            slots.append(None)
            continue
        name = item.type.id
        # Modern vanilla block items have their own identifier. BlockData is
        # an observation of the server palette, never a placed/edited block.
        if block_ids is not None and name in block_ids:
            block_id = block_ids[name]
        else:
            try:
                block_id = int(server.create_block_data(name).runtime_id)
            except (RuntimeError, ValueError):
                block_id = 0  # Non-block item.
            if block_ids is not None:
                if len(block_ids) >= 4096:
                    block_ids.clear()
                block_ids[name] = block_id
        slots.append(
            {
                "name": name,
                "count": item.amount,
                "metadata": item.data,
                "blockRuntimeId": block_id,
                "nbt": typed_nbt(item.nbt),
            }
        )
    snapshot = {"identityId": str(player.unique_id), "slots": slots[:36] if full else slots}
    if full:
        snapshot["equipment"] = slots[36:]
    return snapshot


def full_inventory_snapshot(player, server, block_ids: dict | None = None) -> dict:
    return hotbar_snapshot(player, server, block_ids, full=True)


def publish_hotbar(player, server) -> None:
    for payload in observation_payloads(full_inventory_snapshot(player, server), time.time_ns() // 1_000_000):
        player.send_packet(PACKET_ID, payload)


def _payload(snapshot) -> bytes:
    data = json.dumps(snapshot, separators=(",", ":"), allow_nan=False)
    payload = _string(EVENT_NAME) + _string(data)
    if len(payload) > MAX_PAYLOAD:
        raise ValueError("Bot inventory observation exceeds the packet limit")
    return payload


def observation_payloads(snapshot, sequence: int) -> tuple[bytes, ...]:
    """Atomic full snapshot, split before sending; never truncate item data."""
    data = json.dumps(snapshot, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(data) > MAX_INVENTORY_BYTES:
        raise ValueError("Bot inventory observation exceeds the total size bound")
    parts = max(1, (len(data) + CHUNK_BYTES - 1) // CHUNK_BYTES)
    return tuple(_payload({
        "identityId": snapshot["identityId"], "sequence": sequence, "part": part, "parts": parts,
        "data": base64.b64encode(data[part * CHUNK_BYTES:(part + 1) * CHUNK_BYTES]).decode("ascii"),
    }) for part in range(parts))


def sync_settings(config) -> tuple[int, int]:
    values = []
    for key, default, maximum in (
        ("hotbar-sync-interval-ticks", 4, 1200),
        ("hotbar-sync-resend-ticks", 20, 72000),
    ):
        value = config.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
            raise ValueError(f"runtime.{key} must be an integer from 1 to {maximum}")
        values.append(value)
    return tuple(values)


class HotbarPublisher:
    """Send changes and periodic corrections, retaining only online Bot state."""

    def __init__(self, resend_ticks: int = 20):
        self.resend_ticks = resend_ticks
        self.previous = {}
        self.block_ids = {}
        self.sequence = 0

    def publish(self, player, server, tick: int) -> bool:
        identity = str(player.unique_id)
        snapshot = full_inventory_snapshot(player, server, self.block_ids)
        # Compare the exact content, independently of the fragment sequence.
        payload = json.dumps(snapshot, separators=(",", ":"), allow_nan=False)
        previous = self.previous.get(identity)
        if previous is not None and payload == previous[0] and tick - previous[1] < self.resend_ticks:
            return False
        # Millisecond epoch plus a local monotonic floor also survives publisher
        # recreation while the Bot connection stays online. It fits a JS integer.
        self.sequence = max(self.sequence + 1, time.time_ns() // 1_000_000)
        packets = observation_payloads(snapshot, self.sequence)
        for packet in packets:
            player.send_packet(PACKET_ID, packet)
        self.previous[identity] = (payload, tick)
        return True

    def retain(self, identities: set[str]) -> None:
        self.previous = {key: value for key, value in self.previous.items() if key in identities}
