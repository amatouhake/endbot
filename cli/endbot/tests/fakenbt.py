"""Tiny little-endian NBT encoder for synthetic ``level.dat`` fixtures.

Only used by the unit tests; never commit a real world file (AGENTS.md). The
encoding mirrors the read-only decoder in ``endbot_cli.leveldat``.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

TAG_BYTE = 1
TAG_SHORT = 2
TAG_INT = 3
TAG_LONG = 4
TAG_FLOAT = 5
TAG_DOUBLE = 6
TAG_BYTE_ARRAY = 7
TAG_STRING = 8
TAG_LIST = 9
TAG_COMPOUND = 10
TAG_INT_ARRAY = 11
TAG_LONG_ARRAY = 12


@dataclass(frozen=True)
class Tag:
    type_id: int
    payload: bytes


def byte(value: int) -> Tag:
    return Tag(TAG_BYTE, struct.pack("<b", value))


def short(value: int) -> Tag:
    return Tag(TAG_SHORT, struct.pack("<h", value))


def int32(value: int) -> Tag:
    return Tag(TAG_INT, struct.pack("<i", value))


def long64(value: int) -> Tag:
    return Tag(TAG_LONG, struct.pack("<q", value))


def string(value: str) -> Tag:
    encoded = value.encode("utf-8")
    return Tag(TAG_STRING, struct.pack("<H", len(encoded)) + encoded)


def byte_array(value: bytes) -> Tag:
    return Tag(TAG_BYTE_ARRAY, struct.pack("<i", len(value)) + value)


def int_array(values: list[int]) -> Tag:
    return Tag(TAG_INT_ARRAY, struct.pack("<i", len(values)) + b"".join(struct.pack("<i", v) for v in values))


def long_array(values: list[int]) -> Tag:
    return Tag(TAG_LONG_ARRAY, struct.pack("<i", len(values)) + b"".join(struct.pack("<q", v) for v in values))


def list_tag(element_type: int, payloads: list[bytes]) -> Tag:
    return Tag(TAG_LIST, bytes([element_type]) + struct.pack("<i", len(payloads)) + b"".join(payloads))


def compound(tags: dict[str, Tag]) -> Tag:
    body = b"".join(named(name, tag) for name, tag in tags.items()) + b"\x00"
    return Tag(TAG_COMPOUND, body)


def named(name: str, tag: Tag) -> bytes:
    encoded = name.encode("utf-8")
    return bytes([tag.type_id]) + struct.pack("<H", len(encoded)) + encoded + tag.payload


def level_dat(tags: dict[str, Tag], *, storage_version: int = 10) -> bytes:
    """Build a complete ``level.dat``: 8-byte header plus a root compound."""

    payload = named("", compound(tags))
    return struct.pack("<ii", storage_version, len(payload)) + payload
