"""Shared Bot groups; no world state or runtime credentials are stored."""

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
    strip_colors,
)
from endstone_endbot.control import RuntimeControlError
from endstone_endbot.forms import BotListView, BotView, GroupTarget, Run, menu_argv
from endstone_endbot.group_forms import GroupGuard, GroupListView, GroupView, ResultView
from endstone_endbot.group_results import PAGE_SIZE, MemberResult, ResultStore
from endstone_endbot.i18n import LocalizedError, localize_error, tr

GROUP_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,15}\Z")
GROUP_TARGET = re.compile(r"#([A-Za-z_][A-Za-z0-9_]{0,15})(?:\[([1-9][0-9]*)\])?\Z")
GROUP_INPUT_OPERATIONS = frozenset({"move", "look", "action", "flag", "stop", "hotbar", "drop", "swap", "hold"})
HISTORY_ERROR = "Result history must be a positive integer (1 = latest)"
PAGE_ERROR = "Page must be a positive integer"


class _GroupCanceled(ValueError):
    pass


class _MissingProfile(ValueError):
    """A member UUID with no runtime profile; the membership is kept."""

    code = "missing_profile"

    def __init__(self):
        super().__init__("missing Bot profile; membership retained")


def group_name(value: str) -> str:
    if not isinstance(value, str) or not GROUP_NAME.fullmatch(value):
        raise ValueError(
            "Group names must start with a letter/underscore and contain 1-16 letters, digits or underscores"
        )
    return value


def group_reference(value: str) -> str:
    """Parse a whole-group command reference; persistence keeps the bare name."""
    if not value.startswith("#") or not GROUP_NAME.fullmatch(value[1:]):
        raise LocalizedError("Group syntax: #group (for example #miners)")
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
            try:
                identity = str(UUID(row["identityId"]))
                name = group_name(row["name"])
                members = tuple(str(UUID(member)) for member in row["members"])
            except (KeyError, TypeError, AttributeError) as error:
                raise ValueError("Invalid group entry") from error
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
        raise LocalizedError("Group {name} does not exist", name=name)

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
            raise LocalizedError("Group {name} already exists", name=name)
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
                raise LocalizedError("{target}: member index is out of range", target=target)
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
    """Shared groups reuse existing Bot actions for each operation."""

    def __init__(self, control, world, groups: GroupStore | None, authorize):
        super().__init__(control, world)
        self.groups, self.authorize = groups, authorize
        self._groups_open = True
        self.results = ResultStore()

    def close(self):
        self.cancel_groups()
        super().close()

    def cancel_groups(self):
        self._groups_open = False

    def _store(self) -> GroupStore:
        if self.groups is None:
            raise LocalizedError(
                "Groups are unavailable because the groups file could not be loaded; see the server log"
            )
        return self.groups

    def _check(self, sender):
        if not self._groups_open or not self.authorize(sender):
            raise _GroupCanceled("Bot control permission was revoked; command canceled")

    def _form_group(self, sender, group):
        guard = getattr(sender, "group_guard", None)
        if guard is not None and (
            guard.identity != group.identity or guard.name.casefold() != group.name.casefold()
            or (guard.members is not None and guard.members != group.members)
        ):
            raise LocalizedError("Group changed while the menu was open; reopen the menu")
        if guard is not None and guard.result_id is not None:
            self.results.find(sender, group, guard.result_id)

    @staticmethod
    def _positive(tail, index, message):
        """``tail[index]`` as a positive integer; 1 when omitted."""
        if len(tail) <= index:
            return 1
        value = tail[index]
        if not value.isascii() or not value.isdecimal() or int(value) < 1:
            raise LocalizedError(message)
        return int(value)

    def _menu(self, sender, group=None, page=1):
        if getattr(sender, "player_id", None) is None:
            detail = tr(_language(sender), "menu is available only to players; use /bot help")
            return CommandResult(True, (reply(detail),))
        if group is None:
            return CommandResult(True, view=GroupListView(self._store().groups))
        self._form_group(sender, group)
        if page > max(1, (len(group.members) + PAGE_SIZE - 1) // PAGE_SIZE):
            raise LocalizedError("Group member page is out of range")
        profiles = self._profiles(sender)
        members = tuple({**profiles.get(identity, {"name": "?", "connectionState": "missing"}),
                         "identityId": identity, "index": index}
                        for index, identity in enumerate(group.members, 1))
        available = tuple(sorted((bot for identity, bot in profiles.items() if identity not in group.members),
                                 key=lambda bot: bot["name"].casefold()))
        return CommandResult(True, view=GroupView(group.identity, group.name, members, available, page))

    @staticmethod
    def _group_view_back(view, group, guard):
        """Keep menu origin separate from the UUID/membership checks on actions."""
        back_guard = GroupGuard(group.identity, group.name)
        manage = Run((("menu", f"#{group.name}"),), command="botgroup", guard=back_guard)
        management = guard.return_to_management
        if management:
            back = manage
        elif guard.bot_identity is not None:
            back = Run((menu_argv(f"#{group.name}"),), guard=back_guard)
        else:
            back = Run((menu_argv(),))
        label = "Back to group management" if management else (
            "Back to group operations" if guard.bot_identity is not None else "Back to Bot list")
        return replace(view, guard=guard, back=back, back_label=label,
                       management=manage if view.name.startswith("#") and not management else None)

    def _operation_menu(self, sender, group):
        if getattr(sender, "player_id", None) is None:
            return self._menu(sender)
        self._form_group(sender, group)
        profiles = self._profiles(sender)
        incoming = getattr(sender, "group_guard", None)
        guard = GroupGuard(group.identity, group.name, group.members, operation_menu=True,
                           return_to_management=bool(incoming and incoming.return_to_management))
        online = sum(profiles.get(identity, {}).get("connectionState") == "online" for identity in group.members)
        summary = tr(_language(sender), "Members {count}, online {online}", count=len(group.members), online=online)
        view = BotView(f"#{group.name}", summary, {})
        return CommandResult(True, view=self._group_view_back(view, group, guard))

    def _result(self, sender, group, history, *, menu=False):
        self._form_group(sender, group)
        guard = getattr(sender, "group_guard", None)
        result = (self.results.find(sender, group, guard.result_id)
                  if guard is not None and guard.result_id is not None else self.results.get(sender, group, history))
        records = self.results.history(sender, group)
        history = records.index(result) + 1
        lines = result.lines(history, len(records), _language(sender))
        if menu:
            if getattr(sender, "player_id", None) is None:
                raise ValueError("Use /botgroup result #group [history] from the console")
            return CommandResult(True, view=ResultView(result, history, records,
                                 return_to_operations=bool(guard and guard.operation_menu),
                                 return_to_management=bool(guard and guard.return_to_management)))
        return CommandResult(True, tuple(reply(line) for line in lines))

    def _profiles(self, sender, *, batch=False) -> dict[str, dict]:
        self._check(sender)
        try:
            capabilities = self.control.request("capabilities")
        except RuntimeControlError as error:
            if error.code != "unknown_method":
                raise
            raise LocalizedError("Groups require the matching updated Endbot runtime") from error
        if (capabilities.get("identityPinnedRequests") is not True
                or (batch and capabilities.get("groupInputBatch") is not True)):
            raise LocalizedError("Groups require the matching updated Endbot runtime")
        self._check(sender)
        profiles = {bot["identityId"]: bot for bot in self.control.request("list")}
        self._check(sender)
        return profiles

    @staticmethod
    def _error(error, target=None, lang="en"):
        return CommandResult(True, (reply(localize_error(error, lang), target, ERROR),), accepted=False)

    def execute_group(self, arguments, sender=None):
        """Arguments following /botgroup. Runs on the same authorized, ordered worker as /bot."""
        lang = _language(sender)
        try:
            self._check(sender)
            tokens = _tokens(arguments)
            operation = tokens[0].lower() if tokens else "menu"
            tail = tokens[1:]
            if operation == "help" and not tail:
                return CommandResult(True, tuple(reply(line) for line in (
                    "/botgroup list [#group] | create #group | delete #group",
                    "/botgroup add #group <bot> | remove #group <bot-or-#group[index]>",
                    "/botgroup result #group [history] | menu [#group [page|result [history]]]",
                    "/bot #group <operation> | /bot #group[1] <operation> (one-based)",
                )))
            if operation == "menu" and not tail:
                return self._menu(sender)
            if operation in {"result", "menu"} and tail:
                group = self._store().get(group_reference(tail[0]))
                if operation == "result" and len(tail) in {1, 2}:
                    return self._result(sender, group, self._positive(tail, 1, HISTORY_ERROR))
                if operation == "menu":
                    if len(tail) in {2, 3} and tail[1] == "result":
                        return self._result(sender, group, self._positive(tail, 2, HISTORY_ERROR), menu=True)
                    if len(tail) in {1, 2}:
                        return self._menu(sender, group, self._positive(tail, 1, PAGE_ERROR))
            if operation == "list" and not tail:
                names = ", ".join(f"#{group.name} ({len(group.members)})" for group in self._store().groups)
                return CommandResult(True, (reply(names or tr(lang, "no groups")),))
            if operation in {"create", "delete", "list"} and len(tail) == 1:
                name = group_reference(tail[0])
                if operation == "create":
                    group = self._store().create(name)
                    return CommandResult(True, (reply(tr(lang, "group created"), f"#{group.name}"),))
                if operation == "delete":
                    self._form_group(sender, self._store().get(name))
                    self._store().delete(name)
                    return CommandResult(True, (reply(tr(lang, "group deleted; Bots retained"), f"#{name}"),))
                group = self._store().get(name)
                self._form_group(sender, group)
                profiles = self._profiles(sender)
                rows = []
                for index, identity in enumerate(group.members, start=1):
                    bot = profiles.get(identity)
                    label = (f"{bot['name']} [{tr(lang, bot['connectionState'])}]" if bot
                             else tr(lang, "missing Bot profile"))
                    rows.append(reply(label, f"#{group.name}[{index}]"))
                return CommandResult(True, tuple(rows) or (reply(tr(lang, "no members"), f"#{group.name}"),))
            if operation in {"add", "remove"} and len(tail) == 2:
                name, target = group_reference(tail[0]), tail[1]
                group = self._store().get(name)
                self._form_group(sender, group)
                if operation == "remove" and target.startswith("#"):
                    selected_group, selected, indexed = self._store().select(target)
                    if not indexed or selected_group.identity != group.identity:
                        raise ValueError("Remove requires a Bot name or one indexed member of this group")
                    identity = selected[0][1]
                else:
                    profiles = self._profiles(sender)
                    bot = next((bot for bot in profiles.values() if bot["name"].casefold() == target.casefold()), None)
                    if bot is None:
                        raise LocalizedError("Bot {name} does not exist; add an existing profile", name=target)
                    identity = bot["identityId"]
                    guard = getattr(sender, "group_guard", None)
                    if operation == "add" and guard is not None and guard.candidates:
                        candidates = {name.casefold(): identity for name, identity in guard.candidates}
                        if candidates.get(target.casefold()) != identity:
                            raise LocalizedError("Bot changed while the menu was open; reopen the menu")
                self._check(sender)
                getattr(self._store(), operation)(name, identity)
                template = "add: {target}" if operation == "add" else "remove: {target}"
                return CommandResult(True, (reply(tr(lang, template, target=target), f"#{group.name}"),))
            raise ValueError("Invalid group command; use /botgroup help")
        except (RuntimeError, ValueError, OSError) as error:
            return self._error(error, lang=lang)

    @staticmethod
    def _row(index, identity, name, *, result=None, error=None, outcome=None, lang="en"):
        reason = "other"
        if error is not None:
            reason = getattr(error, "code", "other")
            if isinstance(error, _GroupCanceled):
                reason, outcome = "canceled", "skipped"
            detail = localize_error(error, lang)
        else:
            detail = " ".join(result.messages or ())
        detail = strip_colors(detail).replace("\n", " ").replace("\r", " ")
        detail = detail.removeprefix("Endbot: ").removeprefix(f"{name}: ")
        return MemberResult(index, identity, name, outcome or ("failed" if error else "accepted"), reason, detail,
                            result.data if result is not None else None)

    def _execute_inputs(self, command, sender, members):
        profiles, prepared, requests = {}, [], []
        lang = _language(sender)
        try:
            profiles = self._profiles(sender, batch=True)
            for index, identity in members:
                self._check(sender)
                bot = profiles.get(identity)
                if bot is None:
                    error = _MissingProfile()
                    prepared.append(self._row(index, identity, "?", error=error, lang=lang))
                    continue
                member = replace(command, name=bot["name"])
                try:
                    if command.operation == "look" and "targetCoordinates" in command.parameters:
                        yaw, pitch = self.world.look_at(identity, command.parameters["targetCoordinates"])
                        member = replace(member, parameters={"yaw": yaw, "pitch": pitch})
                    requests.append({"method": member.operation, "params": {
                        **member.parameters, "name": member.name, "expectedIdentityId": identity,
                    }})
                    prepared.append((index, identity, member))
                except (RuntimeError, ValueError) as error:
                    prepared.append(self._row(index, identity, member.name, error=error, lang=lang))
            self._check(sender)
        except (RuntimeError, ValueError) as error:
            return [self._row(index, identity, profiles.get(identity, {}).get("name", "?"),
                              error=error, outcome="skipped", lang=lang) for index, identity in members]
        try:
            responses = self.control.request("groupInputBatch", requests=requests)["results"] if requests else []
            if len(responses) != len(requests):
                raise RuntimeError("Invalid group input batch response")
        except (RuntimeError, ValueError, KeyError, TypeError) as error:
            # A lost response cannot establish whether already-sent inputs ran.
            return [entry if isinstance(entry, MemberResult) else self._row(
                entry[0], entry[1], entry[2].name, error=error, outcome="unknown", lang=lang) for entry in prepared]
        responses, rows = iter(responses), []
        for entry in prepared:
            if isinstance(entry, MemberResult):
                rows.append(entry)
                continue
            index, identity, member = entry
            try:
                result = self._execute(member, sender, lang,
                                       control=_BatchReplyControl(member.operation, next(responses)))
                rows.append(self._row(index, identity, member.name, result=result))
            except (RuntimeError, ValueError) as error:
                rows.append(self._row(index, identity, member.name, error=error, lang=lang))
            except Exception as error:  # noqa: BLE001 - the batch already ran; keep every member's row
                rows.append(self._row(index, identity, member.name, error=error, outcome="unknown", lang=lang))
        return rows

    def _finish(self, sender, group, target, operation, rows, *, indexed=False, query=False):
        if query:
            # A query's payload is its result, not an acknowledgement. Do not
            # replace operation history or apply its saved-detail truncation.
            return CommandResult(True, tuple(
                reply(row.detail, f"#{group.name}[{row.index}] {row.name}",
                        ERROR if row.outcome != "accepted" else "") for row in rows),
                                 accepted=all(row.outcome == "accepted" for row in rows),
                                 data={"results": [{"index": row.index, "identityId": row.identity,
                                                    "name": row.name, "ok": row.outcome == "accepted",
                                                    "reason": row.reason if row.outcome != "accepted" else None,
                                                    "data": row.data} for row in rows]})
        rows = [replace(row, detail=row.detail[:512]) for row in rows]
        record = self.results.save(sender, group, target, operation, rows)
        if indexed:
            row = rows[0]
            messages = (reply(row.detail, f"{target} {row.name}", ERROR if row.outcome != "accepted" else ""),)
        else:
            messages = tuple(reply(line, target) for line in record.summary(_language(sender)))
        actor = getattr(sender, "name", None) or "console"
        actor = strip_colors(str(actor)).replace("\n", " ").replace("\r", " ")[:32]
        if indexed:
            actor += f" {rows[0].name}"
        logs = tuple(reply(f"{actor}: {line}", target) for line in record.summary("en"))
        return CommandResult(True, messages, log_level="info", log_messages=logs,
                             accepted=all(row.outcome == "accepted" for row in rows),
                             data={"accepted": sum(row.outcome == "accepted" for row in rows),
                                   "failed": sum(row.outcome == "failed" for row in rows),
                                   "notRun": sum(row.outcome == "skipped" for row in rows),
                                   "unknown": sum(row.outcome == "unknown" for row in rows)})

    def execute(self, arguments, sender=None):
        try:
            tokens = _tokens(arguments)
            if not tokens or not tokens[0].startswith("#"):
                guard = getattr(sender, "group_guard", None)
                command = parse_command(arguments)
                if guard is not None and command.name:
                    self._check(sender)
                    self._form_group(sender, self._store().get(guard.name))
                    if guard.bot_identity is not None:
                        if command.name.casefold() != guard.bot_name.casefold():
                            raise ValueError("Menu Bot changed; reopen the menu")
                        if command.operation == "inventory":
                            command = replace(command, parameters={"identityId": guard.bot_identity})
                        result = self._execute(command, sender, _language(sender), control=_MemberControl(
                            self.control, guard.bot_identity, lambda: self._check(sender)))
                        if result.view is not None:
                            result = replace(result, view=self._group_view_back(
                                result.view, self._store().get(guard.name), guard))
                        return result
                result = super().execute(arguments, sender)
                if isinstance(result.view, BotListView):
                    self._check(sender)
                    # A store that failed to load disables groups, never the Bot list.
                    groups = self.groups.groups if self.groups is not None else ()
                    targets = tuple(GroupTarget(group.name, len(group.members), GroupGuard(group.identity, group.name))
                                    for group in groups)
                    result = replace(result, view=replace(result.view, groups=targets))
                return result
            command = parse_command(arguments)
            group, members, indexed = self._store().select(tokens[0])
            self._check(sender)
            self._form_group(sender, group)
            if not indexed and command.operation == "menu":
                return self._operation_menu(sender, group)
            if not indexed and command.operation in {"rename", "forget"}:
                raise LocalizedError("{operation} requires one Bot; use #group[1] or a Bot name",
                                     operation=command.operation)
            if not members:
                raise ValueError("Group has no members")
            query = command.operation in {"status", "inventory"} or (
                command.operation in {"hotbar", "radius"} and not command.parameters)
            if not indexed and command.operation in GROUP_INPUT_OPERATIONS:
                rows = self._execute_inputs(command, sender, members)
                return self._finish(sender, group, tokens[0], " ".join(tokens[1:])[:128], rows, query=query)
            lang = _language(sender)
            target, operation = tokens[0], " ".join(tokens[1:])[:128]
            try:
                profiles = self._profiles(sender)
            except (RuntimeError, ValueError) as error:
                rows = [self._row(index, identity, "?", error=error, outcome="skipped", lang=lang)
                        for index, identity in members]
                return self._finish(sender, group, target, operation, rows, indexed=indexed, query=query)
            rows, canceled = [], None
            for index, identity in members:
                name = "?"
                if canceled is not None:
                    rows.append(self._row(index, identity, name, error=canceled, lang=lang))
                    continue
                try:
                    bot = profiles.get(identity)
                    if bot is None:
                        raise _MissingProfile()
                    name = bot["name"]
                    member = replace(command, name=name)
                    if command.operation == "inventory":
                        self._check(sender)
                        # Preserve the group's UUID pin even though this query
                        # reads BDS directly instead of using runtime IPC.
                        member = replace(member, parameters={"identityId": identity})
                    result = self._execute(
                        member, sender, lang,
                        control=_MemberControl(self.control, identity, lambda: self._check(sender)),
                    )
                    if command.operation == "menu":
                        if result.view is not None:
                            incoming = getattr(sender, "group_guard", None)
                            guard = GroupGuard(group.identity, group.name, group.members, bot_identity=identity,
                                               bot_name=name, return_to_management=bool(
                                                   incoming and incoming.return_to_management))
                            result = replace(result, view=self._group_view_back(result.view, group, guard))
                        return result
                    rows.append(self._row(index, identity, name, result=result))
                except (RuntimeError, ValueError) as error:
                    uncertain = isinstance(error, RuntimeControlError) and error.code == "unavailable"
                    outcome = "unknown" if uncertain else None
                    rows.append(self._row(index, identity, name, error=error, outcome=outcome, lang=lang))
                    try:
                        self._check(sender)
                    except _GroupCanceled as error:
                        canceled = error
                except Exception as error:  # noqa: BLE001 - keep earlier members' results and audit entry
                    # The request may already have run before the failure.
                    rows.append(self._row(index, identity, name, error=error, outcome="unknown", lang=lang))
            return self._finish(sender, group, target, operation, rows, indexed=indexed, query=query)
        except (RuntimeError, ValueError) as error:
            return self._error(error, arguments[0] if arguments else None, _language(sender))
