"""Read-only parser for Bedrock ``level.dat`` creative/experiment history.

A Bedrock ``level.dat`` is an 8-byte little-endian header (int32 storage
version, int32 payload length) followed by a little-endian NBT document whose
root compound holds the world history flags audited by ``endbot doctor``
(docs/OPERATIONS.md §7). This module never writes the file and never trusts the
input: truncated or corrupt data raises :class:`LevelDatError` with a clear
message instead of a traceback or a false safety claim.

Decoded here, when present: ``commandsEnabled``, ``hasBeenLoadedInCreative``,
the ``experiments`` compound (``experiments_ever_used``,
``saved_with_toggled_experiments``, plus any other keys, which are reported),
``GameType``, and ``cheatsEnabled``. The root compound is inspected first with
a ``Data`` compound as fallback for Java-style layouts. Experiment flags are
read from the ``experiments`` compound with a root-level fallback so a truthy
history flag is never missed (fail closed).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Any

TAG_END = 0
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

_HEADER_SIZE = 8
_MAX_DEPTH = 64
_NAMED_EXPERIMENT_FLAGS = ("experiments_ever_used", "saved_with_toggled_experiments")


class LevelDatError(ValueError):
    """``level.dat`` is truncated or corrupt and cannot be decoded safely."""


@dataclass(frozen=True, slots=True)
class LevelSummary:
    """Decoded world history flags; ``None`` means the tag was not present."""

    storage_version: int
    commands_enabled: bool | None = None
    has_been_loaded_in_creative: bool | None = None
    game_type: int | None = None
    cheats_enabled: bool | None = None
    experiments_ever_used: bool | None = None
    saved_with_toggled_experiments: bool | None = None
    other_experiment_keys: tuple[str, ...] = ()

    def unsafe_flags(self) -> tuple[str, ...]:
        """Names of the history flags that are set truthy (achievement killers)."""

        flags = (
            ("hasBeenLoadedInCreative", self.has_been_loaded_in_creative),
            ("commandsEnabled", self.commands_enabled),
            ("experiments_ever_used", self.experiments_ever_used),
            ("saved_with_toggled_experiments", self.saved_with_toggled_experiments),
        )
        return tuple(name for name, value in flags if value)


class _Reader:
    """Bounds-checked little-endian NBT reader over the payload bytes."""

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._offset = 0

    @property
    def remaining(self) -> int:
        return len(self._data) - self._offset

    def _take(self, count: int, what: str) -> bytes:
        chunk = self._data[self._offset : self._offset + count]
        if len(chunk) != count:
            raise LevelDatError(f"level.dat is corrupt or truncated: unexpected end of data while reading {what}")
        self._offset += count
        return chunk

    def _unpack(self, fmt: str, what: str) -> Any:
        return struct.unpack(fmt, self._take(struct.calcsize(fmt), what))[0]

    def _u8(self, what: str) -> int:
        return self._unpack("<B", what)

    def _string(self, what: str) -> str:
        length = self._unpack("<H", f"{what} length")
        raw = self._take(length, what)
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError as error:
            raise LevelDatError(f"level.dat is corrupt: invalid UTF-8 in {what}: {error}") from error

    def _payload(self, type_id: int, depth: int) -> Any:
        if depth > _MAX_DEPTH:
            raise LevelDatError("level.dat is corrupt: NBT nesting is too deep")
        if type_id == TAG_BYTE:
            return self._unpack("<b", "byte tag")
        if type_id == TAG_SHORT:
            return self._unpack("<h", "short tag")
        if type_id == TAG_INT:
            return self._unpack("<i", "int tag")
        if type_id == TAG_LONG:
            return self._unpack("<q", "long tag")
        if type_id == TAG_FLOAT:
            return self._unpack("<f", "float tag")
        if type_id == TAG_DOUBLE:
            return self._unpack("<d", "double tag")
        if type_id == TAG_BYTE_ARRAY:
            length = self._unpack("<i", "byte array length")
            if length < 0 or length > self.remaining:
                raise LevelDatError("level.dat is corrupt: byte array length exceeds the payload")
            return self._take(length, "byte array")
        if type_id == TAG_STRING:
            return self._string("string tag")
        if type_id == TAG_LIST:
            element_type = self._u8("list element type")
            count = self._unpack("<i", "list length")
            if count < 0 or count > self.remaining:
                raise LevelDatError("level.dat is corrupt: list length exceeds the payload")
            if element_type == TAG_END and count:
                raise LevelDatError("level.dat is corrupt: non-empty list of end tags")
            return [self._payload(element_type, depth + 1) for _ in range(count)]
        if type_id == TAG_COMPOUND:
            entries: dict[str, Any] = {}
            while True:
                child_type = self._u8("compound child type")
                if child_type == TAG_END:
                    return entries
                if not 0 < child_type <= TAG_LONG_ARRAY:
                    raise LevelDatError(f"level.dat is corrupt: unknown NBT tag type {child_type}")
                child_name = self._string("compound child name")
                entries[child_name] = self._payload(child_type, depth + 1)
        if type_id == TAG_INT_ARRAY:
            length = self._unpack("<i", "int array length")
            if length < 0 or length * 4 > self.remaining:
                raise LevelDatError("level.dat is corrupt: int array length exceeds the payload")
            return [self._unpack("<i", "int array element") for _ in range(length)]
        if type_id == TAG_LONG_ARRAY:
            length = self._unpack("<i", "long array length")
            if length < 0 or length * 8 > self.remaining:
                raise LevelDatError("level.dat is corrupt: long array length exceeds the payload")
            return [self._unpack("<q", "long array element") for _ in range(length)]
        raise LevelDatError(f"level.dat is corrupt: unknown NBT tag type {type_id}")

    def read_document(self) -> tuple[int, dict[str, Any]]:
        """Read the root compound (a named tag) and return (name, entries)."""

        type_id = self._u8("root tag type")
        if type_id != TAG_COMPOUND:
            raise LevelDatError(f"level.dat is corrupt: root tag must be a compound, found type {type_id}")
        name = self._string("root tag name")
        entries = self._payload(type_id, 0)
        return name, entries


def _flag(value: Any) -> bool | None:
    if value is None:
        return None
    return bool(value)


def _lookup(root: dict[str, Any], name: str) -> Any:
    if name in root:
        return root[name]
    data = root.get("Data")
    if isinstance(data, dict) and name in data:
        return data[name]
    return None


def parse_level_dat(data: bytes) -> LevelSummary:
    """Decode ``level.dat`` bytes into a :class:`LevelSummary` (read-only)."""

    if len(data) < _HEADER_SIZE:
        raise LevelDatError(f"level.dat is truncated: the {_HEADER_SIZE}-byte header is incomplete")
    storage_version, payload_length = struct.unpack("<ii", data[:_HEADER_SIZE])
    body = data[_HEADER_SIZE:]
    if payload_length < 0:
        raise LevelDatError(f"level.dat is corrupt: negative payload length {payload_length}")
    if len(body) != payload_length:
        raise LevelDatError(
            f"level.dat is corrupt or truncated: header declares {payload_length} payload bytes, found {len(body)}"
        )
    _, root = _Reader(body).read_document()

    experiments = _lookup(root, "experiments")
    experiments = experiments if isinstance(experiments, dict) else {}

    def experiment_flag(name: str) -> bool | None:
        value = experiments.get(name)
        return _flag(value) if value is not None else _flag(_lookup(root, name))

    game_type = _lookup(root, "GameType")
    return LevelSummary(
        storage_version=storage_version,
        commands_enabled=_flag(_lookup(root, "commandsEnabled")),
        has_been_loaded_in_creative=_flag(_lookup(root, "hasBeenLoadedInCreative")),
        game_type=game_type if isinstance(game_type, int) and not isinstance(game_type, bool) else None,
        cheats_enabled=_flag(_lookup(root, "cheatsEnabled")),
        experiments_ever_used=experiment_flag("experiments_ever_used"),
        saved_with_toggled_experiments=experiment_flag("saved_with_toggled_experiments"),
        other_experiment_keys=tuple(sorted(name for name in experiments if name not in _NAMED_EXPERIMENT_FLAGS)),
    )
