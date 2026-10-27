import queue
import threading
import unittest

from endstone_endbot.commands import CommandResult, reply, strip_colors
from endstone_endbot.dispatch import AsyncCommandRunner, CommandSource, ServerThreadBridge, ServerThreadWorld


class ScheduledCallbacks:
    def __init__(self) -> None:
        self.callbacks = queue.Queue()

    def schedule(self, callback) -> None:
        self.callbacks.put(callback)

    def run_next(self, timeout=1.0) -> None:
        self.callbacks.get(timeout=timeout)()


class DispatchTests(unittest.TestCase):
    def test_script_world_uses_captured_spawn_after_logoff_and_rejects_revoked_world_calls(self):
        from types import SimpleNamespace

        from endstone_endbot.i18n import LocalizedError

        origin = {"x": 1., "y": 64., "z": 2., "yaw": 90., "pitch": 0., "dimension": "Overworld"}
        source = CommandSource(player_id="controller", script_run_id="run1", script_origin=origin)
        bridge = SimpleNamespace(call=lambda callback: callback())
        world = ServerThreadWorld(object(), bridge, lambda source: None)
        self.assertEqual(world.default_spawn(source), {"coordinates": [1., 64., 2.],
                         "rotation": [90., 0.], "dimension": "Overworld"})
        def revoked(source):
            raise LocalizedError("Script authority was revoked")
        class World:
            def teleport(self, *args):
                raise AssertionError("A revoked script must not reach world mutation")
        world = ServerThreadWorld(World(), bridge, revoked)
        with self.assertRaises(LocalizedError):
            world.teleport("bot-id", {"coordinates": [1., 64., 2.]}, source)

    def test_script_routing_captures_grant_origin_and_copies_form_values(self):
        from types import SimpleNamespace

        from endstone_endbot.script_config import ScriptConfig
        from endstone_endbot.script_forms import ScriptGuard

        scheduled = ScheduledCallbacks()
        bridge = ServerThreadBridge(scheduled.schedule)
        self.addCleanup(bridge.close)
        captured, closed = [], threading.Event()
        scripts = SimpleNamespace(config=ScriptConfig(enabled=True), cancel=lambda: None, close=closed.set)
        scripts.execute = lambda argv, source: captured.append((argv, source)) or CommandResult(True)
        player = SimpleNamespace(unique_id="controller", location=object(), name="Caller", xuid="123")
        origin = {"x": 1, "y": 64, "z": 2}
        guard = ScriptGuard("r1", "test", values={"name": "Alice"})
        runner = AsyncCommandRunner(object(), bridge, lambda source: player, authorize=lambda source: True,
                                    scripts=scripts, grant_revision=lambda sender: 7, origin=lambda sender: origin)
        self.addCleanup(runner.close)
        self.assertTrue(runner.submit(["run", "test"], player, command="botscript", group_guard=guard))
        guard.values["name"] = "Changed"
        scheduled.run_next()
        scheduled.run_next()
        self.assertEqual(captured[0][0], ["run", "test"])
        self.assertEqual(captured[0][1].grant_revision, 7)
        self.assertEqual(captured[0][1].script_origin, origin)
        self.assertEqual(captured[0][1].group_guard.values, {"name": "Alice"})
        runner.close()
        self.assertTrue(closed.wait(1))

    def test_form_group_guard_survives_authorized_worker_capture_and_root_dispatch(self):
        from types import SimpleNamespace

        from endstone_endbot.group_forms import GroupGuard

        scheduled = ScheduledCallbacks()
        bridge = ServerThreadBridge(scheduled.schedule)
        self.addCleanup(bridge.close)
        captured = []
        guard = GroupGuard("group-id", "miners", ("bot-uuid",))
        class Service:
            def execute_group(self, argv, source):
                captured.append((argv, source))
                return CommandResult(True)
        player = SimpleNamespace(unique_id="player-id", location=object(), name="Tester", xuid="123")
        runner = AsyncCommandRunner(Service(), bridge, lambda source: player, authorize=lambda source: True)
        self.addCleanup(runner.close)
        self.assertTrue(runner.submit(["delete", "#miners"], player, command="botgroup", group_guard=guard))
        scheduled.run_next()  # authorization on server thread
        scheduled.run_next()  # delivery
        self.assertEqual(captured[0][0], ["delete", "#miners"])
        self.assertIs(captured[0][1].group_guard, guard)
        self.assertIsNone(captured[0][1].sender)
        self.assertEqual(captured[0][1].player_id, "player-id")

    def test_periodic_poll_is_coalesced_and_not_blocked_by_a_slow_command(self):
        scheduled = ScheduledCallbacks()
        bridge = ServerThreadBridge(scheduled.schedule)
        self.addCleanup(bridge.close)
        executing, release_command, polled, release_poll, closed = (threading.Event() for _ in range(5))
        calls = []

        class Service:
            def poll_mining(self, authorize):
                calls.append("poll")
                polled.set()
                release_poll.wait(1)

            def execute(self, arguments, source):
                executing.set()
                release_command.wait(1)
                return CommandResult(True, ())

            def close(self):
                calls.append("close")
                closed.set()

        runner = AsyncCommandRunner(Service(), bridge, lambda source: None)
        self.addCleanup(runner.close)
        # A spawn can block the command worker for longer than the mining lease.
        runner.submit(["Bob", "spawn"], object())
        self.assertTrue(executing.wait(1))
        runner.poll()
        self.assertTrue(polled.wait(1))
        for _ in range(10):
            runner.poll()
        release_poll.set()
        release_command.set()
        runner.close()
        self.assertTrue(closed.wait(1))
        runner.poll()
        self.assertEqual(calls, ["poll", "close"])

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

    def test_mining_notices_and_logged_results_reach_console_log_and_sender(self) -> None:
        scheduled = ScheduledCallbacks()
        bridge = ServerThreadBridge(scheduled.schedule)
        self.addCleanup(bridge.close)
        logged = []

        class Sender:
            def __init__(self):
                self.messages = []

            def send_message(self, message):
                self.messages.append(message)

        sender, offline = Sender(), CommandSource(player_id="00000000-0000-4000-8000-000000000001")
        player = CommandSource(player_id="00000000-0000-4000-8000-000000000002", sender=sender)
        console = Sender()

        class Service:
            def poll_mining(self, authorize):
                return [(player, "warning", "Endbot: Alice: stopped", "Endbot: Alice: stopped"),
                        (offline, "warning", "Endbot: Bob: stopped", "Endbot: Bob: stopped"),
                        (CommandSource(sender=console), "warning", "Endbot: Carol: stopped", "Endbot: Carol: stopped")]

            def execute(self, arguments, source):
                return CommandResult(True, ("Endbot: Alice: started",), log_level="info")

        runner = AsyncCommandRunner(
            Service(), bridge, lambda source: source.sender, log=lambda level, message: logged.append((level, message))
        )
        self.addCleanup(runner.close)
        runner.poll()
        for _ in range(3):
            scheduled.run_next()
        runner.submit(["Alice", "destroy", "continuous"], console)
        scheduled.run_next()
        # A controller who logged off still has the console log entry; the
        # console is not shown the same line twice.
        self.assertEqual(sender.messages, ["Endbot: Alice: stopped"])
        self.assertEqual(console.messages, [])
        self.assertEqual(
            logged,
            [
                ("warning", "Endbot: Alice: stopped"),
                ("warning", "Endbot: Bob: stopped"),
                ("warning", "Endbot: Carol: stopped"),
                ("info", "Endbot: Alice: started"),
            ],
        )

    def test_chat_false_keeps_the_console_log_and_sends_nothing(self) -> None:
        scheduled = ScheduledCallbacks()
        bridge = ServerThreadBridge(scheduled.schedule)
        self.addCleanup(bridge.close)
        logged = []

        class Sender:
            def __init__(self):
                self.messages = []

            def send_message(self, message):
                self.messages.append(message)

        sender = Sender()
        player = CommandSource(player_id="00000000-0000-4000-8000-000000000002", sender=sender)

        class Service:
            def vote(self, *args):
                raise AssertionError("unused")

        runner = AsyncCommandRunner(
            Service(), bridge, lambda source: source.sender, log=lambda level, message: logged.append((level, message))
        )
        self.addCleanup(runner.close)
        runner._deliver(player, CommandResult(True, ("Endbot: Alice: started",), log_level="info"), chat=False)
        scheduled.run_next()
        self.assertEqual(sender.messages, [])
        self.assertEqual(logged, [("info", "Endbot: Alice: started")])
    def test_player_language_is_captured_and_logs_stay_english(self) -> None:
        scheduled = ScheduledCallbacks()
        bridge = ServerThreadBridge(scheduled.schedule)
        self.addCleanup(bridge.close)
        logged, sources = [], []

        class Player:
            unique_id = "00000000-0000-4000-8000-000000000003"
            location = object()

            def __init__(self):
                self.messages = []

            def send_message(self, message):
                self.messages.append(message)

        class Service:
            def poll_mining(self, authorize):
                return []

            def execute(self, arguments, source):
                sources.append(source)
                return CommandResult(True, ("ja",), log_level="info", log_messages=("en",))

        player = Player()
        runner = AsyncCommandRunner(
            Service(),
            bridge,
            lambda source: player if source.player_id else None,
            log=lambda level, message: logged.append(message),
            language=lambda sender: "ja",
        )
        self.addCleanup(runner.close)
        runner.submit(["Alice", "destroy", "stop"], player)
        scheduled.run_next()
        runner.submit(["Alice", "destroy", "stop"], object())
        scheduled.run_next()
        self.assertEqual([source.language for source in sources], ["ja", "en"])
        self.assertEqual(player.messages, ["ja"])
        self.assertEqual(logged, ["en", "en"])

    def test_only_players_receive_color_codes(self) -> None:
        scheduled = ScheduledCallbacks()
        bridge = ServerThreadBridge(scheduled.schedule)
        self.addCleanup(bridge.close)
        logged = []
        colored = reply("destroy stopped", "Alice")

        class Sender:
            def __init__(self):
                self.messages = []

            def send_message(self, message):
                self.messages.append(message)

        class Player(Sender):
            unique_id = "00000000-0000-4000-8000-000000000002"
            location = None

        player, console = Player(), Sender()

        class Service:
            def execute(self, arguments, source):
                log_level = "info" if arguments == ["logged"] else None
                return CommandResult(True, (colored,), log_level=log_level)

        def resolve(source):
            return player if source.player_id == player.unique_id else source.sender

        runner = AsyncCommandRunner(Service(), bridge, resolve, log=lambda level, message: logged.append(message))
        self.addCleanup(runner.close)
        for arguments, sender in ((["plain"], player), (["plain"], console), (["logged"], player)):
            runner.submit(arguments, sender)
            scheduled.run_next()

        self.assertNotEqual(colored, strip_colors(colored))
        self.assertEqual(player.messages, [colored, colored])
        self.assertEqual(console.messages, ["Endbot: Alice: destroy stopped"])
        self.assertEqual(logged, ["Endbot: Alice: destroy stopped"])

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

    def test_inventory_read_is_marshaled_to_server_thread(self):
        scheduled = ScheduledCallbacks()
        bridge = ServerThreadBridge(scheduled.schedule)
        self.addCleanup(bridge.close)
        main_thread = threading.get_ident()

        class World:
            def inventory(self, name, identity):
                return {"name": name, "identity": identity, "thread": threading.get_ident()}

        world = ServerThreadWorld(World(), bridge, lambda source: source.sender)
        result = {}
        thread = threading.Thread(target=lambda: result.update(world.inventory("Alice", "bot-uuid")))
        thread.start()
        scheduled.run_next()
        thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result, {"name": "Alice", "identity": "bot-uuid", "thread": main_thread})

    def test_continuous_retarget_works_after_the_controller_logs_off(self) -> None:
        scheduled = ScheduledCallbacks()
        bridge = ServerThreadBridge(scheduled.schedule)
        self.addCleanup(bridge.close)

        class World:
            def resolve_mining(self, identity_id, parameters, sender):
                return {"sender": sender}

        # The controller is offline: its source no longer resolves to a player.
        world = ServerThreadWorld(World(), bridge, lambda source: None)
        offline = CommandSource(player_id="00000000-0000-4000-8000-000000000001")
        results = {}

        def worker():
            results["retarget"] = world.resolve_mining("bot-uuid", {"continuous": True}, None)
            try:
                world.resolve_mining("bot-uuid", {"coordinates": [0, 0, 0]}, offline)
            except ValueError as error:
                results["explicit"] = str(error)

        thread = threading.Thread(target=worker)
        thread.start()
        scheduled.run_next()
        scheduled.run_next()
        thread.join(1)
        self.assertEqual(results["retarget"], {"sender": None})
        # Commands with Bot-relative coordinates still require their sender.
        self.assertIn("no longer online", results["explicit"])

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

    def test_revoked_then_regranted_queued_command_never_reaches_service(self):
        scheduled = ScheduledCallbacks()
        bridge = ServerThreadBridge(scheduled.schedule)
        self.addCleanup(bridge.close)
        epoch = [1]
        calls, messages, authorization_threads = [], [], []
        main_thread = threading.get_ident()
        player = type(
            "Player",
            (),
            {
                "unique_id": "player-id",
                "location": object(),
                "send_message": lambda _, message: messages.append(message),
            },
        )()

        def authorize(source):
            authorization_threads.append(threading.get_ident())
            self.assertIsNone(source.sender)
            return source.authorization_revision == epoch[0]

        service = type(
            "Service", (), {"execute": lambda _, args, source: calls.append(args) or CommandResult(True, ("done",))}
        )()
        runner = AsyncCommandRunner(service, bridge, lambda source: player, authorize, lambda sender: epoch[0])
        self.addCleanup(runner.close)
        self.assertTrue(runner.submit(["Bot", "despawn"], player))
        epoch[0] += 2  # Revoke and regrant while the main-thread authorization check is queued.
        scheduled.run_next()
        scheduled.run_next()
        self.assertEqual(calls, [])
        self.assertIn("permission was revoked", messages[0])
        self.assertEqual(authorization_threads, [main_thread])
        self.assertTrue(runner.submit(["Bot", "status"], player))
        scheduled.run_next()
        scheduled.run_next()
        self.assertEqual(calls, [["Bot", "status"]])
