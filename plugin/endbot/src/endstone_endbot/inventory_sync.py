"""Read-only hotbar observations over the Bot's existing Bedrock connection.

Uses Player.send_packet, not a script engine or experimental/Beta API. The
protocol-2193 ScriptCustomEvent envelope is just a pair of strings. No item is
created, assigned, or removed here; transactions still run through BDS.
"""

from __future__ import annotations

import json

EVENT_NAME = "endbot:hotbar-v1"
PACKET_ID = 0x75
MAX_PAYLOAD = 60 * 1024


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


def hotbar_snapshot(player, server) -> dict:
    inventory = player.inventory
    slots = []
    for slot in range(9):
        item = inventory.get_item(slot)
        if item is None or item.amount <= 0:
            slots.append(None)
            continue
        name = item.type.id
        # Modern vanilla block items have their own identifier. BlockData is
        # an observation of the server palette, never a placed/edited block.
        try:
            block_id = int(server.create_block_data(name).runtime_id)
        except (RuntimeError, ValueError):
            block_id = 0  # Non-block item.
        slots.append(
            {
                "name": name,
                "count": item.amount,
                "metadata": item.data,
                "blockRuntimeId": block_id,
                "nbt": typed_nbt(item.nbt),
            }
        )
    return {"identityId": str(player.unique_id), "slots": slots}


def publish_hotbar(player, server) -> None:
    data = json.dumps(hotbar_snapshot(player, server), separators=(",", ":"), allow_nan=False)
    payload = _string(EVENT_NAME) + _string(data)
    if len(payload) > MAX_PAYLOAD:
        raise ValueError("Bot hotbar observation exceeds the packet limit")
    player.send_packet(PACKET_ID, payload)
