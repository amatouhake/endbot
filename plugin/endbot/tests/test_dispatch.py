import queue
import threading
import unittest

from endstone_endbot.commands import CommandResult
from endstone_endbot.dispatch import AsyncCommandRunner, CommandSource, ServerThreadBridge, ServerThreadWorld


class ScheduledCallbacks:
    def __init__(self) -> None:
        self.callbacks = queue.Queue()

    def schedule(self, callback) -> None:
        self.callbacks.put(callback)

    def run_next(self, timeout=1.0) -> None:
        self.callbacks.get(timeout=timeout)()


class DispatchTests(unittest.TestCase):
    def test_slow_command_returns_immediately_and_delivers_on_server_thread(self) -> None:
        scheduled = ScheduledCallbacks()
        bridge = ServerThreadBridge(scheduled.schedule)
        release = threading.Event()
        executed = threading.Event()
        main_thread = threading.get_ident()

        class Service:
            def execute(self, arguments, sender):
                self.thread = threading.get_ident()
                executed.set()
                release.wait(1)
                return CommandResult(True, ("Endbot: done",))

        class Sender:
            def __init__(self):
                self.messages = []
                self.thread = None

            def send_message(self, message):
                self.thread = threading.get_ident()
                self.messages.append(message)

        service = Service()
        sender = Sender()
        runner = AsyncCommandRunner(service, bridge, lambda source: source.sender)
        self.addCleanup(runner.close)
        self.addCleanup(bridge.close)

        self.assertTrue(runner.submit(["Alice", "reconnect"], sender))
        self.assertTrue(executed.wait(1))
        self.assertNotEqual(service.thread, main_thread)
        self.assertEqual(sender.messages, [])
        release.set()
        scheduled.run_next()

        self.assertEqual(sender.messages, ["Endbot: done"])
        self.assertEqual(sender.thread, main_thread)

    def test_world_calls_are_marshaled_to_server_thread(self) -> None:
        scheduled = ScheduledCallbacks()
        bridge = ServerThreadBridge(scheduled.schedule)
        self.addCleanup(bridge.close)
        main_thread = threading.get_ident()

        class World:
            def observe(self, identity_id):
                return {"identityId": identity_id, "thread": threading.get_ident()}

        world = ServerThreadWorld(World(), bridge, lambda source: source.sender)
        result = {}

        def worker():
            result.update(world.observe("bot-uuid"))

        thread = threading.Thread(target=worker)
        thread.start()
        scheduled.run_next()
        thread.join(1)

        self.assertFalse(thread.is_alive())
        self.assertEqual(result, {"identityId": "bot-uuid", "thread": main_thread})

    def test_look_at_forwards_identity_id_across_the_bridge(self) -> None:
        scheduled = ScheduledCallbacks()
        bridge = ServerThreadBridge(scheduled.schedule)
        self.addCleanup(bridge.close)
        main_thread = threading.get_ident()

        class World:
            def look_at(self, identity_id, coordinates):
                return identity_id, coordinates, threading.get_ident()

        world = ServerThreadWorld(World(), bridge, lambda source: source.sender)
        result = {}

        def worker():
            result["value"] = world.look_at("bot-uuid", [1.0, 64.0, 2.0])

        thread = threading.Thread(target=worker)
        thread.start()
        scheduled.run_next()
        thread.join(1)

        self.assertFalse(thread.is_alive())
        self.assertEqual(result["value"], ("bot-uuid", [1.0, 64.0, 2.0], main_thread))

    def test_disconnected_player_is_not_retained_or_messaged(self) -> None:
        scheduled = ScheduledCallbacks()
        bridge = ServerThreadBridge(scheduled.schedule)
        release = threading.Event()
        resolved = {}

        class Service:
            def execute(self, arguments, source):
                self.source = source
                release.wait(1)
                return CommandResult(True, ("Endbot: done",))

        class Player:
            unique_id = "00000000-0000-4000-8000-000000000001"
            location = object()

        service = Service()
        player = Player()
        resolved[str(player.unique_id)] = player
        runner = AsyncCommandRunner(
            service,
            bridge,
            lambda source: resolved.get(source.player_id) if source.player_id else source.sender,
        )
        self.addCleanup(runner.close)
        self.addCleanup(bridge.close)

        self.assertTrue(runner.submit(["Alice", "status"], player))
        resolved.clear()
        release.set()
        scheduled.run_next()

        self.assertEqual(service.source, CommandSource(player_id=str(player.unique_id)))
        self.assertIsNone(service.source.sender)

    def test_disable_releases_pending_server_thread_calls(self) -> None:
        scheduled = ScheduledCallbacks()
        bridge = ServerThreadBridge(scheduled.schedule)
        result = []

        def worker():
            try:
                bridge.call(lambda: "unreachable")
            except RuntimeError as error:
                result.append(str(error))

        thread = threading.Thread(target=worker)
        thread.start()
        scheduled.callbacks.get(timeout=1)
        bridge.close()
        thread.join(1)

        self.assertFalse(thread.is_alive())
        self.assertEqual(result, ["Endbot plugin is disabled"])
