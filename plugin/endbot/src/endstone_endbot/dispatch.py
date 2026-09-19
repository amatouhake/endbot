"""Thread boundary for slow runtime control requests and Endstone API work."""

from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor

from endstone_endbot.commands import CommandResult


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

    def __init__(self, world, bridge: ServerThreadBridge) -> None:
        self._world = world
        self._bridge = bridge

    def default_spawn(self, sender):
        return self._bridge.call(lambda: self._world.default_spawn(sender))

    def resolve_spawn_placement(self, placement, sender):
        return self._bridge.call(lambda: self._world.resolve_spawn_placement(placement, sender))

    def queue_spawn_placement(self, identity_id, placement):
        return self._bridge.call(lambda: self._world.queue_spawn_placement(identity_id, placement))

    def clear_pending_placement(self, identity_id):
        return self._bridge.call(lambda: self._world.clear_pending_placement(identity_id))

    def observe(self, identity_id):
        return self._bridge.call(lambda: self._world.observe(identity_id))

    def teleport(self, identity_id, parameters, sender):
        return self._bridge.call(lambda: self._world.teleport(identity_id, parameters, sender))

    def look_at(self, name, coordinates):
        return self._bridge.call(lambda: self._world.look_at(name, coordinates))

    def assert_name_available(self, name, identity_id=None):
        return self._bridge.call(lambda: self._world.assert_name_available(name, identity_id))


class AsyncCommandRunner:
    """Run ordered command service calls away from the BDS command thread."""

    def __init__(self, service, bridge: ServerThreadBridge) -> None:
        self._service = service
        self._bridge = bridge
        # One worker preserves the command ordering previously provided by the
        # BDS command thread while moving all blocking socket waits off it.
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="endbot-control")
        self._lock = threading.Lock()
        self._open = True

    def submit(self, arguments: list[str], sender) -> bool:
        with self._lock:
            if not self._open:
                return False
            try:
                self._executor.submit(self._execute, list(arguments), sender)
            except RuntimeError:
                return False
        return True

    def close(self) -> None:
        with self._lock:
            if not self._open:
                return
            self._open = False
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _execute(self, arguments: list[str], sender) -> None:
        try:
            result = self._service.execute(arguments, sender)
        except Exception:  # noqa: BLE001 - never strand command delivery on an unexpected service failure
            result = CommandResult(True, ("Endbot: internal command failure",))

        def deliver() -> None:
            for message in result.messages:
                sender.send_message(message)

        self._bridge.post(deliver)
