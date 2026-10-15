"""Read-only diagnostics of saved controller policy, independent of Endstone.

Live decisions come from the plugin's list response. Offline decisions describe
the next start's saved configuration; they never grant a permission.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

from endbot_cli.controllers import ControllersError, ControllerState


def _xuid(value):
    return isinstance(value, str) and value.isascii() and value.isdecimal() and value != "0"


def load_policy(path: Path) -> dict:
    try:
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise ValueError("policy must be a regular file")
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        document = {"version": 1}
    except (OSError, ValueError) as error:
        raise ControllersError(f"{path}: cannot read controller policy: {error}") from error
    try:
        fields = {"grants", "pending", "deniedNames", "deniedXuids", "deniedUuids"}
        if not isinstance(document, dict) or document.get("version") != 1 or set(document) - fields - {"version"}:
            raise ValueError("unsupported controller policy schema/version")
        for field in fields:
            document.setdefault(field, [])
            if not isinstance(document[field], list):
                raise TypeError(f"{field} must be an array")
        for key in ("pending", "deniedNames"):
            if any(
                not isinstance(value, str) or not value.strip() or not value.isprintable() for value in document[key]
            ):
                raise ValueError(f"invalid {key}")
            document[key] = [value.strip() for value in document[key]]
        if any(not _xuid(value) for value in document["deniedXuids"]):
            raise ValueError("invalid deniedXuids")
        document["deniedUuids"] = [str(UUID(value)) for value in document["deniedUuids"]]
        identities = set()
        for grant in document["grants"]:
            if not isinstance(grant, dict) or set(grant) != {"gamertag", "xuid", "uuid", "boundAt"}:
                raise ValueError("malformed controller grant")
            if (
                not isinstance(grant["gamertag"], str)
                or not grant["gamertag"].strip()
                or not grant["gamertag"].isprintable()
                or not _xuid(grant["xuid"])
                or not isinstance(grant["boundAt"], str)
                or grant["xuid"] in identities
            ):
                raise ValueError("invalid or duplicate controller grant")
            grant["uuid"] = str(UUID(grant["uuid"]))
            grant["gamertag"] = grant["gamertag"].strip()
            identities.add(grant["xuid"])
    except (ValueError, TypeError, AttributeError) as error:
        raise ControllersError(f"{path}: invalid controller policy: {error}") from error
    return document


def saved_report(gamertags, state: ControllerState, policy: dict, legacy: dict) -> list[dict]:
    if not isinstance(legacy, dict):
        raise ControllersError("authorization must be a table")
    for key in ("allowed-xuids", "allowed-uuids"):
        values = legacy.get(key, [])
        if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
            raise ControllersError(f"{key} must be an array of strings")
    if any(not value.isascii() or not value.isdecimal() for value in legacy.get("allowed-xuids", [])):
        raise ControllersError("legacy XUIDs must be decimal strings")
    try:
        legacy_uuids = {str(UUID(value)) for value in legacy.get("allowed-uuids", [])}
    except (ValueError, AttributeError) as error:
        raise ControllersError("invalid legacy UUID") from error
    configured = {name.casefold() for name in gamertags}
    bindings = [
        {"gamertag": entry.gamertag, "xuid": entry.xuid, "uuid": entry.uuid}
        for entry in state.bindings
        if entry.gamertag.casefold() in configured
    ]
    # The disk schema uses camelCase; only the identity/name fields matter here.
    grants = policy["grants"]
    denied_names = {value.casefold() for value in policy["deniedNames"]}
    denied_xuids, denied_uuids = set(policy["deniedXuids"]), set(policy["deniedUuids"])
    legacy_xuids = set(legacy.get("allowed-xuids", []))
    rows, seen_xuids, seen_uuids, seen_names = [], set(), set(), set()
    for entry in reversed([*bindings, *grants]):
        xuid, identity, name = entry["xuid"], entry["uuid"], entry["gamertag"]
        if not _xuid(xuid):
            raise ControllersError(f"{name}: invalid bound XUID")
        try:
            identity = str(UUID(identity))
        except (ValueError, TypeError, AttributeError) as error:
            raise ControllersError(f"{name}: invalid bound UUID") from error
        if xuid in seen_xuids:
            continue
        if xuid in denied_xuids:
            reason, allowed = "revoked XUID", False
        elif identity in denied_uuids:
            reason, allowed = "revoked UUID", False
        elif any(grant["xuid"] == xuid and grant["uuid"] not in denied_uuids for grant in grants):
            reason, allowed = "runtime grant", True
        elif name.casefold() in denied_names:
            reason, allowed = "revoked GamerTag", False
        else:
            reason, allowed = "bootstrap binding", True
        rows.append({"name": name, "state": "allowed" if allowed else "revoked", "reason": reason})
        seen_names.add(name.casefold())
        seen_xuids.add(xuid)
        seen_uuids.add(identity)
    for name in (*gamertags, *policy["pending"], *policy["deniedNames"]):
        if name.casefold() not in seen_names:
            revoked = name.casefold() in denied_names
            rows.append(
                {
                    "name": name,
                    "state": "revoked" if revoked else "pending",
                    "reason": "revoked GamerTag" if revoked else "awaiting first Xbox-authenticated join",
                }
            )
            seen_names.add(name.casefold())
    for kind, entries, denied, seen in (
        ("xuid", legacy_xuids, denied_xuids, seen_xuids),
        ("uuid", legacy_uuids, denied_uuids, seen_uuids),
    ):
        for identity in sorted(entries | denied):
            if identity in seen:
                continue
            revoked = identity in denied
            rows.append(
                {
                    "name": f"{kind}:{identity}",
                    "state": "revoked" if revoked else "conditional",
                    "reason": f"revoked {kind.upper()}"
                    if revoked
                    else f"legacy {kind.upper()} allowlist; requires Xbox authentication and identity checks",
                }
            )
    return sorted(rows, key=lambda row: row["name"].casefold())
