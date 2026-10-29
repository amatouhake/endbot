"""Persistent, console-managed overrides of bootstrap controller configuration.

The plugin is the sole writer. One atomic replacement commits grants, pending
enrollment and revocations together; operator configuration is never rewritten.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from endbot_core.enrollment import Binding, _is_xuid


def controller_name(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or not value.isprintable():
        raise ValueError("Controller GamerTag must be non-empty printable text")
    return value.strip()


@dataclass(frozen=True)
class Policy:
    grants: tuple[Binding, ...] = ()
    pending: tuple[str, ...] = ()
    denied_names: tuple[str, ...] = ()
    denied_xuids: tuple[str, ...] = ()
    denied_uuids: tuple[str, ...] = ()


def _strings(document: dict, key: str) -> tuple[str, ...]:
    values = document.get(key, [])
    if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
        raise ValueError(f"Controller policy {key} must be an array of strings")
    return tuple(values)


def load_policy(path: Path) -> Policy:
    try:
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise ValueError("Controller policy must be a regular file")
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return Policy()
    if not isinstance(document, dict) or document.get("version") != 1:
        raise ValueError("Unsupported controller policy version")
    if set(document) - {"version", "grants", "pending", "deniedNames", "deniedXuids", "deniedUuids"}:
        raise ValueError("Unknown controller policy field")
    grants = document.get("grants", [])
    if not isinstance(grants, list):
        raise TypeError("Controller policy grants must be an array")
    bindings = []
    for entry in grants:
        if not isinstance(entry, dict) or set(entry) != {"gamertag", "xuid", "uuid", "boundAt"}:
            raise ValueError("Malformed controller policy grant")
        if not _is_xuid(entry["xuid"]) or not isinstance(entry["boundAt"], str):
            raise ValueError("Malformed controller policy identity")
        bindings.append(
            Binding(controller_name(entry["gamertag"]), entry["xuid"], str(UUID(entry["uuid"])), entry["boundAt"])
        )
    if len({binding.xuid for binding in bindings}) != len(bindings):
        raise ValueError("Duplicate controller policy XUID")
    pending = tuple(controller_name(value) for value in _strings(document, "pending"))
    denied_names = tuple(controller_name(value) for value in _strings(document, "deniedNames"))
    denied_xuids = _strings(document, "deniedXuids")
    if any(not _is_xuid(value) for value in denied_xuids):
        raise ValueError("Invalid revoked controller XUID")
    denied_uuids = tuple(str(UUID(value)) for value in _strings(document, "deniedUuids"))
    return Policy(tuple(bindings), pending, denied_names, denied_xuids, denied_uuids)


class ControllerPolicy:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.state = load_policy(path)

    @classmethod
    def from_config(cls, config: dict, data_folder: Path) -> ControllerPolicy:
        value = config.get("controller-policy-file", "controller-policy.json")
        if not isinstance(value, str) or not value:
            raise ValueError("controller-policy-file must be a non-empty string")
        path = Path(value)
        return cls(path if path.is_absolute() else data_folder / path)

    def _save(self, state: Policy) -> None:
        if self.path.is_symlink():
            raise ValueError("Controller policy must not be a symlink")
        document = {
            "version": 1,
            "grants": [binding.to_json() for binding in state.grants],
            "pending": list(state.pending),
            "deniedNames": list(state.denied_names),
            "deniedXuids": list(state.denied_xuids),
            "deniedUuids": list(state.denied_uuids),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".controller-policy-", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(document, stream, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            Path(temporary).unlink(missing_ok=True)
        self.state = state

    def denied(self, xuid: str, player_uuid: str) -> bool:
        return xuid in self.state.denied_xuids or str(UUID(player_uuid)) in self.state.denied_uuids

    def name_denied(self, name: str) -> bool:
        return name.casefold() in {value.casefold() for value in self.state.denied_names}

    def grants(self, xuid: str) -> bool:
        return _is_xuid(xuid) and any(
            binding.xuid == xuid and not self.denied(binding.xuid, binding.uuid) for binding in self.state.grants
        )

    def add(self, name: str, *, xuid: str | None = None, player_uuid: str | None = None) -> None:
        name = controller_name(name)
        if name.lower().startswith(("xuid:", "uuid:")):
            raise ValueError("Add a GamerTag; identity selectors are for removal only")
        state = self.state
        if xuid is None:
            existing = next((entry for entry in state.grants if entry.gamertag.casefold() == name.casefold()), None)
            if existing is not None:
                self.add(name, xuid=existing.xuid, player_uuid=existing.uuid)
                return
        pending = tuple(value for value in state.pending if value.casefold() != name.casefold())
        denied_names = tuple(value for value in state.denied_names if value.casefold() != name.casefold())
        if xuid is None:
            self._save(replace(state, pending=(*pending, name), denied_names=denied_names))
            return
        if not _is_xuid(xuid) or player_uuid is None:
            raise ValueError("Only normally authenticated players with an XUID can become controllers")
        identity = str(UUID(player_uuid))
        binding = Binding(name, xuid, identity, datetime.now(timezone.utc).isoformat(timespec="seconds"))
        self._save(
            replace(
                state,
                grants=(*(entry for entry in state.grants if entry.xuid != xuid), binding),
                pending=pending,
                denied_names=denied_names,
                denied_xuids=tuple(value for value in state.denied_xuids if value != xuid),
                denied_uuids=tuple(value for value in state.denied_uuids if value != identity),
            )
        )

    def enroll(self, name: str, xuid: str, player_uuid: str) -> bool:
        if not _is_xuid(xuid) or name.casefold() not in {value.casefold() for value in self.state.pending}:
            return False
        # A claimable GamerTag must never lift an explicit identity revocation;
        # only a console add naming that account can.
        if self.denied(xuid, player_uuid):
            return False
        self.add(name, xuid=xuid, player_uuid=player_uuid)
        return True

    def remove(
        self,
        target: str,
        *,
        known: tuple[Binding, ...],
        configured: tuple[str, ...],
        xuid: str | None = None,
        player_uuid: str | None = None,
    ) -> None:
        target = controller_name(target)
        xuids, uuids, names = set(), set(), {target.casefold()}
        if target.lower().startswith("xuid:"):
            names.clear()
            identity = target[5:]
            if not _is_xuid(identity):
                raise ValueError("Invalid XUID selector")
            xuids.add(identity)
        elif target.lower().startswith("uuid:"):
            names.clear()
            uuids.add(str(UUID(target[5:])))
        if xuid is not None:
            if not _is_xuid(xuid) or player_uuid is None:
                raise ValueError("Local Bots cannot be controller targets")
            xuids.add(xuid)
            uuids.add(str(UUID(player_uuid)))
        bindings = (*known, *self.state.grants)
        # Resolve aliases by stable identity, including a bound account's old GamerTag.
        for _ in range(2):
            for entry in bindings:
                if entry.gamertag.casefold() in names or entry.xuid in xuids or entry.uuid in uuids:
                    names.add(entry.gamertag.casefold())
                    xuids.add(entry.xuid)
                    uuids.add(entry.uuid)
        pending_names = {value.casefold() for value in (*configured, *self.state.pending, *self.state.denied_names)}
        if not xuids and not uuids and target.casefold() not in pending_names:
            raise ValueError("Unknown offline controller; use its bound GamerTag, xuid:<id>, or uuid:<id>")
        self._save(
            replace(
                self.state,
                # Retain bindings after revocation. An offline regrant must not
                # turn a known account back into claimable name-only enrollment.
                pending=tuple(value for value in self.state.pending if value.casefold() not in names),
                denied_names=tuple(sorted(set(self.state.denied_names) | names)),
                denied_xuids=tuple(sorted(set(self.state.denied_xuids) | xuids)),
                denied_uuids=tuple(sorted(set(self.state.denied_uuids) | uuids)),
            )
        )
