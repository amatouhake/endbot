"""Server-thread boundary for Endstone API work."""

from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeoutError

from endbot_core.link import resolve_mining_batch
from endbot_core.principal import Principal


class ServerThreadBridge:
    """Schedule Endstone API calls on the server thread from one worker."""

    def __init__(self, schedule: Callable[[Callable[[], None]], object]) -> None:
        self._schedule = schedule
        self._lock = threading.Lock()
        self._open = True
        self._pending: set[Future] = set()

    def call(self, callback: Callable[[], object], timeout: float | None = None):
        """Run ``callback`` on the server thread and wait for it.

        With ``timeout``, a stuck tick fails this call with TimeoutError and the
        callback is skipped if the server thread reaches it later.
        """
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
        try:
            return future.result(timeout)
        except FutureTimeoutError:
            self._finish(future, error=TimeoutError(f"the server thread did not answer within {timeout:g} s"))
            # The callback may have finished first; then its own result stands.
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

    def resolve_mining_batch(self, requests):
        # One server-thread task for every retarget of a poll; each item's
        # failure stays with that item.
        return self._bridge.call(lambda: resolve_mining_batch(self._world, requests))

    def resolve_dismount(self, identity_id, vehicle_runtime_id):
        return self._bridge.call(lambda: self._world.resolve_dismount(identity_id, vehicle_runtime_id))

    def assert_name_available(self, name, identity_id=None):
        return self._bridge.call(lambda: self._world.assert_name_available(name, identity_id))

    def capacity(self, bot_ids):
        return self._bridge.call(lambda: self._world.capacity(bot_ids))
