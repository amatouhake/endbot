import queue
import threading
import unittest

from endstone_endbot.commands import CommandResult
from endstone_endbot.dispatch import AsyncCommandRunner, ServerThreadBridge, ServerThreadWorld


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
        runner = AsyncCommandRunner(service, bridge)
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

        world = ServerThreadWorld(World(), bridge)
        result = {}

        def worker():
            result.update(world.observe("bot-uuid"))

        thread = threading.Thread(target=worker)
        thread.start()
        scheduled.run_next()
        thread.join(1)

        self.assertFalse(thread.is_alive())
        self.assertEqual(result, {"identityId": "bot-uuid", "thread": main_thread})

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
