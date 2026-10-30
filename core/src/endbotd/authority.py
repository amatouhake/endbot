"""Who may control Bots: enrollment, controller policy and the two-layer check (docs/ENDBOTD.md P4).

This is the authorization the plugin used to hold, driven by presence reports
instead of Endstone players. A player is a presence record
(``playerId``, ``name``, ``xuid``, ``revision``). Every method runs under one
lock, which takes the place of the BDS server thread as the point that orders
authorization changes.

Methods that change who may control return the control permissions to apply as
``(playerId, allowed)`` pairs; the caller sends them to the plugin.
"""

from __future__ import annotations

import shlex
import threading
from collections.abc import Callable
from pathlib import Path

from endbot_core.authorization import AllowedPlayers
from endbot_core.controller_policy import ControllerPolicy
from endbot_core.controller_roles import FILE_NAME as ROLES_FILE
from endbot_core.controller_roles import ROLES, ControllerRoles
from endbot_core.enrollment import ControllerEnrollment, _is_xuid
from endbot_core.grant_revisions import FILE_NAME as GRANT_REVISIONS_FILE
from endbot_core.grant_revisions import GrantRevisions
from endbot_core.link import Presence
from endbot_core.principal import Principal

Permissions = list[tuple[str, bool]]


def _describe(row: dict) -> str:
    line = f"{row['name']}: {row['state']} - {row['reason']}"
    if row.get("role"):
        line += f" (role {row['role']})"
    return line


class Authority:
    def __init__(
        self,
        presence: Presence,
        *,
        allowed: AllowedPlayers | None = None,
        controllers: ControllerEnrollment | None = None,
        policy: ControllerPolicy,
        grant_revisions: GrantRevisions | None = None,
        roles: ControllerRoles | None = None,
        log: Callable[[str, str], None] | None = None,
    ) -> None:
        self.presence = presence
        self.allowed = allowed or AllowedPlayers()
        self.controllers = controllers or ControllerEnrollment((), None)
        self.policy = policy
        # Beside the policy, so an Endbot instance keeps it in state/; read only here, at start-up.
        self.grant_revisions = grant_revisions or GrantRevisions(policy.path.parent / GRANT_REVISIONS_FILE)
        self.roles = roles or ControllerRoles(policy.path.parent / ROLES_FILE)
        self._log = log or (lambda level, message: None)
        self.lock = threading.RLock()
        # The control permission last sent per online player.
        self._granted: dict[str, bool] = {}

    @classmethod
    def from_config(cls, presence: Presence, authorization: dict, base: Path, log=None) -> Authority:
        controllers = ControllerEnrollment.from_config(authorization, base)
        policy = ControllerPolicy.from_config(authorization, base)
        if controllers.bindings_path is not None and controllers.bindings_path.resolve() == policy.path.resolve():
            raise ValueError("controllers-file and controller-policy-file must be different files")
        return cls(presence, allowed=AllowedPlayers.from_config(authorization), controllers=controllers,
                   policy=policy, log=log)

    # -- decisions -----------------------------------------------------------

    def control_decision(self, name: str, xuid: str, player_id: str) -> tuple[bool, str]:
        if not _is_xuid(xuid):
            return False, "Xbox authentication required"
        policy = self.policy
        if xuid in policy.state.denied_xuids:
            return False, "revoked XUID"
        if player_id in policy.state.denied_uuids:
            return False, "revoked UUID"
        if policy.grants(xuid):
            return True, "runtime grant"
        if policy.name_denied(name):
            return False, "revoked GamerTag"
        if any(binding.xuid == xuid for binding in self.controllers.bindings):
            return True, "bootstrap binding"
        if xuid in self.allowed.xuids:
            return True, "legacy XUID allowlist"
        if player_id in self.allowed.uuids:
            return True, "legacy UUID allowlist"
        return False, "not enrolled"

    def allows(self, record: dict) -> bool:
        return self.control_decision(record.get("name") or "", record.get("xuid") or "", record["playerId"])[0]

    def role(self, xuid: str) -> str:
        """The stored role for ``xuid``; a grant with no entry is a scripter."""
        return self.roles.role(xuid)

    def authorize(self, principal: Principal) -> bool:
        """Two layers for every principal.

        Every principal except the console needs a grant at the grant revision
        it captured, so a revoke and regrant never revive older work. Then an
        in-game player must still be online with an unchanged session revision;
        runs and jobs outlive their owner's session and are checked by the
        identity captured when they started. No plugin link means nobody is
        online, so only runs, jobs and the console can pass.
        """
        if principal.console:
            return True
        with self.lock:
            if principal.player_id is None or principal.grant_revision != self.grant_revisions.get(
                    principal.player_id):
                return False
            record = self.presence.player(principal.player_id)
            if principal.kind == "player":
                return (record is not None and self.allows(record)
                        and principal.authorization_revision == record["revision"])
            if record is not None:
                return self.allows(record)
            return self.control_decision(principal.name or "", principal.xuid or "", principal.player_id)[0]

    # -- presence ------------------------------------------------------------

    def joined(self, records: list[dict], *, snapshot: bool = False) -> Permissions:
        """Enroll and bind joining players; first Xbox-authenticated joins bind GamerTags."""
        with self.lock:
            if snapshot:
                self._granted = {}
            permissions = []
            for record in records:
                name, xuid, player_id = record.get("name") or "", record.get("xuid") or "", record["playerId"]
                self.policy.enroll(name, xuid, player_id)
                if _is_xuid(xuid) and not self.policy.denied(xuid, player_id) and not self.policy.name_denied(name):
                    _, binding = self.controllers.authorize(name, xuid, player_id)
                    if binding is not None:
                        self._log("info", f"Bound controller {binding.gamertag} through Xbox authentication.")
                if _is_xuid(xuid):
                    # A role granted before this GamerTag joined travels to its XUID now.
                    pending = self.roles.pending_role(name)
                    if pending is not None and self.allows(record):
                        self.roles.set(xuid, pending)
                        self.roles.forget_pending([name])
                permissions.append(self._apply(player_id))
            return permissions

    def quit(self, player_id: str) -> None:
        with self.lock:
            self._granted.pop(player_id, None)

    def _apply(self, player_id: str) -> tuple[str, bool]:
        record = self.presence.player(player_id)
        allowed = record is not None and self.allows(record)
        if self._granted.get(player_id, False) != allowed:
            # A permission change ends commands queued under the old session revision.
            self.presence.bump(player_id)
        self._granted[player_id] = allowed
        return player_id, allowed

    def refresh(self) -> Permissions:
        return [self._apply(record["playerId"]) for record in self.presence.online()]

    # -- the console's botcontrollers ---------------------------------------

    def manage(self, args: list[str]) -> tuple[list[str], Permissions]:
        """Run ``botcontrollers`` from the console; returns its lines and permission changes."""
        result, permissions = self.operation(args)
        return list(result["messages"]), permissions

    def operation(self, args: list[str], target: str | None = None,
                  role: str | None = None) -> tuple[dict, Permissions]:
        """Run a console operation; ``target`` is an already-validated whole name."""
        with self.lock:
            try:
                return self._operation(args, target, role)
            except (OSError, ValueError, TypeError) as error:
                return {"ok": False, "messages": [f"Endbot: controller change failed: {error}"]}, []

    def _operation(self, args: list[str], target: str | None, role: str | None = None) -> tuple[dict, Permissions]:
        if not args or args == ["list"]:
            rows = self.report()
            messages = ["Endbot controllers (live effective authorization):"]
            messages.extend(_describe(row) + (" (online)" if row.get("online") else "") for row in rows)
            if not rows:
                messages.append("none; the server console remains authorized")
            return {"ok": True, "messages": messages, "entries": rows}, []
        operation = args[0].lower()
        if target is None and len(args) >= 2:
            # Console text, which BDS may pass as one argument: quoted names may contain spaces, and the role
            # option follows the GamerTag.
            words = shlex.split(" ".join(args[1:]))
            if operation == "add" and len(words) >= 3 and words[-2] == "--role":
                if role is None:
                    role = words[-1]
                words = words[:-2]
            target = " ".join(words)
        if operation == "add" and role is not None and role not in ROLES:
            raise ValueError("role must be scripter or controller")
        if operation not in {"add", "remove"} or not target:
            raise ValueError(
                "Use botcontrollers list|add <GamerTag> [--role scripter|controller]|remove "
                "<GamerTag|xuid:id|uuid:id>"
            )
        player = next((record for record in self.presence.online()
                       if (record.get("name") or "").casefold() == target.casefold()), None)
        if operation == "add":
            binding = next((entry for entry in (*self.controllers.bindings, *self.policy.state.grants)
                            if entry.gamertag.casefold() == target.casefold()), None)
            # Decide before the add: afterwards every new grant would look allowed.
            existing = next((row for row in self.report()
                             if row["name"].casefold() == target.casefold() and row["state"] == "allowed"), None)
            xuid = player.get("xuid") if player is not None else binding.xuid if binding is not None else None
            if role is not None:
                effective = role
            elif existing is None:
                effective = "controller"
            else:
                effective = None  # An existing grant keeps its role unless --role is given.
            # The role is written before the grant: if a write fails, a new grant never exists without its role,
            # which would make it a scripter (the no-entry default).
            if effective is not None:
                if _is_xuid(xuid):
                    self.roles.set(xuid, effective)
                    self.roles.forget_pending([target])
                else:
                    self.roles.set_pending(target, effective)
            if player is not None:
                self.policy.add(target, xuid=player.get("xuid") or "", player_uuid=player["playerId"])
            elif binding is not None:
                self.policy.add(target, xuid=binding.xuid, player_uuid=binding.uuid)
            else:
                self.policy.add(target)
        else:
            # Separate grant revisions survive logout/rejoin and restarts. A revoke/regrant
            # never revives a run holding the old revision, including offline runs.
            before = self.report()
            self.policy.remove(
                target,
                known=self.controllers.bindings,
                configured=self.controllers.gamertags,
                xuid=(player.get("xuid") or "") if player is not None else None,
                player_uuid=player["playerId"] if player is not None else None,
            )
            denied = self.policy.state
            self.grant_revisions.bump(
                row["uuid"]
                for row in before
                if row.get("uuid")
                and (row.get("xuid") in denied.denied_xuids
                     or row.get("uuid") in denied.denied_uuids
                     or row.get("name", "").casefold() in denied.denied_names)
            )
            self.roles.forget(
                row.get("xuid")
                for row in before
                if row.get("xuid")
                and (row.get("xuid") in denied.denied_xuids
                     or row.get("uuid") in denied.denied_uuids
                     or row.get("name", "").casefold() in denied.denied_names)
            )
            self.roles.forget_pending([target])
        permissions = self.refresh()
        state = "updated" if operation == "add" else "revoked"
        rows = self.report()
        details = [_describe(row) for row in rows if row["name"].casefold() == target.casefold()]
        result = {"ok": True, "messages": [f"Endbot: controller {target} {state}", *details], "entries": rows}
        return result, permissions

    def report(self) -> list[dict]:
        policy = self.policy
        rows, seen_xuids, seen_names, seen_uuids = [], set(), set(), set()
        online = self.presence.online()
        bindings = (*self.controllers.bindings, *policy.state.grants)
        # Prefer the latest runtime-bound name, then the authenticated online name.
        for binding in reversed(bindings):
            if binding.xuid in seen_xuids:
                continue
            player = next((entry for entry in online if entry.get("xuid") == binding.xuid), None)
            name = (player.get("name") or binding.gamertag) if player is not None else binding.gamertag
            identity = player["playerId"] if player is not None else binding.uuid
            allowed, reason = self.control_decision(name, binding.xuid, identity)
            row = {"name": name, "state": "allowed" if allowed else "revoked", "reason": reason,
                   "xuid": binding.xuid, "uuid": identity, "online": player is not None}
            if allowed:
                row["role"] = self.roles.role(binding.xuid)
            rows.append(row)
            seen_xuids.add(binding.xuid)
            seen_uuids.add(identity)
            seen_names.add(binding.gamertag.casefold())
            seen_names.add(name.casefold())
        for player in online:
            xuid, identity, name = player.get("xuid") or "", player["playerId"], player.get("name") or ""
            if not _is_xuid(xuid) or xuid in seen_xuids:
                continue
            allowed, reason = self.control_decision(name, xuid, identity)
            row = {"name": name, "state": "allowed" if allowed else "denied", "reason": reason,
                   "xuid": xuid, "uuid": identity, "online": True}
            if allowed:
                row["role"] = self.roles.role(xuid)
            rows.append(row)
            seen_xuids.add(xuid)
            seen_uuids.add(identity)
            seen_names.add(name.casefold())
        for name in (*self.controllers.pending(), *policy.state.pending, *policy.state.denied_names):
            if name.casefold() in seen_names:
                continue
            revoked = policy.name_denied(name)
            row = {"name": name, "state": "revoked" if revoked else "pending",
                   "reason": "revoked GamerTag" if revoked else "awaiting first Xbox-authenticated join"}
            if not revoked:
                row["role"] = self.roles.pending_role(name) or "scripter"
            rows.append(row)
            seen_names.add(name.casefold())
        for kind, configured, denied, seen in (
            ("xuid", self.allowed.xuids, policy.state.denied_xuids, seen_xuids),
            ("uuid", self.allowed.uuids, policy.state.denied_uuids, seen_uuids),
        ):
            for identity in sorted(set(configured) | set(denied)):
                if identity in seen:
                    continue
                revoked = identity in denied
                rows.append({
                    "name": f"{kind}:{identity}",
                    "state": "revoked" if revoked else "conditional",
                    "reason": f"revoked {kind.upper()}" if revoked
                    else f"legacy {kind.upper()} allowlist; requires Xbox authentication and identity checks",
                })
        return sorted(rows, key=lambda row: row["name"].casefold())
