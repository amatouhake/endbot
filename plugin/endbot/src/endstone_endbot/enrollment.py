"""GamerTag bootstrap enrollment of Endbot controllers.

Operators name controllers by Xbox GamerTag. The first join of a Microsoft/Xbox
authenticated player whose name matches a pending GamerTag binds that GamerTag
to the player's XUID and UUID; from then on only the XUID grants control, so a
later GamerTag change keeps control and another account taking the old GamerTag
gets nothing. See docs/OPERATIONS.md section 3.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

BINDINGS_VERSION = 1


class EnrollmentError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Binding:
    gamertag: str
    xuid: str
    uuid: str
    bound_at: str

    def to_json(self) -> dict[str, str]:
        return {"gamertag": self.gamertag, "xuid": self.xuid, "uuid": self.uuid, "boundAt": self.bound_at}


def _is_xuid(value: object) -> bool:
    return isinstance(value, str) and value.isascii() and value.isdecimal() and value != "0"


def _gamertag_key(value: str) -> str:
    return value.casefold()


def parse_gamertags(values: object) -> tuple[str, ...]:
    if not isinstance(values, list):
        raise TypeError("controller gamertags must be an array of strings")
    gamertags: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise TypeError("controller gamertags must be non-empty strings")
        key = _gamertag_key(value.strip())
        if key in seen:
            raise ValueError(f"controller gamertag {value!r} is listed twice")
        seen.add(key)
        gamertags.append(value.strip())
    return tuple(gamertags)


def load_bindings(path: Path) -> tuple[Binding, ...]:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ()
    try:
        document = json.loads(raw)
    except ValueError as error:
        raise EnrollmentError(f"{path}: controller bindings are not valid JSON") from error
    if not isinstance(document, dict) or document.get("version") != BINDINGS_VERSION:
        raise EnrollmentError(f"{path}: unsupported controller bindings version")
    entries = document.get("bindings")
    if not isinstance(entries, list):
        raise EnrollmentError(f"{path}: bindings must be an array")
    bindings: list[Binding] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise EnrollmentError(f"{path}: every binding must be an object")
        gamertag, xuid, uuid, bound_at = (entry.get(key) for key in ("gamertag", "xuid", "uuid", "boundAt"))
        if not isinstance(gamertag, str) or not gamertag or not _is_xuid(xuid) or not isinstance(bound_at, str):
            raise EnrollmentError(f"{path}: malformed controller binding")
        try:
            normalized_uuid = str(UUID(str(uuid)))
        except ValueError as error:
            raise EnrollmentError(f"{path}: malformed controller binding UUID") from error
        bindings.append(Binding(gamertag, xuid, normalized_uuid, bound_at))
    return tuple(bindings)


def _write_bindings(path: Path, bindings: tuple[Binding, ...]) -> None:
    document = {"version": BINDINGS_VERSION, "bindings": [binding.to_json() for binding in bindings]}
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".controllers-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            json.dump(document, file, indent=2)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


class ControllerEnrollment:
    """Pending GamerTags plus persisted XUID bindings.

    Only bindings whose GamerTag is still configured are honoured, so removing
    a GamerTag from the operator config revokes its binding.
    """

    def __init__(self, gamertags: tuple[str, ...], bindings_path: Path | None) -> None:
        self.gamertags = gamertags
        self.bindings_path = bindings_path
        configured = {_gamertag_key(tag) for tag in gamertags}
        stored = load_bindings(bindings_path) if bindings_path is not None else ()
        self._bindings = {
            _gamertag_key(binding.gamertag): binding
            for binding in stored
            if _gamertag_key(binding.gamertag) in configured
        }

    @classmethod
    def from_config(cls, config: dict, data_folder: Path) -> ControllerEnrollment:
        gamertags = parse_gamertags(config.get("controller-gamertags", []))
        configured_path = config.get("controllers-file")
        if configured_path is None:
            if gamertags:
                raise EnrollmentError("controller-gamertags requires controllers-file")
            return cls((), None)
        if not isinstance(configured_path, str) or not configured_path:
            raise TypeError("controllers-file must be a non-empty string")
        path = Path(configured_path)
        return cls(gamertags, path if path.is_absolute() else data_folder / path)

    @property
    def bindings(self) -> tuple[Binding, ...]:
        return tuple(self._bindings.values())

    def pending(self) -> tuple[str, ...]:
        return tuple(tag for tag in self.gamertags if _gamertag_key(tag) not in self._bindings)

    def authorize(self, name: str, xuid: str, player_uuid: str) -> tuple[bool, Binding | None]:
        """Return (authorized, new_binding) for a joining player.

        A player without a Microsoft/Xbox XUID (including every local Bot) can
        never bind or be authorized here.
        """
        if not _is_xuid(xuid):
            return False, None
        if any(binding.xuid == xuid for binding in self._bindings.values()):
            return True, None
        key = _gamertag_key(name)
        if key in self._bindings or key not in {_gamertag_key(tag) for tag in self.gamertags}:
            return False, None
        if self.bindings_path is None:
            return False, None
        gamertag = next(tag for tag in self.gamertags if _gamertag_key(tag) == key)
        binding = Binding(
            gamertag=gamertag,
            xuid=xuid,
            uuid=str(UUID(player_uuid)),
            bound_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
        updated = dict(self._bindings)
        updated[key] = binding
        _write_bindings(self.bindings_path, tuple(updated.values()))
        self._bindings = updated
        return True, binding
