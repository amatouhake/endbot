"""The ordered command worker: commands, script operations, cleanup and mining polls."""

from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError

from endbot_core.commands import ERROR, WARNING, CommandResult, reply, strip_colors
from endbot_core.events import Event, EventLogSink, render
from endbot_core.i18n import tr
from endbot_core.menu_views import menu_view
from endbot_core.principal import Principal


def _send(sender, source: Principal, message: str) -> None:
    """Deliver a reply; only players get color codes.

    Endstone renders codes on an interactive console, but the console stream
    is also the server log that operators and the smoke tests read as text.
    """
    if sender is not None:
        sender.send_message(message if source.player_id is not None else strip_colors(message))


class AsyncCommandRunner:
    """Run ordered command service calls away from the BDS command thread."""

    def __init__(
        self,
        service,
        bridge,
        resolve_sender: Callable[[Principal], object],
        authorize: Callable[[Principal], bool] | None = None,
        log_sink: EventLogSink | None = None,
        show_view: Callable[[object, object], None] | None = None,
        scripts=None,
    ) -> None:
        self._service = service
        self._bridge = bridge
        self._resolve_sender = resolve_sender
        # One two-layer check for commands, runs and jobs (see Principal).
        self._authorize = authorize
        self._log_sink = log_sink
        # Presents a result's form view to its player on the server thread.
        self._show_view = show_view
        self._scripts = scripts
        # One worker preserves the command ordering previously provided by the
        # BDS command thread while moving all blocking socket waits off it.
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="endbot-control")
        # Mining lease renewal must not wait behind a slow command such as a
        # spawn; loop IDs make a renewal racing a stop/replacement harmless.
        self._poll_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="endbot-mining")
        self._lock = threading.Lock()
        self._open = True
        self._poll_future = None
        if scripts is not None:
            scripts.ordered = lambda callback: self._executor.submit(callback).result()
            scripts.notify = self._deliver
            scripts.publish = self.publish

    def poll(self) -> None:
        """Queue at most one poll, independent of the command worker."""
        with self._lock:
            if self._open and (self._poll_future is None or self._poll_future.done()):
                self._poll_future = self._poll_executor.submit(self._poll)

    def _poll(self) -> None:
        with self._lock:
            if not self._open:
                return

        def authorize(source):
            return self._authorize is None or self._bridge.call(lambda: self._authorize(source))

        for event in self._service.poll_mining(authorize) or ():
            self.publish(event)

    def publish(self, event: Event) -> None:
        """Deliver an Event to its sinks: the console log in English, then its principal's chat."""

        def deliver() -> None:
            source = event.to
            if self._log_sink is not None:
                for line in render(event, "log"):
                    self._log_sink.write(event.level, strip_colors(line))
                if source.player_id is None:
                    return  # The console already shows the log entry.
            # A controller who has logged off still gets the console log entry.
            sender = self._resolve_sender(source)
            for line in render(event, "chat"):
                _send(sender, source, line)

        self._bridge.post(deliver)

    def submit(self, arguments: list[str], principal: Principal, *, command: str = "bot") -> bool:
        """Queue one command; the adapter captured ``principal`` on the server thread."""
        if command not in {"bot", "botgroup", "botscript"}:
            raise ValueError("Unknown Endbot command root")
        with self._lock:
            if not self._open:
                return False
            try:
                self._executor.submit(self._execute, list(arguments), principal, command)
            except RuntimeError:
                return False
        return True

    def close(self, timeout: float = 0.0) -> None:
        """Stop taking work and queue cleanup; wait up to ``timeout`` seconds for it to finish."""
        with self._lock:
            if not self._open:
                return
            self._open = False
            if self._scripts is not None:
                self._scripts.cancel()
                self._executor.submit(self._scripts.close)
            if hasattr(self._service, "cancel_groups"):
                self._service.cancel_groups()
            if hasattr(self._service, "close"):
                self._executor.submit(self._service.close)
            drained = self._executor.submit(lambda: None)
        # Queued commands check _open; cleanup follows any in-flight request.
        self._executor.shutdown(wait=False)
        self._poll_executor.shutdown(wait=False)
        if timeout > 0:
            try:
                drained.result(timeout)
            except FutureTimeoutError:
                pass

    def _execute(self, arguments: list[str], source: Principal, command: str = "bot") -> None:
        with self._lock:
            if not self._open:
                return
        try:
            if self._authorize is not None and not self._bridge.call(lambda: self._authorize(source)):
                revoked = tr(source.language, "Bot control permission was revoked; command canceled")
                result = CommandResult(True, (reply(revoked, color=WARNING),), accepted=False, code="unauthorized")
            else:
                execute = (self._scripts.execute if command == "botscript" and self._scripts is not None else
                           self._service.execute_group if command == "botgroup" else self._service.execute)
                result = execute(arguments, source)
        except Exception:  # noqa: BLE001 - never strand command delivery on an unexpected service failure
            result = CommandResult(True, (reply(tr(source.language, "internal command failure"), color=ERROR),),
                                   accepted=False, code="unavailable")

        self._deliver(source, result)

    def _deliver(self, source, result, chat: bool = True) -> None:
        def deliver() -> None:
            if result.log_level is not None and self._log_sink is not None:
                for message in result.messages if result.log_messages is None else result.log_messages:
                    self._log_sink.write(result.log_level, strip_colors(message))
                if not chat or source.player_id is None:
                    return  # The console already shows the log entry.
            elif not chat:
                return  # chat=False keeps the console record only.
            sender = self._resolve_sender(source)
            for message in result.messages:
                _send(sender, source, message)
            # Permission may have changed while the worker queried the runtime.
            view = result.view if result.view is not None else menu_view(
                result, source, scripting=self._scripts is not None and self._scripts.config.enabled)
            if view is None or self._show_view is None:
                return
            if sender is None:
                dropped = "sender unresolved"
            elif self._authorize is not None and not self._authorize(source):
                dropped = "not authorized at delivery"
            else:
                self._show_view(sender, view)
                return
            if self._log_sink is not None:
                self._log_sink.write(
                    "warning", f"A {type(view).__name__} form for player {source.player_id} was dropped: {dropped}")

        self._bridge.post(deliver)
