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

from endbot_core.commands import strip_colors
from endbot_core.control import RuntimeControlError, bot_profiles
from endbot_core.events import Event, render

LOG_LINES = 200
LANGUAGES = {"en", "ja"}


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
        # conn -> (xuid, topics, push): dropped when the connection closes.
        self._subscribers: dict[str, tuple[str, set[str], Callable[[object], None], str]] = {}

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
            return self._subscribe(principal, conn, message["topic"], push)
        if kind == "unsubscribe" and message.get("topic") in {"status", "log"}:
            with self._lock:
                entry = self._subscribers.get(conn)
                if entry is not None:
                    entry[1].discard(message["topic"])
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

    def _subscribe(self, principal, conn: str, topic: str, push: Callable[[object], None]) -> dict:
        with self._lock:
            entry = self._subscribers.get(conn)
            topics = entry[1] if entry is not None else set()
            topics.add(topic)
            self._subscribers[conn] = (principal.xuid, topics, push, principal.language)
        return {"type": "result", "code": "ok"}

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
            targets = [entry for entry in self._subscribers.values() if entry[0] == xuid and "log" in entry[1]]
        for _xuid, _topics, push, language in targets:
            push({"type": "event", "topic": "log", "data": self._entry(moment, event, language)})

    def notify_account(self, xuid: str, kind: str) -> None:
        """A security notice for this account's open pages, such as a passkey just added (P4)."""
        with self._lock:
            targets = [entry for entry in self._subscribers.values() if entry[0] == xuid]
        for _xuid, _topics, push, _language in targets:
            push({"type": "event", "topic": "account", "data": {"kind": kind}})

    def status_changed(self) -> None:
        with self._lock:
            targets = [entry for entry in self._subscribers.values() if "status" in entry[1]]
        for _xuid, _topics, push, _language in targets:
            push({"type": "event", "topic": "status", "data": {"bds": "up" if self._bds_up() else "down"}})
