"""What a signed-in browser can ask over its WebSocket (docs/WEB.md P11; docs/WEB_PROTOCOL.md).

Every message builds the ``web`` principal again, so a revoke or a role change holds
at once. Visibility is the in-game rule: only what this principal may see. A log is
this player's own run and job events, kept in memory only, and rendered in the
language the page asks for.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from endbot_core.commands import strip_colors
from endbot_core.control import RuntimeControlError, bot_profiles
from endbot_core.events import Event, render

LOG_LINES = 200
LANGUAGES = {"en", "ja"}


@dataclass
class _Subscriber:
    xuid: str
    topics: set[str]
    push: Callable[[object], None]
    language: str
    token: str


class WebApi:
    def __init__(self, auth, *, commands, scripts, control, bds_up: Callable[[], bool],
                 display_name: Callable[[], str], clock: Callable[[], float] = time.time) -> None:
        self._auth = auth
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
        return {"type": "result", "code": "unsupported"}

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
                "lines": [strip_colors(line) for line in lines]}

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
