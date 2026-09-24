"""Controller enrollment state (``state/controllers.json``, docs/OPERATIONS.md section 3).

Schema (generated and maintained by the plugin; the CLI only reads it)::

    {"version": 1, "bindings": [{"gamertag": ..., "xuid": ..., "uuid": ..., "boundAt": ...}]}

A missing file means nothing is bound yet and every configured GamerTag is
pending. A corrupt or off-schema file fails closed: it is never treated as
"nothing bound" silently, because that would misreport bound controllers.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from endbot_cli.fsutil import atomic_write_text

CONTROLLERS_VERSION = 1
BINDING_KEYS = ("gamertag", "xuid", "uuid", "boundAt")


class ControllersError(ValueError):
    """``state/controllers.json`` is unreadable or off-schema."""


@dataclass(frozen=True, slots=True)
class ControllerBinding:
    gamertag: str
    xuid: str
    uuid: str
    bound_at: str


@dataclass(frozen=True, slots=True)
class ControllerState:
    version: int = CONTROLLERS_VERSION
    bindings: tuple[ControllerBinding, ...] = ()

    def find(self, gamertag: str) -> ControllerBinding | None:
        """Return the binding for ``gamertag`` (case-insensitive, section 3) if any."""

        wanted = gamertag.casefold()
        for binding in self.bindings:
            if binding.gamertag.casefold() == wanted:
                return binding
        return None


def _load_binding(path: Path, index: int, entry: object) -> ControllerBinding:
    if not isinstance(entry, dict):
        raise ControllersError(f"{path}: bindings[{index}] must be a table with keys {', '.join(BINDING_KEYS)}")
    for key in entry:
        if key not in BINDING_KEYS:
            raise ControllersError(
                f"{path}: bindings[{index}].{key} is not recognized; "
                f"remove it or fix the spelling (known keys: {', '.join(BINDING_KEYS)})"
            )
    values: dict[str, str] = {}
    for key in BINDING_KEYS:
        if key not in entry:
            raise ControllersError(
                f"{path}: bindings[{index}].{key} is missing; add it or delete the binding to re-enroll"
            )
        value = entry[key]
        if not isinstance(value, str):
            raise ControllersError(f"{path}: bindings[{index}].{key} must be a string")
        if key != "boundAt" and not value:
            raise ControllersError(f"{path}: bindings[{index}].{key} must not be empty")
        values[key] = value
    return ControllerBinding(
        gamertag=values["gamertag"], xuid=values["xuid"], uuid=values["uuid"], bound_at=values["boundAt"]
    )


def load_controllers(path: Path) -> ControllerState:
    """Load ``state/controllers.json``; a missing file yields an empty state."""

    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return ControllerState()
    except OSError as error:
        raise ControllersError(f"{path}: file cannot be read: {error}; fix the file permissions") from error
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise ControllersError(
            f"{path}: invalid JSON: {error}; fix or delete the file to re-enroll all controllers"
        ) from error
    if not isinstance(document, dict):
        raise ControllersError(f"{path}: top level must be an object with keys version, bindings")
    for key in document:
        if key not in ("version", "bindings"):
            raise ControllersError(
                f"{path}: key {key!r} is not recognized; remove it or fix the spelling (known keys: version, bindings)"
            )
    version = document.get("version")
    if version != CONTROLLERS_VERSION:
        raise ControllersError(f"{path}: key 'version' must be {CONTROLLERS_VERSION}, found {version!r}")
    entries = document.get("bindings", [])
    if not isinstance(entries, list):
        raise ControllersError(f"{path}: key 'bindings' must be an array")
    return ControllerState(
        version=CONTROLLERS_VERSION,
        bindings=tuple(_load_binding(path, index, entry) for index, entry in enumerate(entries)),
    )


def remove_binding(state: ControllerState, gamertag: str) -> ControllerState:
    """Return ``state`` without the binding for ``gamertag`` (case-insensitive, section 3).

    The GamerTag returns to pending and re-binds to its XUID on the next join.
    """

    binding = state.find(gamertag)
    if binding is None:
        return state
    return ControllerState(
        version=state.version,
        bindings=tuple(entry for entry in state.bindings if entry is not binding),
    )


def save_controllers(path: Path, state: ControllerState) -> None:
    """Write ``state/controllers.json`` atomically in the section 3 schema."""

    document = {
        "version": state.version,
        "bindings": [
            {"gamertag": entry.gamertag, "xuid": entry.xuid, "uuid": entry.uuid, "boundAt": entry.bound_at}
            for entry in state.bindings
        ],
    }
    atomic_write_text(path, json.dumps(document, indent=2) + "\n")
