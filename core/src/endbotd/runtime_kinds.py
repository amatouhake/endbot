"""Runtime- and endbotd-sourced event kinds (BOT-022 step 6b, EVENTS.md E4, E9).

The runtime has no push channel, so endbotd adapts its Bot profiles by polling
``bot_profiles`` once a second while a watch or trigger wants ``bot.online`` or
``bot.offline`` (E4 allows this adapter). The first poll after interest appears
is the baseline: no occurrence. After that, a Bot that becomes ``online`` raises
``bot.online``; a Bot that was ``online`` and is no longer (including a removed
profile) raises ``bot.offline``, with the new state (or ``removed``) as reason.

``job.finished`` is raised from the ``job.ended`` Event the command service
publishes. ``server.started`` is raised by ``OccurrenceHub.link_changed`` from
the plugin's boot id and needs no poll. None of these kinds reaches the plugin:
all four are ``endbotd``-sourced.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

from endbot_core.control import bot_profiles
from endbot_core.events import Event, reason_text

POLL_SECONDS = 1.0
# How often an idle monitor looks for a new subscription, so interest starts the poll promptly.
IDLE_SECONDS = 0.25
RUNTIME_KINDS = ("bot.online", "bot.offline")
JOB_EVENT = "job.ended"
# job.ended names the operation; both mining operations are one mining job to a script.
JOB_KINDS = {"destroy": "mining", "destroy continuous": "mining"}


class RuntimeKinds:
    """Polls Bot profiles while wanted and turns command Events into occurrences."""

    def __init__(self, hub, control, *, poll_seconds: float = POLL_SECONDS,
                 idle_seconds: float = IDLE_SECONDS,
                 log: Callable[[str, str], None] | None = None) -> None:
        self._hub = hub
        self._control = control
        self._poll_seconds = poll_seconds
        self._idle_seconds = idle_seconds
        self._log = log or (lambda level, message: None)
        self._lock = threading.Lock()
        # identityId -> (name, connectionState) from the last successful poll; None until a baseline exists.
        self._profiles: dict[str, tuple[str, str]] | None = None
        self._closed = threading.Event()
        self._thread: threading.Thread | None = None
        self._failure: str | None = None

    def start(self) -> RuntimeKinds:
        self._thread = threading.Thread(target=self._loop, name="endbotd-runtime-kinds", daemon=True)
        self._thread.start()
        return self

    def close(self) -> None:
        self._closed.set()
        if self._thread is not None:
            self._thread.join(5)

    def _loop(self) -> None:
        while not self._closed.is_set():
            if not self._hub.has_interest(*RUNTIME_KINDS):
                # No one is watching: the next poll after interest returns is a fresh baseline.
                with self._lock:
                    self._profiles = None
                if self._closed.wait(self._idle_seconds):
                    return
                continue
            self.poll()
            if self._closed.wait(self._poll_seconds):
                return

    def poll(self) -> bool:
        """One profile poll; False when no watch or trigger wants the runtime kinds."""
        if not self._hub.has_interest(*RUNTIME_KINDS):
            return False
        try:
            current = self._snapshot(bot_profiles(self._control))
        except Exception as error:  # noqa: BLE001 - a runtime hiccup must not stop the monitor
            failure = f"{type(error).__name__}: {error}"
            if failure != self._failure:
                self._log("warning", f"Bot presence poll failed: {failure}")
                self._failure = failure
            with self._lock:
                # The next successful poll is a baseline, not a burst of offline reports for a runtime restart.
                self._profiles = None
            return True
        self._failure = None
        with self._lock:
            previous, self._profiles = self._profiles, current
        if previous is not None:
            self._transitions(previous, current)
        return True

    @staticmethod
    def _snapshot(profiles) -> dict[str, tuple[str, str]]:
        current = {}
        for profile in profiles if isinstance(profiles, list) else ():
            if not isinstance(profile, dict):
                continue
            name, state = profile.get("name"), profile.get("connectionState")
            if not isinstance(name, str) or not name or not isinstance(state, str):
                continue
            key = profile.get("identityId")
            key = key if isinstance(key, str) and key else name
            current[key] = (name, state)
        return current

    def _transitions(self, previous: dict[str, tuple[str, str]], current: dict[str, tuple[str, str]]) -> None:
        for key, (name, state) in current.items():
            before = previous.get(key)
            if state == "online" and (before is None or before[1] != "online"):
                self._hub.raise_occurrence("endbotd", {"kind": "bot.online", "bot": name})
        for key, (name, state) in previous.items():
            if state != "online":
                continue
            now = current.get(key)
            if now is None:
                self._hub.raise_occurrence("endbotd", {"kind": "bot.offline", "bot": name, "reason": "removed"})
            elif now[1] != "online":
                self._hub.raise_occurrence("endbotd", {"kind": "bot.offline", "bot": now[0], "reason": now[1]})

    def on_event(self, event: Event) -> None:
        """The service's Event hook: one job.ended Event becomes one job.finished occurrence."""
        if event.kind != JOB_EVENT:
            return
        data = event.data if isinstance(event.data, dict) else {}
        bot, job = data.get("bot"), data.get("job")
        if not isinstance(bot, str) or not bot or not isinstance(job, str) or not job:
            return
        reason = data.get("reason")
        self._hub.raise_occurrence("endbotd", {
            "kind": "job.finished", "bot": bot, "job": JOB_KINDS.get(job, job),
            "reason": "ended" if reason is None else reason_text(reason),
        })
