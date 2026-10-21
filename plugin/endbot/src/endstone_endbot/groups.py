"""Small shared-group prototype; no world state or runtime credentials are stored."""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from uuid import UUID, uuid4

from endstone_endbot.commands import (
    ERROR,
    BotCommandService,
    CommandResult,
    _language,
    _tokens,
    parse_command,
    reply,
)
from endstone_endbot.control import RuntimeControlError

GROUP_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,15}\Z")
GROUP_TARGET = re.compile(r"#([A-Za-z_][A-Za-z0-9_]{0,15})(?:\[([1-9][0-9]*)\])?\Z")
GROUP_INPUT_OPERATIONS = frozenset({"move", "look", "action", "flag", "stop", "hotbar", "drop"})


def group_name(value: str) -> str:
    if not isinstance(value, str) or not GROUP_NAME.fullmatch(value):
        raise ValueError(
            "Group names must start with a letter/underscore and contain 1-16 letters, digits or underscores"
        )
    return value


def group_reference(value: str) -> str:
    """Parse a whole-group command reference; persistence keeps the bare name."""
    if not value.startswith("#") or not GROUP_NAME.fullmatch(value[1:]):
        raise ValueError("Group syntax: #group (for example #miners)")
    return value[1:]


@dataclass(frozen=True)
class Group:
    identity: str
    name: str
    members: tuple[str, ...] = ()


class GroupStore:
    """One plugin-owned atomic file. Publish memory changes only after a successful save."""

    def __init__(self, path: Path):
        self.path = path
        self.groups: tuple[Group, ...] = ()
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise ValueError("Groups must be stored in a regular file")
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        if (not isinstance(document, dict) or document.get("version") != 1
                or not isinstance(document.get("groups"), list)):
            raise ValueError("Invalid groups file")
        groups = []
        for row in document["groups"]:
            if not isinstance(row, dict) or not isinstance(row.get("members"), list):
                raise ValueError("Invalid group entry")  # noqa: TRY004 - persisted JSON schema validation
            identity = str(UUID(row["identityId"]))
            name = group_name(row["name"])
            members = tuple(str(UUID(member)) for member in row["members"])
            if len(set(members)) != len(members):
                raise ValueError("Duplicate group member")
            groups.append(Group(identity, name, members))
        if len({group.name.casefold() for group in groups}) != len(groups):
            raise ValueError("Duplicate group name")
        if len({group.identity for group in groups}) != len(groups):
            raise ValueError("Duplicate group identity")
        self.groups = tuple(groups)

    def get(self, name: str) -> Group:
        group_name(name)
        for group in self.groups:
            if group.name.casefold() == name.casefold():
                return group
        raise ValueError(f"Group {name} does not exist")

    def _save(self, groups: tuple[Group, ...]) -> None:
        if self.path.is_symlink():
            raise ValueError("Groups file must not be a symlink")
        document = {"version": 1, "groups": [
            {"identityId": group.identity, "name": group.name, "members": list(group.members)} for group in groups
        ]}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".groups-", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(document, stream, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            Path(temporary).unlink(missing_ok=True)
        self.groups = groups

    def create(self, name: str) -> Group:
        group_name(name)
        if any(group.name.casefold() == name.casefold() for group in self.groups):
            raise ValueError(f"Group {name} already exists")
        group = Group(str(uuid4()), name)
        self._save((*self.groups, group))
        return group

    def delete(self, name: str) -> None:
        group = self.get(name)
        self._save(tuple(entry for entry in self.groups if entry.identity != group.identity))

    def add(self, name: str, identity: str) -> None:
        group = self.get(name)
        identity = str(UUID(identity))
        if identity in group.members:
            raise ValueError("Bot is already a group member")
        self._replace(replace(group, members=(*group.members, identity)))

    def remove(self, name: str, identity: str) -> None:
        group = self.get(name)
        if identity not in group.members:
            raise ValueError("Bot is not a group member")
        self._replace(replace(group, members=tuple(member for member in group.members if member != identity)))

    def _replace(self, group: Group) -> None:
        self._save(tuple(group if entry.identity == group.identity else entry for entry in self.groups))

    def select(self, target: str) -> tuple[Group, tuple[tuple[int, str], ...], bool]:
        match = GROUP_TARGET.fullmatch(target)
        if match is None:
            raise ValueError("Group target syntax: #group or #group[1] (one-based)")
        group = self.get(match[1])
        indexed = match[2] is not None
        if indexed:
            index = int(match[2])
            if index > len(group.members):
                raise ValueError(f"{target}: member index is out of range")
            members = ((index, group.members[index - 1]),)
        else:
            members = tuple(enumerate(group.members, start=1))
        return group, members, indexed


class _MemberControl:
    """Pin name-based IPC to a UUID and recheck controller authority before every request."""

    def __init__(self, control, identity: str, authorize):
        self.control, self.identity, self.authorize = control, identity, authorize

    def request(self, operation, **parameters):
        self.authorize()
        return self.control.request(operation, **parameters, expectedIdentityId=self.identity)


class _BatchReplyControl:
    """Render a completed batch member through the ordinary command reply path."""

    def __init__(self, method, response):
        self.method, self.response = method, response

    def request(self, operation, **parameters):
        if operation != self.method:
            raise RuntimeError("Unexpected request while rendering group input result")
        if not self.response.get("ok"):
            error = self.response["error"]
            raise RuntimeControlError(error["code"], error["message"])
        return self.response["result"]


class GroupCommandService(BotCommandService):
    """Command-first prototype. Existing Bot actions remain the implementation of each operation."""

    def __init__(self, control, world, groups: GroupStore, authorize):
        super().__init__(control, world)
        self.groups, self.authorize = groups, authorize
        self._groups_open = True

    def close(self):
        self.cancel_groups()
        super().close()

    def cancel_groups(self):
        self._groups_open = False

    def _check(self, sender):
        if not self._groups_open or not self.authorize(sender):
            raise ValueError("Bot control permission was revoked; command canceled")

    def _profiles(self, sender, *, batch=False) -> dict[str, dict]:
        self._check(sender)
        try:
            capabilities = self.control.request("capabilities")
        except RuntimeControlError as error:
            if error.code != "unknown_method":
                raise
            raise RuntimeError("Group prototype requires the matching updated Endbot runtime") from error
        if (capabilities.get("identityPinnedRequests") is not True
                or (batch and capabilities.get("groupInputBatch") is not True)):
            raise RuntimeError("Group prototype requires the matching updated Endbot runtime")
        self._check(sender)
        profiles = {bot["identityId"]: bot for bot in self.control.request("list")}
        self._check(sender)
        return profiles

    @staticmethod
    def _error(error, target=None):
        return CommandResult(True, (reply(str(error), target, ERROR),))

    def execute_group(self, arguments, sender=None):
        """Arguments following /botgroup. Runs on the same authorized, ordered worker as /bot."""
        try:
            self._check(sender)
            tokens = _tokens(arguments)
            operation = tokens[0].lower() if tokens else "help"
            tail = tokens[1:]
            if operation == "help" and not tail:
                return CommandResult(True, tuple(reply(line) for line in (
                    "/botgroup list [#group] | create #group | delete #group",
                    "/botgroup add #group <bot> | remove #group <bot-or-#group[index]>",
                    "/bot #group <operation> | /bot #group[1] <operation> (one-based)",
                )))
            if operation == "list" and not tail:
                names = ", ".join(f"#{group.name} ({len(group.members)})" for group in self.groups.groups)
                return CommandResult(True, (reply(names or "no groups"),))
            if operation in {"create", "delete", "list"} and len(tail) == 1:
                name = group_reference(tail[0])
                if operation == "create":
                    group = self.groups.create(name)
                    return CommandResult(True, (reply("group created", f"#{group.name}"),))
                if operation == "delete":
                    self.groups.delete(name)
                    return CommandResult(True, (reply("group deleted; Bots retained", f"#{name}"),))
                group = self.groups.get(name)
                profiles = self._profiles(sender)
                rows = []
                for index, identity in enumerate(group.members, start=1):
                    bot = profiles.get(identity)
                    label = f"{bot['name']} [{bot['connectionState']}]" if bot else "missing Bot profile"
                    rows.append(reply(label, f"#{group.name}[{index}]"))
                return CommandResult(True, tuple(rows) or (reply("no members", f"#{group.name}"),))
            if operation in {"add", "remove"} and len(tail) == 2:
                name, target = group_reference(tail[0]), tail[1]
                group = self.groups.get(name)
                if operation == "remove" and target.startswith("#"):
                    selected_group, selected, indexed = self.groups.select(target)
                    if not indexed or selected_group.identity != group.identity:
                        raise ValueError("Remove requires a Bot name or one indexed member of this group")
                    identity = selected[0][1]
                else:
                    profiles = self._profiles(sender)
                    bot = next((bot for bot in profiles.values() if bot["name"].casefold() == target.casefold()), None)
                    if bot is None:
                        raise ValueError(f"Bot {target} does not exist; add an existing profile")
                    identity = bot["identityId"]
                self._check(sender)
                getattr(self.groups, operation)(name, identity)
                return CommandResult(True, (reply(f"{operation}: {target}", f"#{group.name}"),))
            raise ValueError("Invalid group command; use /botgroup help")
        except (RuntimeError, ValueError, OSError) as error:
            return self._error(error)

    def _execute_inputs(self, command, sender, group, members):
        profiles = self._profiles(sender, batch=True)
        prepared, requests = [], []
        for index, identity in members:
            self._check(sender)
            target = f"#{group.name}[{index}]"
            try:
                bot = profiles.get(identity)
                if bot is None:
                    raise ValueError("missing Bot profile; membership retained")
                member = replace(command, name=bot["name"])
                if command.operation == "look" and "targetCoordinates" in command.parameters:
                    yaw, pitch = self.world.look_at(identity, command.parameters["targetCoordinates"])
                    member = replace(member, parameters={"yaw": yaw, "pitch": pitch})
                requests.append({"method": member.operation, "params": {
                    **member.parameters, "name": member.name, "expectedIdentityId": identity,
                }})
                prepared.append((target, member, None))
            except (RuntimeError, ValueError) as error:
                prepared.append((target, None, error))
        # One authorization boundary immediately before the synchronous runtime
        # batch starts. Revocation during preparation prevents the entire batch.
        # Once accepted, immediate inputs are applied without per-member waits;
        # later revocation does not roll back already accepted inputs.
        self._check(sender)
        responses = self.control.request("groupInputBatch", requests=requests)["results"] if requests else []
        if len(responses) != len(requests):
            raise RuntimeError("Invalid group input batch response")
        responses = iter(responses)
        messages = []
        for target, member, error in prepared:
            if error is not None:
                messages.append(reply(str(error), target, ERROR))
                continue
            try:
                result = self._execute(member, sender, _language(sender),
                                       control=_BatchReplyControl(member.operation, next(responses)))
                messages.extend(result.messages)
            except (RuntimeError, ValueError) as error:
                messages.append(reply(str(error), target, ERROR))
        return CommandResult(True, tuple(messages))

    def execute(self, arguments, sender=None):
        try:
            tokens = _tokens(arguments)
            if not tokens or not tokens[0].startswith("#"):
                return super().execute(arguments, sender)
            command = parse_command(arguments)
            group, members, indexed = self.groups.select(tokens[0])
            if not indexed and command.operation in {"rename", "forget", "menu"}:
                raise ValueError(f"{command.operation} requires one Bot; use #group[1] or a Bot name")
            if not members:
                raise ValueError("Group has no members")
            if not indexed and command.operation in GROUP_INPUT_OPERATIONS:
                return self._execute_inputs(command, sender, group, members)
            messages, logs, log_level, view = [], [], None, None
            for index, identity in members:
                target = f"#{group.name}[{index}]"
                try:
                    profiles = self._profiles(sender)
                    bot = profiles.get(identity)
                    if bot is None:
                        raise ValueError("missing Bot profile; membership retained")
                    result = self._execute(
                        replace(command, name=bot["name"]), sender, _language(sender),
                        control=_MemberControl(self.control, identity, lambda: self._check(sender)),
                    )
                    messages.extend(result.messages)
                    logs.extend(result.log_messages or result.messages)
                    log_level = result.log_level or log_level
                    view = result.view
                except (RuntimeError, ValueError) as error:
                    message = reply(str(error), target, ERROR)
                    messages.append(message)
                    logs.append(message)
                    # Keep earlier per-Bot results when revocation/shutdown cancels the remainder.
                    try:
                        self._check(sender)
                    except (RuntimeError, ValueError):
                        break
            return CommandResult(True, tuple(messages), log_level=log_level, log_messages=tuple(logs), view=view)
        except (RuntimeError, ValueError) as error:
            return self._error(error, arguments[0] if arguments else None)
