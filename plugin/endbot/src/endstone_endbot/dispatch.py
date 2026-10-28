"""Thread boundary for slow runtime control requests and Endstone API work."""

from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor

from endstone_endbot.commands import ERROR, WARNING, CommandResult, reply, strip_colors
from endstone_endbot.events import Event, render
from endstone_endbot.i18n import tr
from endstone_endbot.menu_views import menu_view
from endstone_endbot.principal import Principal


class ServerThreadBridge:
    """Schedule Endstone API calls on the server thread from one worker."""

    def __init__(self, schedule: Callable[[Callable[[], None]], object]) -> None:
        self._schedule = schedule
        self._lock = threading.Lock()
        self._open = True
        self._pending: set[Future] = set()

    def call(self, callback: Callable[[], object]):
        future: Future = Future()
        with self._lock:
            if not self._open:
                raise RuntimeError("Endbot plugin is disabled")
            self._pending.add(future)

        def invoke() -> None:
            with self._lock:
                if not self._open or future.done():
                    return
            try:
                result = callback()
            except Exception as error:  # noqa: BLE001 - propagate arbitrary callback failures through the Future
                self._finish(future, error=error)
            else:
                self._finish(future, result=result)

        try:
            self._schedule(invoke)
        except Exception as error:  # noqa: BLE001 - preserve scheduler failures for the waiting worker
            self._finish(future, error=error)
        return future.result()

    def post(self, callback: Callable[[], None]) -> bool:
        with self._lock:
            if not self._open:
                return False

        def invoke() -> None:
            with self._lock:
                if not self._open:
                    return
            callback()

        try:
            self._schedule(invoke)
        except RuntimeError:
            return False
        return True

    def close(self) -> None:
        with self._lock:
            self._open = False
            pending = tuple(self._pending)
            self._pending.clear()
        for future in pending:
            if not future.done():
                future.set_exception(RuntimeError("Endbot plugin is disabled"))

    def _finish(self, future: Future, *, result=None, error: BaseException | None = None) -> None:
        with self._lock:
            self._pending.discard(future)
            if future.done():
                return
            if error is not None:
                future.set_exception(error)
            else:
                future.set_result(result)


class ServerThreadWorld:
    """Expose the world adapter to workers without off-thread Endstone access."""

    def __init__(self, world, bridge: ServerThreadBridge, resolve_sender: Callable[[Principal], object]) -> None:
        self._world = world
        self._bridge = bridge
        self._resolve_sender = resolve_sender

    def _sender(self, source: Principal):
        sender = self._resolve_sender(source)
        if source.player_id is not None and sender is None and source.run_id is None:
            raise ValueError("Command player is no longer online")
        return sender

    def default_spawn(self, sender):
        def resolve():
            player = self._sender(sender)
            origin = sender.origin
            if player is None and sender.run_id is not None and origin is not None:
                return {"coordinates": [origin[axis] for axis in ("x", "y", "z")],
                        "rotation": [origin[axis] for axis in ("yaw", "pitch")], "dimension": origin["dimension"]}
            return self._world.default_spawn(player)

        return self._bridge.call(resolve)

    def resolve_spawn_placement(self, placement, sender):
        return self._bridge.call(lambda: self._world.resolve_spawn_placement(placement, self._sender(sender)))

    def queue_spawn_placement(self, identity_id, placement):
        return self._bridge.call(lambda: self._world.queue_spawn_placement(identity_id, placement))

    def clear_pending_placement(self, identity_id):
        return self._bridge.call(lambda: self._world.clear_pending_placement(identity_id))

    def observe(self, identity_id):
        return self._bridge.call(lambda: self._world.observe(identity_id))

    def target(self, identity_id):
        return self._bridge.call(lambda: self._world.target(identity_id))

    def block_info(self, identity_id, coordinates):
        return self._bridge.call(lambda: self._world.block_info(identity_id, coordinates))

    def reachable_block(self, identity_id, block_type, reach):
        return self._bridge.call(lambda: self._world.reachable_block(identity_id, block_type, reach))

    def find_blocks(self, identity_id, block_type, radius, limit, coordinates=None):
        return self._bridge.call(lambda: self._world.find_blocks(identity_id, block_type, radius, limit, coordinates))

    def describe_entities(self, identity_id, runtime_ids):
        return self._bridge.call(lambda: self._world.describe_entities(identity_id, runtime_ids))

    def inventory(self, name, identity_id=None):
        return self._bridge.call(lambda: self._world.inventory(name, identity_id))

    def held_book_pages(self, player_id, writable=False):
        return self._bridge.call(lambda: self._world.held_book_pages(player_id, writable))

    def fill_held_book(self, player_id, pages, expected=None):
        return self._bridge.call(lambda: self._world.fill_held_book(player_id, pages, expected))

    def teleport(self, identity_id, parameters, sender):
        return self._bridge.call(lambda: self._world.teleport(identity_id, parameters, self._sender(sender)))

    def look_at(self, identity_id, coordinates):
        return self._bridge.call(lambda: self._world.look_at(identity_id, coordinates))

    def resolve_interaction(self, identity_id, parameters, sender):
        return self._bridge.call(lambda: self._world.resolve_interaction(identity_id, parameters, self._sender(sender)))

    def resolve_mining(self, identity_id, parameters, sender):
        # A continuous retarget passes no sender: it only reads the Bot's own
        # sight and must keep working after its controller logs off.
        return self._bridge.call(
            lambda: self._world.resolve_mining(
                identity_id, parameters, None if sender is None else self._sender(sender)
            )
        )

    def resolve_dismount(self, identity_id, vehicle_runtime_id):
        return self._bridge.call(lambda: self._world.resolve_dismount(identity_id, vehicle_runtime_id))

    def assert_name_available(self, name, identity_id=None):
        return self._bridge.call(lambda: self._world.assert_name_available(name, identity_id))


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
        bridge: ServerThreadBridge,
        resolve_sender: Callable[[Principal], object],
        authorize: Callable[[Principal], bool] | None = None,
        log: Callable[[str, str], None] | None = None,
        show_view: Callable[[object, object], None] | None = None,
        scripts=None,
    ) -> None:
        self._service = service
        self._bridge = bridge
        self._resolve_sender = resolve_sender
        # One two-layer check for commands, runs and jobs (see Principal).
        self._authorize = authorize
        self._log = log
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
            if self._log is not None:
                for line in render(event, "log"):
                    self._log(event.level, strip_colors(line))
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

    def close(self) -> None:
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
        # Queued commands check _open; cleanup follows any in-flight request.
        self._executor.shutdown(wait=False)
        self._poll_executor.shutdown(wait=False)

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
            if result.log_level is not None and self._log is not None:
                for message in result.messages if result.log_messages is None else result.log_messages:
                    self._log(result.log_level, strip_colors(message))
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
            if (
                view is not None
                and self._show_view is not None
                and sender is not None
                and (self._authorize is None or self._authorize(source))
            ):
                self._show_view(sender, view)

        self._bridge.post(deliver)
