import queue
import threading
import unittest
from pathlib import Path

from endbot_core.command_runner import AsyncCommandRunner
from endbot_core.commands import CommandResult, reply, strip_colors
from endbot_core.events import Event, LoggerSink, reason
from endbot_core.principal import Principal

from endstone_endbot.dispatch import ServerThreadBridge, ServerThreadWorld


class ScheduledCallbacks:
    def __init__(self) -> None:
        self.callbacks = queue.Queue()

    def schedule(self, callback) -> None:
        self.callbacks.put(callback)

    def run_next(self, timeout=1.0) -> None:
        self.callbacks.get(timeout=timeout)()


class RecordingSink:
    """Record Event log writes as (level, line) pairs and as bare lines."""

    def __init__(self) -> None:
        self.calls = []
        self.lines = []

    def write(self, level: str, line: str) -> None:
        self.calls.append((level, line))
        self.lines.append(line)


class FakeLogger:
    """Record the level methods a LoggerSink calls."""

    def __init__(self) -> None:
        self.calls = []

    def info(self, message) -> None:
        self.calls.append(("info", message))

    def warning(self, message) -> None:
        self.calls.append(("warning", message))

    def error(self, message) -> None:
        self.calls.append(("error", message))


class DispatchTests(unittest.TestCase):
    def test_script_world_uses_captured_spawn_after_logoff_and_rejects_revoked_world_calls(self):
        from types import SimpleNamespace

        from endbot_core.i18n import LocalizedError

        origin = {"x": 1., "y": 64., "z": 2., "yaw": 90., "pitch": 0., "dimension": "Overworld"}
        source = Principal("player", player_id="controller", run_id="run1", origin=origin)
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

    def test_find_blocks_forwards_the_scan_centre_to_the_world(self):
        from types import SimpleNamespace

        calls = []

        class World:
            def find_blocks(self, *args):
                calls.append(args)
                return []

        bridge = SimpleNamespace(call=lambda callback: callback())
        world = ServerThreadWorld(World(), bridge, lambda source: None)
        world.find_blocks("bot-id", "minecraft:oak_log", 8, 10)
        world.find_blocks("bot-id", "minecraft:oak_log", 16, 3, [("relative", 0.0), 70.0, 2.0])
        self.assertEqual(calls, [("bot-id", "minecraft:oak_log", 8, 10, None),
                                 ("bot-id", "minecraft:oak_log", 16, 3, [("relative", 0.0), 70.0, 2.0])])

    def test_server_thread_world_methods_take_the_world_parameters(self):
        # world.py needs endstone, so read its signatures from the source; a missed
        # parameter here only fails on a real server.
        import ast
        import inspect

        path = Path(__file__).resolve().parents[1] / "src" / "endstone_endbot" / "world.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "EndstoneWorld")
        expected = {node.name: [arg.arg for arg in node.args.args if arg.arg != "self"]
                    for node in cls.body if isinstance(node, ast.FunctionDef)}
        checked = 0
        for name, method in vars(ServerThreadWorld).items():
            if name.startswith("_") or not callable(method) or name not in expected:
                continue
            with self.subTest(method=name):
                self.assertEqual([p for p in inspect.signature(method).parameters if p != "self"], expected[name])
            checked += 1
        self.assertGreater(checked, 10)

    def test_script_routing_passes_the_captured_principal_to_scripts(self):
        from types import SimpleNamespace

        from endbot_core.script_config import ScriptConfig

        scheduled = ScheduledCallbacks()
        bridge = ServerThreadBridge(scheduled.schedule)
        self.addCleanup(bridge.close)
        captured, closed = [], threading.Event()
        scripts = SimpleNamespace(config=ScriptConfig(enabled=True), cancel=lambda: None, close=closed.set)
        scripts.execute = lambda argv, source: captured.append((argv, source)) or CommandResult(True)
        player = SimpleNamespace(send_message=lambda message: None)
        principal = Principal("player", player_id="controller", grant_revision=7, origin={"x": 1, "y": 64, "z": 2})
        runner = AsyncCommandRunner(object(), bridge, lambda source: player, authorize=lambda source: True,
                                    scripts=scripts)
        self.addCleanup(runner.close)
        self.assertTrue(runner.submit(["run", "test"], principal, command="botscript"))
        scheduled.run_next()
        scheduled.run_next()
        self.assertEqual(captured, [(["run", "test"], principal)])
        runner.close()
        self.assertTrue(closed.wait(1))

    def test_menu_outcomes_open_their_screen_with_the_scripting_entry(self):
        from types import SimpleNamespace

        from endbot_core.forms import BotListView
        from endbot_core.script_config import ScriptConfig

        scheduled = ScheduledCallbacks()
        bridge = ServerThreadBridge(scheduled.schedule)
        self.addCleanup(bridge.close)
        shown = []
        scripts = SimpleNamespace(config=ScriptConfig(enabled=True), cancel=lambda: None, close=lambda: None)
        service = SimpleNamespace(execute=lambda argv, source: CommandResult(
            True, data={"menu": "bots", "bots": [{"name": "Alice", "connectionState": "online"}]}))
        player = SimpleNamespace(send_message=lambda message: None)
        runner = AsyncCommandRunner(service, bridge, lambda source: player, scripts=scripts,
                                    show_view=lambda sender, view: shown.append(view))
        self.addCleanup(runner.close)
        runner.submit(["menu"], Principal("player", player_id="controller"))
        scheduled.run_next()
        self.assertEqual(shown, [BotListView(({"name": "Alice", "connectionState": "online"},), scripting=True)])

    def test_form_group_guard_survives_authorized_worker_capture_and_root_dispatch(self):
        from types import SimpleNamespace

        from endbot_core.group_forms import GroupGuard

        scheduled = ScheduledCallbacks()
        bridge = ServerThreadBridge(scheduled.schedule)
        self.addCleanup(bridge.close)
        captured = []
        guard = GroupGuard("group-id", "miners", ("bot-uuid",))
        class Service:
            def execute_group(self, argv, source):
                captured.append((argv, source))
                return CommandResult(True)
        player = SimpleNamespace(send_message=lambda message: None)
        runner = AsyncCommandRunner(Service(), bridge, lambda source: player, authorize=lambda source: True)
        self.addCleanup(runner.close)
        principal = Principal("player", player_id="player-id", group_guard=guard)
        self.assertTrue(runner.submit(["delete", "#miners"], principal, command="botgroup"))
        scheduled.run_next()  # authorization on server thread
        scheduled.run_next()  # delivery
        self.assertEqual(captured[0][0], ["delete", "#miners"])
        self.assertIs(captured[0][1].group_guard, guard)
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
        runner.submit(["Bob", "spawn"], Principal())
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
        runner = AsyncCommandRunner(service, bridge, lambda source: sender)
        self.addCleanup(runner.close)
        self.addCleanup(bridge.close)

        self.assertTrue(runner.submit(["Alice", "reconnect"], Principal()))
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

        class Sender:
            def __init__(self):
                self.messages = []

            def send_message(self, message):
                self.messages.append(message)

        sender, offline = Sender(), Principal("player", player_id="00000000-0000-4000-8000-000000000001")
        player = Principal("player", player_id="00000000-0000-4000-8000-000000000002")
        console = Sender()
        senders = {player.player_id: sender, None: console}

        class Service:
            def poll_mining(self, authorize):
                def ended(principal, bot):
                    return Event(principal, "job.ended", {"bot": bot, "job": "destroy", "reason": reason("stopped")},
                                 "warning")

                return [ended(player, "Alice"), ended(offline, "Bob"), ended(Principal(), "Carol")]

            def execute(self, arguments, source):
                return CommandResult(True, ("Endbot: Alice: started",), log_level="info")

        sink = RecordingSink()
        logged = sink.calls
        runner = AsyncCommandRunner(
            Service(), bridge, lambda source: senders.get(source.player_id),
            log_sink=sink,
        )
        self.addCleanup(runner.close)
        runner.poll()
        for _ in range(3):
            scheduled.run_next()
        runner.submit(["Alice", "destroy", "continuous"], Principal())
        scheduled.run_next()
        # A controller who logged off still has the console log entry; the
        # console is not shown the same line twice.
        self.assertEqual([strip_colors(message) for message in sender.messages],
                         ["Endbot: Alice: destroy stopped: stopped"])
        self.assertEqual(console.messages, [])
        self.assertEqual(
            logged,
            [
                ("warning", "Endbot: Alice: destroy stopped: stopped"),
                ("warning", "Endbot: Bob: destroy stopped: stopped"),
                ("warning", "Endbot: Carol: destroy stopped: stopped"),
                ("info", "Endbot: Alice: started"),
            ],
        )

    def test_chat_false_keeps_the_console_log_and_sends_nothing(self) -> None:
        scheduled = ScheduledCallbacks()
        bridge = ServerThreadBridge(scheduled.schedule)
        self.addCleanup(bridge.close)

        class Sender:
            def __init__(self):
                self.messages = []

            def send_message(self, message):
                self.messages.append(message)

        sender = Sender()
        player = Principal("player", player_id="00000000-0000-4000-8000-000000000002")

        class Service:
            def vote(self, *args):
                raise AssertionError("unused")

        sink = RecordingSink()
        logged = sink.calls
        runner = AsyncCommandRunner(Service(), bridge, lambda source: sender, log_sink=sink)
        self.addCleanup(runner.close)
        runner._deliver(player, CommandResult(True, ("Endbot: Alice: started",), log_level="info"), chat=False)
        scheduled.run_next()
        self.assertEqual(sender.messages, [])
        self.assertEqual(logged, [("info", "Endbot: Alice: started")])

    def test_player_language_reaches_the_service_and_logs_stay_english(self) -> None:
        scheduled = ScheduledCallbacks()
        bridge = ServerThreadBridge(scheduled.schedule)
        self.addCleanup(bridge.close)
        sources = []

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
        sink = RecordingSink()
        logged = sink.lines
        runner = AsyncCommandRunner(
            Service(),
            bridge,
            lambda source: player if source.player_id else None,
            log_sink=sink,
        )
        self.addCleanup(runner.close)
        runner.submit(["Alice", "destroy", "stop"], Principal("player", player_id=Player.unique_id, language="ja"))
        scheduled.run_next()
        runner.submit(["Alice", "destroy", "stop"], Principal())
        scheduled.run_next()
        self.assertEqual([source.language for source in sources], ["ja", "en"])
        self.assertEqual(player.messages, ["ja"])
        self.assertEqual(logged, ["en", "en"])

    def test_only_players_receive_color_codes(self) -> None:
        scheduled = ScheduledCallbacks()
        bridge = ServerThreadBridge(scheduled.schedule)
        self.addCleanup(bridge.close)
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
            return player if source.player_id == player.unique_id else console

        sink = RecordingSink()
        logged = sink.lines
        runner = AsyncCommandRunner(Service(), bridge, resolve, log_sink=sink)
        self.addCleanup(runner.close)
        in_game = Principal("player", player_id=Player.unique_id)
        for arguments, principal in ((["plain"], in_game), (["plain"], Principal()), (["logged"], in_game)):
            runner.submit(arguments, principal)
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
        offline = Principal("player", player_id="00000000-0000-4000-8000-000000000001")
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

    def test_mining_batch_runs_every_raycast_in_one_bridge_call(self):
        bridge_calls, world_calls = [], []

        class Bridge:
            def call(self, callback):
                bridge_calls.append(callback)
                return callback()

        class World:
            def reachable_block(self, identity_id, block_type, reach):
                world_calls.append(("reachable_block", identity_id, block_type, reach))
                return [1, 64, 2]

            def resolve_mining(self, identity_id, parameters, sender):
                world_calls.append(("resolve_mining", identity_id, parameters, sender))
                if identity_id == "missing":
                    raise RuntimeError("gone")
                return {"blockPosition": [1, 64, 2]}

        world = ServerThreadWorld(World(), Bridge(), lambda source: None)
        results = world.resolve_mining_batch([
            {"identityId": "typed", "blockType": "minecraft:oak_log"},
            {"identityId": "untyped", "blockType": None},
            {"identityId": "missing", "blockType": None},
        ])
        self.assertEqual(len(bridge_calls), 1)
        self.assertTrue(results[0]["target"]["turn"])
        self.assertEqual(results[1]["target"]["blockPosition"], [1, 64, 2])
        self.assertEqual(results[2]["error"]["code"], "failed")
        self.assertEqual(len(world_calls), 4)

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
        runner = AsyncCommandRunner(service, bridge, lambda source: resolved.get(source.player_id))
        self.addCleanup(runner.close)
        self.addCleanup(bridge.close)

        self.assertTrue(runner.submit(["Alice", "status"], Principal("player", player_id=str(player.unique_id))))
        resolved.clear()
        release.set()
        scheduled.run_next()

        self.assertEqual(service.source, Principal("player", player_id=str(player.unique_id)))

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
            return source.authorization_revision == epoch[0]

        service = type(
            "Service", (), {"execute": lambda _, args, source: calls.append(args) or CommandResult(True, ("done",))}
        )()
        runner = AsyncCommandRunner(service, bridge, lambda source: player, authorize)
        self.addCleanup(runner.close)
        submitted = Principal("player", player_id="player-id", authorization_revision=epoch[0])
        self.assertTrue(runner.submit(["Bot", "despawn"], submitted))
        epoch[0] += 2  # Revoke and regrant while the main-thread authorization check is queued.
        scheduled.run_next()
        scheduled.run_next()
        self.assertEqual(calls, [])
        self.assertIn("permission was revoked", messages[0])
        self.assertEqual(authorization_threads, [main_thread])
        current = Principal("player", player_id="player-id", authorization_revision=epoch[0])
        self.assertTrue(runner.submit(["Bot", "status"], current))
        scheduled.run_next()
        scheduled.run_next()
        self.assertEqual(calls, [["Bot", "status"]])


class LoggerSinkTests(unittest.TestCase):
    def test_forwards_level_and_line_unchanged(self):
        logger = FakeLogger()
        sink = LoggerSink(logger)
        sink.write("info", "one")
        sink.write("warning", "two")
        sink.write("error", "three")
        self.assertEqual(logger.calls, [("info", "one"), ("warning", "two"), ("error", "three")])

    def test_falls_back_to_info_for_an_unknown_level(self):
        logger = FakeLogger()
        LoggerSink(logger).write("debug", "fallback")
        self.assertEqual(logger.calls, [("info", "fallback")])

    def test_runner_writes_the_same_event_and_audit_calls_a_recorder_receives(self):
        def run(sink):
            scheduled = ScheduledCallbacks()
            bridge = ServerThreadBridge(scheduled.schedule)
            self.addCleanup(bridge.close)
            runner = AsyncCommandRunner(object(), bridge, lambda source: None, log_sink=sink)
            self.addCleanup(runner.close)
            event = Event(Principal(), "job.ended",
                          {"bot": "Alice", "job": "destroy", "reason": reason("stopped")}, "warning")
            runner.publish(event)
            scheduled.run_next()
            result = CommandResult(True, ("Endbot: Alice: started",), log_level="info")
            runner._deliver(Principal(), result, chat=False)
            scheduled.run_next()

        logger, recorder = FakeLogger(), RecordingSink()
        run(LoggerSink(logger))
        run(recorder)  # Records the (level, line) writes the runner makes.
        self.assertEqual(logger.calls, recorder.calls)
        self.assertEqual(logger.calls, [
            ("warning", "Endbot: Alice: destroy stopped: stopped"),
            ("info", "Endbot: Alice: started"),
        ])
