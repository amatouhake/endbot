"""What a signed-in browser can ask over its WebSocket (docs/WEB.md P11; docs/WEB_PROTOCOL.md).

Every message builds the ``web`` principal again, so a revoke or a role change holds
at once. Visibility is the in-game rule: only what this principal may see. A log is
this player's own run and job events, kept in memory only, and rendered in the
language the page asks for.
"""

from __future__ import annotations

import json
import re
import shlex
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, replace

from endbot_core.commands import error_code, strip_colors
from endbot_core.control import RuntimeControlError, bot_profiles
from endbot_core.events import Event, render
from endbot_core.i18n import localize_error, tr
from endbot_core.operations import OPERATIONS, RESERVED_BOT_NAMES, Operation
from endbot_core.principal import Principal
from endbot_core.script_forms import TEMPLATES, ScriptGuard

LOG_LINES = 200
SCRIPT_NAME = re.compile(r"[A-Za-z0-9_-]{1,64}")
# Not for the web: an in-game screen, chat help, and what stays with the console.
WEB_REFUSED_OPERATIONS = frozenset({"menu", "help", "owner"})
_LINE = re.compile(r"(?:^|\s)script:(\d+):")
LANGUAGES = {"en", "ja"}


def _plain(line: str) -> str:
    """A reply as the page shows it: no chat colours, and no "Endbot:" in Endbot's own page."""
    return strip_colors(line).removeprefix("Endbot: ")


@dataclass
class _Subscriber:
    xuid: str
    topics: set[str]
    push: Callable[[object], None]
    language: str
    token: str


class WebApi:
    def __init__(self, auth, *, commands, scripts, control, bds_up: Callable[[], bool],
                 display_name: Callable[[], str], clock: Callable[[], float] = time.time, runner=None) -> None:
        self._auth = auth
        self._runner = runner
        self._commands = commands
        self._scripts = scripts
        self._control = control
        self._bds_up = bds_up
        self._display_name = display_name
        self._clock = clock
        self._lock = threading.Lock()
        self._logs: dict[str, deque] = {}
        # conn -> what a push needs: dropped when the connection closes, or when its session has ended.
        self._subscribers: dict[str, _Subscriber] = {}

    # -- requests --------------------------------------------------------------

    def handle(self, token: str, conn: str, message: object, push: Callable[[object], None]) -> dict | None:
        language = message.get("lang") if isinstance(message, dict) else None
        language = language if language in LANGUAGES else "en"
        principal = self._auth.principal(token, language)
        if principal is None:
            return {"type": "result", "code": "signed_out"}
        if not isinstance(message, dict):
            return {"type": "result", "code": "invalid_request"}
        kind = message.get("type")
        if kind == "query":
            return self._query(principal, message.get("what"))
        if kind == "subscribe" and message.get("topic") in {"status", "log"}:
            return self._subscribe(principal, token, conn, message["topic"], push)
        if kind == "unsubscribe" and message.get("topic") in {"status", "log"}:
            with self._lock:
                entry = self._subscribers.get(conn)
                if entry is not None:
                    entry.topics.discard(message["topic"])
            return {"type": "result", "code": "ok"}
        if kind == "op":
            return self._operation(principal, message.get("operation"))
        if isinstance(kind, str) and kind.startswith("script."):
            return self._script(principal, kind[len("script."):], message)
        return {"type": "result", "code": "unsupported"}

    # -- operations (docs/WEB.md P11) -------------------------------------------

    def _operation(self, principal: Principal, operation: object) -> dict:
        """One registry operation, through the same command path as chat: same checks, same wording."""
        if not isinstance(operation, dict) or not isinstance(operation.get("op"), str):
            return {"type": "result", "code": "invalid_argument"}
        name, target, params = operation["op"], operation.get("target"), operation.get("params", {})
        if name in WEB_REFUSED_OPERATIONS or name not in OPERATIONS or not isinstance(params, dict):
            return {"type": "result", "code": "invalid_argument"}
        # A reserved word as the target would be read as that root command instead ("help status").
        if isinstance(target, str) and target in RESERVED_BOT_NAMES:
            return {"type": "result", "code": "invalid_argument"}
        try:
            argv = Operation(name, target if isinstance(target, str) else None, params).argv()
        except (ValueError, TypeError) as error:
            return self._refused(error, principal, "invalid_argument")
        return self._command(principal, argv, "bot")

    def _command(self, principal: Principal, argv: list[str], root: str) -> dict:
        try:
            result = self._runner.call(argv, principal, command=root)
        except TimeoutError:
            return {"type": "result", "code": "timeout"}
        answer = {"type": "result", "code": result.code, "messages": [_plain(line) for line in result.messages]}
        try:
            json.dumps(result.data)
        except (TypeError, ValueError):
            return answer  # A menu's data is for a form; the messages say what happened.
        if result.data is not None:
            answer["data"] = result.data
        return answer

    @staticmethod
    def _refused(error: BaseException, principal: Principal, code: str | None = None) -> dict:
        message = localize_error(error, principal.language)
        answer = {"type": "result", "code": code or error_code(error), "messages": [message]}
        # A declaration or compile error names its line, for the editor's margin.
        line = _LINE.search(message)
        if line:
            answer["line"] = int(line[1])
        return answer

    # -- scripts ---------------------------------------------------------------------

    def _script(self, principal: Principal, action: str, message: dict) -> dict:
        name = message.get("name")
        if action != "list" and action != "templates" and action != "stop" and (
                not isinstance(name, str) or not SCRIPT_NAME.fullmatch(name)):
            return {"type": "result", "code": "invalid_argument"}
        try:
            return self._script_action(principal, action, name, message)
        except (ValueError, RuntimeError, OSError) as error:
            return self._refused(error, principal)

    def _script_action(self, principal: Principal, action: str, name: str | None, message: dict) -> dict:
        scripts = self._scripts
        if action == "list":
            return {"type": "result", "code": "ok", "data": {"scripts": scripts.listing(principal),
                                                             "canAuthor": principal.can_author}}
        if action == "templates":
            return {"type": "result", "code": "ok", "data": dict(TEMPLATES)}
        if action == "get":
            return {"type": "result", "code": "ok", "data": scripts.detail(name, principal)}
        if action == "declaration":
            return {"type": "result", "code": "ok", "data": scripts.declaration(name, principal)}
        if action == "compile":
            return {"type": "result", "code": "ok", "data": scripts.compile(name, message.get("source"), principal)}
        if action == "save":
            return self._script_save(principal, name, message)
        if action == "run":
            return self._script_run(principal, name, message)
        if action == "stop":
            run = message.get("run")
            if not isinstance(run, str) or not SCRIPT_NAME.fullmatch(run):
                return {"type": "result", "code": "invalid_argument"}
            return self._command(principal, ["stop", shlex.quote(run)], "botscript")
        words = self._script_words(action, name, message)
        if words is None:
            return {"type": "result", "code": "unsupported"}
        revision = message.get("revision")
        guard = ScriptGuard(revision if isinstance(revision, str) else None, name)
        return self._command(replace(principal, group_guard=guard), words, "botscript")

    @staticmethod
    def _script_words(action: str, name: str, message: dict) -> list[str] | None:
        """A guard without an edit is tokenized again, so everything here is one checked word."""
        if action == "delete":
            return ["delete", name]
        if action in {"copy", "rename"}:
            new = message.get("new")
            return [action, name, new] if isinstance(new, str) and SCRIPT_NAME.fullmatch(new) else None
        if action == "visibility":
            value = message.get("visibility")
            return ["visibility", name, value] if isinstance(value, str) and value in {"shared", "private"} else None
        if action == "restore":
            index = message.get("index")
            return ["restore", name, str(index)] if type(index) is int and 1 <= index <= 999 else None
        return None

    def _script_save(self, principal: Principal, name: str, message: dict) -> dict:
        source, revision = message.get("source"), message.get("revision")
        if not isinstance(source, str) or (revision is not None and not isinstance(revision, str)):
            return {"type": "result", "code": "invalid_argument"}
        if revision is not None:
            # Someone saved first: show them what is there now, rather than only refuse. The store checks this
            # again under its own lock, so a save racing past here is still refused, just without the source.
            current = self._scripts.detail(name, principal)
            if current["revision"] != revision:
                return {"type": "result", "code": "conflict", "current": current,
                        "messages": [tr(principal.language, "Script changed; reopen the menu")]}
        # The in-game whole-script save: ownership, role, declaration and revision are all checked there.
        guard = ScriptGuard(revision, name if revision is not None else None, edit=True)
        answer = self._command(replace(principal, group_guard=guard), ["save", name, source], "botscript")
        line = next((_LINE.search(text) for text in answer.get("messages", ()) if _LINE.search(text)), None)
        if line:
            answer["line"] = int(line[1])
        answer.pop("data", None)
        if answer["code"] == "ok":
            answer["data"] = self._scripts.detail(name, principal)
        return answer

    def _script_run(self, principal: Principal, name: str, message: dict) -> dict:
        values, requires, revision = message.get("values", {}), message.get("requires"), message.get("revision")
        if (not isinstance(values, dict) or not isinstance(revision, str) or not isinstance(requires, list)
                or not all(isinstance(entry, str) for entry in requires)
                or not all(isinstance(key, str) and type(value) in (str, int, float, bool)
                           for key, value in values.items())):
            return {"type": "result", "code": "invalid_argument"}
        targets = self._run_targets(message.get("targets", {}), values)
        if targets is None:
            return {"type": "result", "code": "invalid_argument"}
        # The run is held to the declaration the form showed (P11), the revision it came from and the targets it chose.
        guard = ScriptGuard(revision, name, values=values, targets=targets, requires=tuple(requires))
        return self._command(replace(principal, group_guard=guard), ["run", name], "botscript")

    @staticmethod
    def _run_targets(targets: object, values: dict) -> dict | None:
        """The targets a form chose, as the in-game form pins them; None when they do not make sense."""
        if not isinstance(targets, dict):
            return None
        pinned = {}
        for parameter, target in targets.items():
            if not isinstance(target, dict) or values.get(parameter) != target.get("value"):
                return None
            value, identity, members = target.get("value"), target.get("identity"), target.get("members")
            if not isinstance(value, str) or not isinstance(identity, str):
                return None
            if members is None:
                pinned[parameter] = ("bot", value, identity)
            elif isinstance(members, list) and all(isinstance(member, str) for member in members):
                pinned[parameter] = ("group", value, identity, tuple(members))
            else:
                return None
        return pinned

    def _query(self, principal, what: object) -> dict:
        readers = {"status": self._status, "bots": self._bots, "groups": self._groups, "runs": self._runs,
                   "log": self._log}
        reader = readers.get(what) if isinstance(what, str) else None
        if reader is None:
            return {"type": "result", "code": "unsupported"}
        return {"type": "result", "code": "ok", "data": reader(principal)}

    def _status(self, principal) -> dict:
        return {
            "displayName": self._display_name(),
            "bds": "up" if self._bds_up() else "down",
            "gamertag": principal.name,
            "role": principal.role or "scripter",
            "bots": self._bots(principal),
            "groups": self._groups(principal),
            "runs": self._runs(principal),
        }

    def _bots(self, principal) -> list[dict] | None:
        """The Bots this principal may see; None while the runtime is down (it stops with BDS)."""
        try:
            profiles = bot_profiles(self._control)
        except (RuntimeControlError, OSError):
            return None
        ownership = self._commands.ownership
        return [{"name": bot["name"], "state": bot["connectionState"]}
                for bot in profiles
                if ownership is None or ownership.visible_to("bots", bot["identityId"], principal)]

    def _groups(self, principal) -> list[dict]:
        return [{"name": group.name, "members": len(group.members)}
                for group in self._commands.visible_groups(principal)]

    def _runs(self, principal) -> list[dict]:
        return [{"id": run.identity, "name": run.name} for run in self._scripts.active(principal)]

    def _log(self, principal) -> list[dict]:
        with self._lock:
            events = list(self._logs.get(principal.xuid, ()))
        return [self._entry(moment, event, principal.language) for moment, event in events]

    @staticmethod
    def _entry(moment: float, event: Event, language: str) -> dict:
        # A line chat holds back (its rate limit, `chat-logs = false`) still belongs in this player's own log.
        lines = render(event, "chat", language) or render(event, "log")
        return {"time": moment, "kind": event.kind, "level": event.level,
                "lines": [_plain(line) for line in lines]}

    def _subscribe(self, principal, token: str, conn: str, topic: str, push: Callable[[object], None]) -> dict:
        with self._lock:
            entry = self._subscribers.get(conn)
            topics = entry.topics if entry is not None else set()
            topics.add(topic)
            self._subscribers[conn] = _Subscriber(principal.xuid, topics, push, principal.language, token)
        return {"type": "result", "code": "ok"}

    def _live(self, wanted: Callable[[_Subscriber], bool]) -> list[_Subscriber]:
        """Who to push to. A page may send nothing for days, so its session is checked here too (P5)."""
        with self._lock:
            targets = {conn: entry for conn, entry in self._subscribers.items() if wanted(entry)}
        live = []
        for conn, entry in targets.items():
            if self._auth.principal(entry.token) is not None:
                live.append(entry)
            else:
                self.connection_closed(conn)
        return live

    def connection_closed(self, conn: str) -> None:
        with self._lock:
            self._subscribers.pop(conn, None)

    # -- what happened -----------------------------------------------------------

    def record(self, event: Event) -> None:
        """An Event for a player: kept for their log and pushed to their open pages. The console has no page."""
        xuid = getattr(event.to, "xuid", None)
        if not xuid:
            return
        moment = self._clock()
        with self._lock:
            self._logs.setdefault(xuid, deque(maxlen=LOG_LINES)).append((moment, event))
        for entry in self._live(lambda entry: entry.xuid == xuid and "log" in entry.topics):
            entry.push({"type": "event", "topic": "log", "data": self._entry(moment, event, entry.language)})

    def notify_account(self, xuid: str, kind: str) -> None:
        """A security notice for this account's open pages, such as a passkey just added (P4)."""
        for entry in self._live(lambda entry: entry.xuid == xuid):
            entry.push({"type": "event", "topic": "account", "data": {"kind": kind}})

    def status_changed(self) -> None:
        for entry in self._live(lambda entry: "status" in entry.topics):
            entry.push({"type": "event", "topic": "status", "data": {"bds": "up" if self._bds_up() else "down"}})
