import unittest

from endbot_core.commands import UNREACHABLE_POLL_LIMIT, WAITING_NOTICE_POLLS
from endbot_core.control import RuntimeControlError
from endbot_core.i18n import LocalizedError
from endbot_core.limits import BotLimits
from endbot_core.link import LinkError, error_from_wire, error_to_wire, resolve_mining_batch
from endbot_core.operations import Operation
from endbot_core.ownership import Ownership, OwnershipStore
from endbot_core.principal import Principal
from plain_service import PlainCommandService


class FakeControl:
    def __init__(self) -> None:
        self.calls = []
        self.bots = {}

    def request(self, operation, **parameters):
        self.calls.append((operation, parameters))
        name = parameters.get("name", "")
        if operation in {"list", "profiles"}:
            return list(self.bots.values())
        if operation == "status":
            if name not in self.bots:
                raise RuntimeControlError("not_found", f"Bot {name} does not exist")
            return self.bots[name]
        if operation == "spawn":
            if name in self.bots:
                return {**self.bots[name], "alreadyOnline": True, "created": False}
            value = {
                "identityId": f"00000000-0000-4000-8000-00000000000{len(self.bots) + 1}",
                "name": name,
                "desiredState": "online",
                "connectionState": "connecting",
                "created": True,
                "alreadyOnline": False,
            }
            self.bots[name] = value
            return value
        if operation == "rename":
            value = self.bots.pop(name)
            value = {**value, "name": parameters["newName"], "connectionState": "reconnecting"}
            self.bots[value["name"]] = value
            return value
        if operation == "hotbar":
            return {**self.bots[name], "selectedHotbarSlot": parameters.get("slot", 1)}
        if operation == "radius":
            if "value" in parameters:
                value = parameters["value"]
                self.bots[name] = {
                    **self.bots[name],
                    "chunkRadius": 5 if value == "default" else value,
                    "chunkRadiusSource": "default" if value == "default" else "bot",
                    "serverChunkRadius": None,
                }
            return {"chunkRadius": 5, "chunkRadiusSource": "default", "serverChunkRadius": None, **self.bots[name]}
        if operation == "mine":
            if parameters.get("mode") == "start":
                self.bots[name]["mining"] = {
                    "state": "waiting", "continuous": True, "loopId": f"loop-{len(self.calls)}"
                }
            elif parameters.get("mode") == "stop":
                self.bots[name]["mining"] = {"state": "canceled"}
        return {**self.bots[name], "connectionState": "online"}


class FakeWorld:
    def __init__(self) -> None:
        self.placements = []
        self.cleared_placements = []
        self.collisions = {}
        self.invalid_dimensions = set()
        self.look_targets = []

    def default_spawn(self, sender):
        if getattr(sender, "is_console", False):
            return None
        return {"coordinates": [1, 2, 3], "dimension": "minecraft:overworld", "rotation": [0, 0]}

    def resolve_spawn_placement(self, placement, sender):
        if placement.get("dimension") in self.invalid_dimensions:
            raise ValueError("Unknown dimension")
        if sender is None and "dimension" not in placement:
            raise ValueError("A dimension is required")
        return {**placement, "resolved": True}

    def resolve_interaction(self, identity_id, parameters, sender):
        return {"blockPosition": [1, 64, 2], "blockRuntimeId": 42, "face": parameters["face"]}

    def resolve_mining(self, identity_id, parameters, sender):
        return {
            "blockPosition": [1, 64, 2],
            "blockRuntimeId": 42,
            "blockName": "minecraft:stone",
            "face": parameters.get("face", 1),
            "yaw": 0.0,
            "pitch": 20.0,
            "dimension": "Overworld",
        }

    def resolve_mining_batch(self, requests):
        return resolve_mining_batch(self, requests)

    def queue_spawn_placement(self, identity_id, placement):
        self.placements.append((identity_id, placement))

    def clear_pending_placement(self, identity_id):
        self.cleared_placements.append(identity_id)
        self.placements = [entry for entry in self.placements if entry[0] != identity_id]

    def observe(self, identity_id):
        return None

    def inventory(self, name, identity_id=None):
        raise ValueError("Bot is not present in the world")

    def describe_entities(self, identity_id, runtime_ids):
        return {}

    def look_at(self, identity_id, coordinates):
        self.look_targets.append((identity_id, coordinates))
        return (90.0, -10.0)

    def assert_name_available(self, name, identity_id=None):
        if name in self.collisions and self.collisions[name] != identity_id:
            raise ValueError("online player collision")


class CommandServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.control = FakeControl()
        self.world = FakeWorld()
        self.service = PlainCommandService(self.control, self.world)

    def test_reserved_names_cannot_be_created_or_taken_by_rename(self):
        # `/bot help spawn` is help itself; a script or the web can still ask for a Bot called help.
        for name in ("list", "PING", "menu"):
            with self.subTest(name=name):
                result = self.service.execute([name, "spawn"])
                self.assertFalse(result.accepted)
                self.assertIn(f"{name} is a reserved name; choose another name", result.message)
                self.assertEqual(result.code, "invalid_argument")
        self.assertNotIn("spawn", [operation for operation, _ in self.control.calls])
        self.service.execute(["Alice", "spawn"])
        renamed = self.service.execute(["Alice", "rename", "Menu"])
        self.assertIn("Menu is a reserved name", renamed.message)
        self.assertNotIn("rename", [operation for operation, _ in self.control.calls])
        # Typed operations from scripts and forms reach the same check.
        from endbot_core.i18n import LocalizedError
        from endbot_core.operations import Operation

        # As text, `help spawn` would silently become help; the typed entry says why instead.
        with self.assertRaisesRegex(LocalizedError, "help is a reserved name"):
            self.service.execute_operation(Operation("spawn", "help"))
        self.assertEqual(self.service.execute_operation(Operation("help", None, {"topic": "spawn"})).message,
                         "Endbot help: spawn")

    def test_existing_reserved_profiles_are_kept_and_pointed_at_rename(self):
        self.control.bots["list"] = {"identityId": "00000000-0000-4000-8000-000000000009", "name": "list",
                                     "desiredState": "offline", "connectionState": "offline"}
        self.assertTrue(self.service.execute(["list", "spawn"]).accepted)
        listing = self.service.execute(["list"]).message
        self.assertIn("list [offline] (reserved name; rename it)", listing)
        player = type("Player", (), {"player_id": "p", "language": "en"})()
        summary = self.service.execute(["list", "menu"], player).data["summary"]
        self.assertTrue(summary.endswith("list is a reserved name; rename this Bot"))
        self.assertTrue(self.service.execute(["list", "rename", "Lister"]).accepted)

    def test_teleport_returns_a_job_that_ends_on_the_next_acknowledged_teleport(self):
        self.service.execute(["Alice", "spawn"])
        self.control.bots["Alice"]["inputs"] = {"teleports": 4, "actions": {}}
        self.world.teleport = lambda identity, parameters, sender: "Overworld 1.00 64.00 2.00"
        result = self.service.execute(["Alice", "tp", "1", "64", "2"])
        self.assertEqual(result.data["job"], {"kind": "tp", "id": 5})
        # A runtime that does not count teleports gives no job rather than one that never ends.
        del self.control.bots["Alice"]["inputs"]
        self.assertIsNone(self.service.execute(["Alice", "tp", "1", "64", "2"]).data)

    def test_outcomes_carry_a_code_and_queries_carry_data(self):
        self.assertEqual(self.service.execute(["Alice", "status"]).code, "not_found")
        self.assertEqual(self.service.execute(["Alice", "move", "sideways"]).code, "invalid_argument")
        self.assertEqual(self.service.execute(["list"]).data, {"bots": []})
        spawned = self.service.execute(["Alice", "spawn"])
        self.assertEqual((spawned.accepted, spawned.code), (True, "ok"))
        self.assertEqual(self.service.execute(["list"]).data["bots"][0]["name"], "Alice")
        radius = self.service.execute(["Alice", "radius"])
        self.assertEqual(radius.data, {"name": "Alice", "chunkRadius": 5, "source": "default",
                                       "serverChunkRadius": None})
        self.assertEqual(self.service.execute(["Alice", "radius", "8"]).data["source"], "bot")

    def test_error_codes_follow_what_the_user_should_do_next(self):
        from endbot_core.commands import CommandSyntaxError, error_code
        from endbot_core.i18n import LocalizedError

        for error, code in (
            (CommandSyntaxError("Expected x y z coordinates"), "invalid_argument"),
            (LocalizedError("No matching entity within 16 blocks"), "invalid_argument"),
            (RuntimeControlError("not_found", "missing"), "not_found"),
            (RuntimeControlError("name_conflict", "taken"), "conflict"),
            (RuntimeControlError("not_online", "offline"), "conflict"),
            (RuntimeControlError("invalid_name", "bad"), "invalid_argument"),
            (RuntimeControlError("unavailable", "down"), "unavailable"),
            # The runtime rejecting the plugin's token is an installation fault, not the user's authority.
            (RuntimeControlError("unauthorized", "token"), "unavailable"),
            (RuntimeControlError("something_new", "?"), "unavailable"),
            (RuntimeError("runtime control is not configured"), "unavailable"),
        ):
            with self.subTest(error=error):
                self.assertEqual(error_code(error), code)

    def test_operation_entry_runs_like_the_equivalent_command(self):
        from endbot_core.operations import Operation

        self.service.execute(["Alice", "spawn"])
        self.control.calls.clear()
        typed = self.service.execute_operation(Operation("move", "Alice", {"direction": "forward", "ticks": 20}))
        calls = list(self.control.calls)
        self.control.calls.clear()
        text = self.service.execute(["Alice", "move", "forward", "for", "20"])
        self.assertEqual(typed, text)
        self.assertEqual(calls, self.control.calls)
        # A well-formed operation that fails still comes back as a result.
        missing = self.service.execute_operation(Operation("status", "Nobody"))
        self.assertFalse(missing.accepted)

    def test_operation_entry_rejects_malformed_operations_before_anything_runs(self):
        from endbot_core.commands import CommandSyntaxError
        from endbot_core.operations import Operation

        for operation in (Operation("fly", "Alice"), Operation("move", "Alice", {"direction": "up"}),
                          Operation("move", "Alice", {"direction": "forward", "speed": 2}),
                          Operation("move", "Alice", {"direction": "stop", "ticks": 20})):
            with self.subTest(operation=operation), self.assertRaises((ValueError, CommandSyntaxError)):
                self.service.execute_operation(operation)
        self.assertEqual(self.control.calls, [])

    def test_continuous_mining_retargets_then_waits_without_restarting_active_block(self):
        self.service.execute(["Alice", "spawn"])
        source = object()
        reply = self.service.execute(["Alice", "destroy", "continuous"], source)
        self.assertIn("destroy continuous started", reply.message)
        loop_id = self.control.bots["Alice"]["mining"]["loopId"]
        self.service.poll_mining(lambda sender: sender is source)
        self.assertEqual(self.control.calls[-1][1]["blockPosition"], [1, 64, 2])
        self.assertEqual(self.control.calls[-1][1]["loopId"], loop_id)
        self.control.bots["Alice"]["mining"]["state"] = "active"
        self.world.resolve_mining = lambda *args: self.fail("active blocks must not be restarted")
        self.service.poll_mining(lambda _: True)
        self.assertEqual(self.control.calls[-1], ("mine", {"name": "Alice", "mode": "continue", "loopId": loop_id}))
        self.control.bots["Alice"]["mining"]["state"] = "waiting"
        self.world.resolve_mining = lambda *args: None
        self.service.poll_mining(lambda _: True)
        self.assertEqual(self.control.calls[-1][1]["mode"], "continue")
        status = self.service.execute(["Alice", "status"]).message
        self.assertIn("destroy continuous waiting for a block in sight", status)
        self.service.execute(["Alice", "destroy", "stop"])
        before = len(self.control.calls)
        self.service.poll_mining(lambda _: True)
        self.assertEqual(len(self.control.calls), before)

    def test_continuous_mining_keeps_waiting_when_runtime_rejects_a_target(self):
        self.service.execute(["Alice", "spawn"])
        self.service.execute(["Alice", "destroy", "continuous"])
        loop_id = self.control.bots["Alice"]["mining"]["loopId"]
        request = self.control.request

        def reject_targets(operation, **parameters):
            if operation == "mine" and "blockPosition" in parameters:
                self.control.calls.append((operation, parameters))
                raise RuntimeControlError("invalid_request", "Target block is out of reach")
            return request(operation, **parameters)

        self.control.request = reject_targets
        self.service.poll_mining(lambda _: True)
        self.assertEqual(self.control.calls[-1], ("mine", {"name": "Alice", "mode": "continue", "loopId": loop_id}))
        self.service.poll_mining(lambda _: True)
        self.assertNotIn("stop", [call[1].get("mode") for call in self.control.calls if call[0] == "mine"])
        # A briefly unreachable runtime (e.g. Windows WinError 10048) is retried;
        # one that stays unreachable for about a lease ends the loop.
        failures = {"left": 3}

        def flaky(operation, **parameters):
            if failures["left"]:
                failures["left"] -= 1
                raise RuntimeControlError("unavailable", "runtime unavailable: [WinError 10048]")
            return reject_targets(operation, **parameters)

        self.control.request = flaky
        for _ in range(4):
            self.assertEqual(self.service.poll_mining(lambda _: True), [])
        self.assertNotIn("stop", [call[1].get("mode") for call in self.control.calls if call[0] == "mine"])

        def unavailable(operation, **parameters):
            self.control.calls.append((operation, parameters))
            if operation == "mine" and parameters.get("mode") == "stop":
                return {}
            raise RuntimeControlError("unavailable", "runtime unavailable")

        self.control.request = unavailable
        for _ in range(UNREACHABLE_POLL_LIMIT - 1):
            self.assertEqual(self.service.poll_mining(lambda _: True), [])
        notices = self.service.poll_mining(lambda _: True)
        self.assertIn("runtime unavailable", notices[0][2])
        self.assertEqual(self.control.calls[-1], ("mine", {"name": "Alice", "mode": "stop", "loopId": loop_id}))

    def test_typed_loop_tells_once_when_nothing_is_in_sight(self):
        self.service.execute(["Alice", "spawn"])
        source = object()
        self.service.execute(["Alice", "destroy", "oak_log", "count", "5"], source)
        seen = {"cell": None}

        def reachable_block(identity, block_type, reach):
            if seen["cell"] is None:
                raise ValueError("No oak_log in sight within 5 blocks")
            return seen["cell"]

        self.world.reachable_block = reachable_block
        for _ in range(WAITING_NOTICE_POLLS - 1):
            self.assertEqual(self.service.poll_mining(lambda _: True), [])
        notices = self.service.poll_mining(lambda _: True)
        waiting = "Endbot: Alice: destroy is waiting: no oak_log it can break within 5 blocks; move the Bot closer"
        self.assertEqual(notices, [(source, "info", waiting)])
        for _ in range(WAITING_NOTICE_POLLS):
            self.assertEqual(self.service.poll_mining(lambda _: True), [])
        # A target ends the wait; the next empty stretch is reported again.
        seen["cell"] = [1, 64, 2]
        self.assertEqual(self.service.poll_mining(lambda _: True), [])
        seen["cell"] = None
        notices = [notice for _ in range(WAITING_NOTICE_POLLS) for notice in self.service.poll_mining(lambda _: True)]
        self.assertEqual(len(notices), 1)

    def test_continuous_retarget_does_not_need_the_controller(self):
        self.service.execute(["Alice", "spawn"])
        self.service.execute(["Alice", "destroy", "continuous"], object())
        senders = []
        resolve = self.world.resolve_mining

        def record(identity, parameters, sender):
            senders.append(sender)
            return resolve(identity, parameters, sender)

        self.world.resolve_mining = record
        self.assertEqual(self.service.poll_mining(lambda _: True), [])
        self.assertEqual(senders, [None])

    def test_mining_notifies_only_abnormal_ends(self):
        self.service.execute(["Alice", "spawn"])
        source = object()
        # Single block: success is silent, a failure is reported once.
        self.service.execute(["Alice", "destroy"], source)
        stone = {"blockPosition": [1, 64, 2], "blockName": "minecraft:stone"}
        self.control.bots["Alice"]["mining"] = {**stone, "state": "active"}
        self.assertEqual(self.service.poll_mining(lambda _: True), [])
        self.control.bots["Alice"]["mining"] = {**stone, "state": "completed", "reason": "BDS changed target block"}
        self.assertEqual(self.service.poll_mining(lambda _: True), [])
        self.service.execute(["Alice", "destroy"], source)
        self.control.bots["Alice"]["mining"] = {**stone, "state": "failed", "reason": "target moved out of reach"}
        notices = self.service.poll_mining(lambda _: True)
        self.assertEqual(notices, [(source, "warning", "Endbot: Alice: destroy stopped: target out of reach")])
        self.assertEqual(self.service.poll_mining(lambda _: True), [])
        # Continuous: a commanded stop is silent, death is reported.
        reply = self.service.execute(["Alice", "destroy", "continuous"], source)
        self.assertEqual(reply.log_level, "info")
        self.control.bots["Alice"]["mining"] = {"state": "canceled", "reason": "stopped"}
        self.assertEqual(self.service.poll_mining(lambda _: True), [])
        self.service.execute(["Alice", "destroy", "continuous"], source)
        self.control.bots["Alice"]["mining"] = {"state": "canceled", "reason": "respawning"}
        notices = self.service.poll_mining(lambda _: True)
        self.assertEqual(notices, [(source, "warning", "Endbot: Alice: destroy continuous stopped: canceled by death")])
        # Revocation ends the loop and says why.
        self.service.execute(["Alice", "destroy", "continuous"], source)
        notices = self.service.poll_mining(lambda _: False)
        self.assertIn("control permission revoked", notices[0][2])

    def test_continuous_mining_stops_on_revocation_and_does_not_adopt_new_session(self):
        self.service.execute(["Alice", "spawn"])
        self.service.execute(["Alice", "destroy", "continuous"])
        loop_id = self.control.bots["Alice"]["mining"]["loopId"]
        self.service.poll_mining(lambda _: False)
        self.assertEqual(self.control.calls[-1], ("mine", {"name": "Alice", "mode": "stop", "loopId": loop_id}))
        self.service.execute(["Alice", "destroy", "continuous"])
        self.control.bots["Alice"]["mining"]["loopId"] = "replacement-session"
        self.service.poll_mining(lambda _: True)
        self.assertEqual(self.control.calls[-1][0], "status")
        self.service.close()
        self.assertEqual(self.control.calls[-1][0], "status")

    def test_continuous_mining_close_and_world_failure_stop_only_owned_loop(self):
        self.service.execute(["Alice", "spawn"])
        for shutdown in (False, True):
            self.service.execute(["Alice", "destroy", "continuous"])
            if shutdown:
                self.service.close()
            else:
                def fail(*args):
                    raise ValueError("source is offline")
                self.world.resolve_mining = fail
                self.service.poll_mining(lambda _: True)
            self.assertEqual(self.control.calls[-1][1]["mode"], "stop")

    def test_spawn_queues_world_placement_after_profile_creation(self) -> None:
        result = self.service.execute(["Alice", "spawn"], object())
        self.assertIn("created and connecting", result.message)
        self.assertEqual([call[0] for call in self.control.calls], ["status", "spawn"])
        self.assertEqual(self.world.placements[0][1]["coordinates"], [1, 2, 3])

    def test_radius_reports_requested_source_and_server_confirmation(self) -> None:
        self.service.execute(["Alice", "spawn"], object())
        updated = self.service.execute(["Alice", "radius", "12"])
        self.assertEqual(self.control.calls[-1], ("radius", {"name": "Alice", "value": 12}))
        # Not yet online: the request is stored and sent on connection.
        self.assertIn("Alice: chunk radius 12 (Bot override); applies when online", updated.message)
        self.control.bots["Alice"]["connectionState"] = "online"
        pending = self.service.execute(["Alice", "radius"])
        self.assertIn("server confirmation pending", pending.message)
        self.control.bots["Alice"]["serverChunkRadius"] = 10
        query = self.service.execute(["Alice", "radius"])
        self.assertIn("chunk radius 12 (Bot override); server confirmed 10", query.message)
        reset = self.service.execute(["Alice", "radius", "default"])
        self.assertIn("chunk radius 5 (shared default)", reset.message)

    def test_mining_starts_authoritative_target_and_stop_skips_world_resolution(self):
        self.service.execute(["Alice", "spawn"], object())
        reply = self.service.execute(["Alice", "destroy", "1", "64", "2"], object())
        self.assertIn("Alice: destroying minecraft:stone at 1 64 2; see status for the result", reply.message)
        self.assertEqual(self.control.calls[-1][0], "mine")
        self.assertEqual(self.control.calls[-1][1]["blockRuntimeId"], 42)
        self.world.resolve_mining = lambda *args: self.fail("stop must not resolve a new target")
        stopped = self.service.execute(["Alice", "destroy", "stop"], object())
        self.assertIn("destroy stopped", stopped.message)
        self.assertEqual(self.control.calls[-1], ("mine", {"name": "Alice", "mode": "stop"}))

    def test_console_spawn_without_coordinates_leaves_placement_to_bds(self) -> None:
        console = type("Console", (), {"is_console": True})()
        result = self.service.execute(["Alice", "spawn"], console)
        self.assertIn("created and connecting (at the world spawn)", result.message)
        self.assertEqual(self.world.placements, [])
        identity_id = self.control.bots["Alice"]["identityId"]
        self.assertIn(identity_id, self.world.cleared_placements)

        # A known offline profile: the fake runtime reports it as not created.
        self.control.request = lambda operation, **parameters: (
            {"identityId": identity_id, "name": "Alice", "connectionState": "connecting", "created": False}
            if operation in {"status", "spawn"}
            else {}
        )
        result = self.service.execute(["Alice", "spawn"], console)
        self.assertIn("connecting (where it last was)", result.message)
        self.assertEqual(self.world.placements, [])

    def test_console_spawn_at_coordinates_still_places_the_bot(self) -> None:
        console = type("Console", (), {"is_console": True})()
        self.service.execute(["Alice", "spawn", "at", "5", "70", "5", "in", "overworld"], console)
        self.assertEqual(self.world.placements[0][1]["coordinates"], [5.0, 70.0, 5.0])

    def test_commands_target_independent_names(self) -> None:
        self.service.execute(["Alice", "spawn"], object())
        self.service.execute(["Bob", "spawn"], object())
        self.service.execute(["Alice", "move", "forward"], object())
        self.assertEqual(self.control.calls[-1], ("move", {"name": "Alice", "direction": "forward"}))

    def test_rename_checks_online_player_before_runtime_update(self) -> None:
        self.service.execute(["Alice", "spawn"], object())
        self.world.collisions = {"Steve": "human-uuid"}
        result = self.service.execute(["Alice", "rename", "Steve"], object())
        self.assertIn("collision", result.message)
        self.assertNotIn("rename", [call[0] for call in self.control.calls])

    def test_case_only_rename_allows_the_same_online_bot_identity(self) -> None:
        self.service.execute(["Alice", "spawn"], object())
        identity_id = self.control.bots["Alice"]["identityId"]
        self.world.collisions = {"ALICE": identity_id}

        result = self.service.execute(["Alice", "rename", "ALICE"], object())

        self.assertIn("ALICE", result.message)
        self.assertEqual(self.control.calls[-1][0], "rename")
        self.assertEqual(self.world.cleared_placements, [identity_id])

    def test_non_spawn_lifecycle_intents_clear_abandoned_placement_first(self) -> None:
        operations = ("resume", "reconnect", "despawn", "forget")
        for operation in operations:
            with self.subTest(operation=operation):
                self.setUp()
                self.service.execute(["Alice", "spawn"], object())
                identity_id = self.control.bots["Alice"]["identityId"]
                self.control.calls.clear()

                self.service.execute(["Alice", operation], object())

                self.assertEqual([call[0] for call in self.control.calls], ["status", operation])
                self.assertEqual(self.world.cleared_placements, [identity_id])
                self.assertEqual(self.world.placements, [])

    def test_first_spawn_rejects_active_human_before_profile_creation(self) -> None:
        self.world.collisions = {"Steve": "human-uuid"}
        result = self.service.execute(["Steve", "spawn"], object())
        self.assertIn("collision", result.message)
        self.assertNotIn("spawn", [call[0] for call in self.control.calls])

    def test_existing_bot_may_reuse_its_own_online_name(self) -> None:
        self.service.execute(["Alice", "spawn"], object())
        identity_id = self.control.bots["Alice"]["identityId"]
        self.control.bots["Alice"] = {**self.control.bots["Alice"], "connectionState": "online"}
        self.world.collisions = {"Alice": identity_id}
        result = self.service.execute(["Alice", "spawn"], object())
        self.assertIn("already online", result.message)

    def test_spawn_reports_connecting_instead_of_online(self) -> None:
        self.service.execute(["Alice", "spawn"], object())
        self.control.bots["Alice"] = {
            **self.control.bots["Alice"],
            "connectionState": "connecting",
        }
        result = self.service.execute(["Alice", "spawn"], object())
        self.assertIn("already connecting", result.message)
        self.assertNotIn("already online", result.message)

    def test_spawn_reports_reconnecting_instead_of_online(self) -> None:
        self.service.execute(["Alice", "spawn"], object())
        self.control.bots["Alice"] = {
            **self.control.bots["Alice"],
            "connectionState": "reconnecting",
        }
        result = self.service.execute(["Alice", "spawn"], object())
        self.assertIn("already reconnecting", result.message)
        self.assertNotIn("already online", result.message)

    def test_spawn_without_connection_state_stays_neutral(self) -> None:
        # Older runtimes omit connectionState; stay neutral rather than claiming online.
        original = self.control.request

        def without_state(operation, **parameters):
            result = original(operation, **parameters)
            result.pop("connectionState", None)
            return result

        self.service.execute(["Alice", "spawn"], object())
        self.control.request = without_state
        result = self.service.execute(["Alice", "spawn"], object())
        self.assertIn("already active", result.message)
        self.assertNotIn("already online", result.message)

    def test_invalid_console_placement_has_no_lifecycle_side_effect(self) -> None:
        result = self.service.execute(["Alice", "spawn", "at", "1", "64", "2"], None)
        self.assertIn("dimension is required", result.message)
        self.assertNotIn("spawn", [call[0] for call in self.control.calls])

    def test_invalid_dimension_has_no_lifecycle_side_effect(self) -> None:
        self.world.invalid_dimensions.add("missing:dimension")
        result = self.service.execute(
            ["Alice", "spawn", "at", "1", "64", "2", "in", "missing:dimension"],
            object(),
        )
        self.assertIn("Unknown dimension", result.message)
        self.assertNotIn("spawn", [call[0] for call in self.control.calls])

    def test_look_at_resolves_bot_identity_before_facing_coordinates(self) -> None:
        self.service.execute(["Alice", "spawn"], object())
        identity_id = self.control.bots["Alice"]["identityId"]
        self.control.calls.clear()

        result = self.service.execute(["Alice", "look", "at", "1", "64", "2"], object())

        self.assertEqual(self.world.look_targets, [(identity_id, [1.0, 64.0, 2.0])])
        self.assertEqual(
            [call[0] for call in self.control.calls],
            ["status", "look"],
        )
        self.assertEqual(self.control.calls[-1], ("look", {"name": "Alice", "yaw": 90.0, "pitch": -10.0}))
        self.assertIn("look set to yaw=90.0 pitch=-10.0", result.message)

    def test_player_primitives_route_authoritative_parameters(self) -> None:
        self.service.execute(["Alice", "spawn"], object())

        selected = self.service.execute(["Alice", "hotbar", "3"], object())
        self.assertEqual(self.control.calls[-1], ("hotbar", {"name": "Alice", "slot": 3}))
        self.assertIn("slot", selected.message)

        self.service.execute(["Alice", "interact", "1", "64", "2", "up"], object())
        self.assertEqual(
            self.control.calls[-1],
            (
                "interact",
                {"name": "Alice", "blockPosition": [1, 64, 2], "blockRuntimeId": 42, "face": 1},
            ),
        )

        self.service.execute(["Alice", "drop", "stack"], object())
        self.assertEqual(self.control.calls[-1], ("drop", {"name": "Alice", "stack": True}))

    def test_action_replies_identify_the_operation_and_parameters(self):
        self.service.execute(["Alice", "spawn"], object())
        cases = [
            (["move", "forward"], "movement set to forward"),
            (["move", "stop"], "movement stopped"),
            (["jump"], "jump once requested"),
            (["attack", "continuous"], "attack continuous started"),
            (["use", "interval", "20"], "use started every 20 ticks"),
            (["use", "stop"], "use stopped"),
            (["sprint", "on"], "sprint on"),
            (["sneak", "off"], "sneak off"),
            (["stop"], "movement and actions stopped"),
            (["interact", "1", "64", "2", "up"], "block interaction requested at 1 64 2 face=up"),
        ]
        for args, expected in cases:
            with self.subTest(args=args):
                reply = self.service.execute(["Alice", *args], object())
                self.assertIn("Alice", reply.message)
                self.assertIn(expected, reply.message)
                self.assertNotEqual(reply.message, "Endbot: Alice online")

    def test_duration_and_hold_replies_and_runtime_parameters(self):
        self.service.execute(["Alice", "spawn"], object())
        cases = [
            (["move", "forward", "for", "40"], "movement set to forward for 40 ticks",
             ("move", {"direction": "forward", "durationTicks": 40})),
            (["jump", "for", "20"], "jump continuous started for 20 ticks",
             ("action", {"action": "jump", "mode": "continuous", "durationTicks": 20})),
            (["attack", "interval", "20", "for", "100"], "attack started every 20 ticks for 100 ticks",
             ("action", {"action": "attack", "mode": "interval", "intervalTicks": 20, "durationTicks": 100})),
            (["use", "for", "40"], "use started for 40 ticks, then releases",
             ("action", {"action": "use", "mode": "continuous", "durationTicks": 40})),
            (["use", "hold", "30"], "use once requested: hold 30 ticks, then release",
             ("action", {"action": "use", "mode": "once", "holdTicks": 30})),
            (["use", "hold", "30", "for", "200"], "use repeating: hold 30 ticks, then release for 200 ticks",
             ("action", {"action": "use", "mode": "continuous", "holdTicks": 30, "durationTicks": 200})),
            (["use", "hold", "30", "interval", "40", "for", "200"],
             "use started every 40 ticks: hold 30 ticks, then release for 200 ticks",
             ("action", {"action": "use", "mode": "interval", "holdTicks": 30, "intervalTicks": 40,
                         "durationTicks": 200})),
            (["destroy", "for", "200"], "destroy continuous started for 200 ticks; destroy stop to cancel",
             ("mine", {"mode": "start", "durationTicks": 200})),
            (["destroy", "count", "64"], "destroy continuous started until 64 blocks; destroy stop to cancel",
             ("mine", {"mode": "start", "blockCount": 64})),
            (["destroy", "for", "200", "count", "64"],
             "destroy continuous started for 200 ticks until 64 blocks; destroy stop to cancel",
             ("mine", {"mode": "start", "durationTicks": 200, "blockCount": 64})),
        ]
        for args, expected, (operation, parameters) in cases:
            with self.subTest(args=args):
                reply = self.service.execute(["Alice", *args], object())
                self.assertIn(f"Alice: {expected}", reply.message)
                self.assertEqual(self.control.calls[-1], (operation, {"name": "Alice", **parameters}))

    def test_destroy_duration_end_is_an_info_notice_and_status_shows_time_left(self):
        self.service.execute(["Alice", "spawn"])
        source = object()
        self.service.execute(["Alice", "destroy", "for", "200"], source)
        self.control.bots["Alice"]["mining"] = {
            "state": "waiting", "continuous": True, "loopId": self.control.bots["Alice"]["mining"]["loopId"],
            "remainingTicks": 120,
        }
        self.world.resolve_mining = lambda *args: None
        self.assertEqual(self.service.poll_mining(lambda _: True), [])
        status = self.service.execute(["Alice", "status"]).message
        self.assertIn("destroy continuous waiting for a block in sight (120 ticks left)", status)
        stone = {"blockPosition": [1, 64, 2], "blockName": "minecraft:stone"}
        self.control.bots["Alice"]["mining"] = {**stone, "state": "completed", "reason": "duration elapsed"}
        notices = self.service.poll_mining(lambda _: True)
        self.assertEqual(notices, [(source, "info", "Endbot: Alice: destroy continuous stopped: duration elapsed")])
        self.assertIn("last destroy: duration elapsed", self.service.execute(["Alice", "status"]).message)

    def test_destroy_count_shows_blocks_and_ends_as_an_info_notice(self):
        self.service.execute(["Alice", "spawn"])
        source = object()
        self.service.execute(["Alice", "destroy", "count", "64"], source)
        loop_id = self.control.bots["Alice"]["mining"]["loopId"]
        self.world.resolve_mining = lambda *args: None
        self.control.bots["Alice"]["mining"] = {
            "state": "waiting", "continuous": True, "loopId": loop_id, "blocksBroken": 12, "blockCount": 64,
        }
        self.assertEqual(self.service.poll_mining(lambda _: True), [])
        status = self.service.execute(["Alice", "status"]).message
        self.assertIn("destroy continuous waiting for a block in sight (12/64 blocks)", status)
        stone = {"blockPosition": [1, 64, 2], "blockName": "minecraft:stone"}
        self.control.bots["Alice"]["mining"] = {
            **stone, "state": "active", "continuous": True, "loopId": loop_id, "blocksBroken": 12, "blockCount": 64,
        }
        status = self.service.execute(["Alice", "status"]).message
        self.assertIn("destroying minecraft:stone at 1 64 2 (continuous, 12/64 blocks)", status)
        self.control.bots["Alice"]["mining"] = {
            **stone, "state": "completed", "reason": "block count reached", "blocksBroken": 64, "blockCount": 64,
        }
        notices = self.service.poll_mining(lambda _: True)
        self.assertEqual(
            notices, [(source, "info", "Endbot: Alice: destroy continuous stopped: block count reached")]
        )
        self.assertIn(
            "last destroy: block count reached (64/64 blocks, minecraft:stone at 1 64 2)",
            self.service.execute(["Alice", "status"]).message,
        )

    def test_destroy_loop_status_shows_broken_blocks_without_a_limit(self):
        self.service.execute(["Alice", "spawn"])
        self.service.execute(["Alice", "destroy", "continuous"])
        loop_id = self.control.bots["Alice"]["mining"]["loopId"]
        self.world.resolve_mining = lambda *args: None
        self.control.bots["Alice"]["mining"] = {
            "state": "waiting", "continuous": True, "loopId": loop_id, "blocksBroken": 3,
        }
        self.service.poll_mining(lambda _: True)
        status = self.service.execute(["Alice", "status"]).message
        self.assertIn("destroy continuous waiting for a block in sight (3 blocks)", status)

    def test_status_lists_inputs_that_end_on_their_own(self):
        self.service.execute(["Alice", "spawn"])
        self.control.bots["Alice"]["inputs"] = {
            "movement": "forward", "movementRemainingTicks": 30, "actions": {
                "attack": {"mode": "continuous", "intervalTicks": 1},
                "jump": {"mode": "interval", "intervalTicks": 20, "remainingTicks": 80},
                "use": {"mode": "interval", "intervalTicks": 40, "holdTicks": 30},
            },
        }
        status = self.service.execute(["Alice", "status"]).message
        self.assertIn(
            "timed: move forward 30 ticks left, jump interval 20 80 ticks left, use interval 40 hold 30", status
        )
        self.assertNotIn("attack", status)
        self.control.bots["Alice"]["inputs"] = {"movement": None, "actions": {}}
        self.assertNotIn("timed:", self.service.execute(["Alice", "status"]).message)

    def test_failed_action_identifies_bot_and_operation(self):
        def unavailable(operation, **parameters):
            raise RuntimeControlError("not_online", "Bot is offline")

        self.control.request = unavailable
        result = self.service.execute(["Alice", "move", "forward"], object())
        self.assertEqual(result.message, "Endbot: Alice: move failed: Bot is offline")

    def test_lifecycle_replies_identify_change_instead_of_only_state(self):
        self.service.execute(["Alice", "spawn"], object())
        self.assertIn("Alice: renamed to Bob", self.service.execute(["Alice", "rename", "Bob"], object()).message)
        for operation in ("resume", "reconnect", "despawn"):
            self.assertIn(f"{operation} requested", self.service.execute(["Bob", operation], object()).message)
        self.assertIn("profile registration removed", self.service.execute(["Bob", "forget"], object()).message)

    def test_mining_status_uses_player_facing_wording(self):
        stone = {"blockPosition": [0, 64, 2], "blockName": "minecraft:stone"}
        cases = [
            ({**stone, "state": "active", "reason": "waiting for BDS"}, "destroying minecraft:stone at 0 64 2"),
            (
                {**stone, "state": "waiting", "continuous": True, "loopId": "x"},
                "destroy continuous waiting for a block in sight (last: minecraft:stone at 0 64 2)",
            ),
            (
                {**stone, "state": "completed", "reason": "BDS changed target block"},
                "last destroy: block broken or changed (minecraft:stone at 0 64 2)",
            ),
            (
                {**stone, "state": "failed", "reason": "timed out waiting for BDS to change target"},
                "last destroy: failed: block did not break within 10 minutes",
            ),
            ({**stone, "state": "canceled", "reason": "look changed"}, "last destroy: canceled by look"),
            ({**stone, "state": "canceled", "reason": "connection closed"}, "last destroy: canceled: Bot disconnected"),
        ]
        self.service.execute(["Alice", "spawn"], object())
        for mining, expected in cases:
            with self.subTest(mining=mining):
                self.control.bots["Alice"]["mining"] = mining
                message = self.service.execute(["Alice", "status"], object()).message
                self.assertIn(expected, message)
                self.assertNotIn("BDS", message)

    def test_bot_replies_share_one_prefix_including_syntax_errors(self):
        self.service.execute(["Alice", "spawn"], object())
        for args in (
            ["spawn"],
            ["hotbar"],
            ["hotbar", "2"],
            ["drop"],
            ["radius"],
            ["destroy"],
            ["destroy", "1", "64"],
            ["move", "sideways"],
            ["status"],
        ):
            with self.subTest(args=args):
                self.assertTrue(
                    self.service.execute(["Alice", *args], object()).message.startswith("Endbot: Alice: ")
                )
        renamed = self.service.execute(["Alice", "rename", "Bob"], object()).message
        self.assertTrue(renamed.startswith("Endbot: Alice: "))
        self.assertEqual(self.service.execute(["help", "nope"]).message.split(":")[0], "Endbot")


class OwnershipRecordingTests(unittest.TestCase):
    """A player's new Bot is owned by the invoker; the console records nothing (BOT-024 4a)."""

    def setUp(self) -> None:
        self.control = FakeControl()
        self.world = FakeWorld()
        self.ownership = OwnershipStore()
        self.service = PlainCommandService(self.control, self.world, ownership=self.ownership)

    @staticmethod
    def player(name="Alice", xuid="2535400000000001", player_id="0000000a-0000-4000-8000-00000000000b"):
        return Principal("player", name=name, xuid=xuid, player_id=player_id)

    def test_player_spawn_records_the_owner_with_the_kind_default(self):
        result = self.service.execute(["Alice", "spawn"], self.player())
        self.assertIn("created and connecting", result.message)
        identity = self.control.bots["Alice"]["identityId"]
        self.assertEqual(self.ownership.get("bots", identity),
                         Ownership("xuid:2535400000000001", "Alice", "shared"))

    def test_spawn_uses_a_private_default_and_records_a_uuid_only_invoker(self):
        self.ownership = OwnershipStore(bots_default="private")
        self.service = PlainCommandService(self.control, self.world, ownership=self.ownership)
        self.service.execute(["Alice", "spawn"], self.player(xuid=None))
        identity = self.control.bots["Alice"]["identityId"]
        self.assertEqual(self.ownership.get("bots", identity),
                         Ownership("player:0000000a-0000-4000-8000-00000000000b", "Alice", "private"))

    def test_existing_profile_spawn_records_nothing_new(self):
        identity = "00000000-0000-4000-8000-000000000099"
        self.control.bots["Alice"] = {
            "identityId": identity, "name": "Alice", "desiredState": "online", "connectionState": "online",
        }
        result = self.service.execute(["Alice", "spawn"], self.player("Bob", xuid="2535400000000002"))
        self.assertIn("already online", result.message)
        self.assertIsNone(self.ownership.get("bots", identity))

    def test_console_spawn_records_nothing(self):
        self.service.execute(["Alice", "spawn"], Principal("console"))
        identity = self.control.bots["Alice"]["identityId"]
        self.assertIsNone(self.ownership.get("bots", identity))

    def test_forget_removes_the_recorded_entry(self):
        self.service.execute(["Alice", "spawn"], self.player())
        identity = self.control.bots["Alice"]["identityId"]
        self.assertIsNotNone(self.ownership.get("bots", identity))
        self.service.execute(["Alice", "forget"], self.player())
        self.assertIsNone(self.ownership.get("bots", identity))


class OwnershipEnforcementTests(unittest.TestCase):
    """Someone else's private Bot is refused alike from every entry; S12 owner-only actions (BOT-024 4b)."""

    def setUp(self) -> None:
        self.control = FakeControl()
        self.world = FakeWorld()
        self.ownership = OwnershipStore()
        self.service = PlainCommandService(self.control, self.world, ownership=self.ownership)
        self.alice = Principal("player", name="Alice", xuid="2535400000000001",
                               player_id="0000000a-0000-4000-8000-00000000000a")
        self.bob = Principal("player", name="Bob", xuid="2535400000000002",
                             player_id="0000000b-0000-4000-8000-00000000000b")
        self.service.execute(["Mine", "spawn"], self.alice)
        self.identity = self.control.bots["Mine"]["identityId"]

    def test_someone_elses_private_bot_is_listed_but_refused(self):
        self.ownership.set_visibility("bots", self.identity, "private")
        for argv in (["Mine", "status"], ["Mine", "move", "forward"], ["Mine", "spawn"], ["Mine", "menu"],
                     ["Mine", "forget"]):
            with self.subTest(argv=argv):
                result = self.service.execute(argv, self.bob)
                self.assertFalse(result.accepted)
                self.assertEqual(result.code, "unauthorized")
                self.assertIn("Bot Mine is private to Alice", result.message)
        # The typed operation entry (forms, scripts and the web) fails the same way.
        typed = self.service.execute_operation(Operation("status", "Mine", {}), self.bob)
        self.assertEqual((typed.accepted, typed.code), (False, "unauthorized"))
        self.assertIn("Mine", self.service.execute(["list"], self.bob).message)
        self.assertTrue(self.service.execute(["Mine", "status"], self.alice).accepted)
        self.assertTrue(self.service.execute(["Mine", "status"], Principal("console")).accepted)

    def test_someone_elses_shared_bot_is_operated_but_forget_and_rename_stay_with_the_owner(self):
        self.assertTrue(self.service.execute(["Mine", "move", "forward"], self.bob).accepted)
        for argv in (["Mine", "forget"], ["Mine", "rename", "Theirs"]):
            with self.subTest(argv=argv):
                result = self.service.execute(argv, self.bob)
                self.assertEqual((result.accepted, result.code), (False, "unauthorized"))
                self.assertIn("Only Alice or the console can", result.message)
        self.assertIn("Mine", self.control.bots)
        self.assertTrue(self.service.execute(["Mine", "rename", "Ours"], self.alice).accepted)
        self.assertTrue(self.service.execute(["Ours", "forget"], Principal("console")).accepted)

    def test_a_loop_on_a_bot_that_becomes_private_ends(self):
        reply = self.service.execute(["Mine", "destroy", "continuous"], self.bob)
        self.assertIn("destroy continuous started", reply.message)
        self.ownership.set_visibility("bots", self.identity, "private")
        self.control.calls.clear()
        notices = self.service.poll_mining(lambda _: True)
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0][2], "Endbot: Mine: destroy continuous stopped: the Bot became private")
        self.assertEqual(self.service._mining_loops, {})
        self.assertNotIn(("mine", {"name": "Mine", "mode": "continue"}),
                         [(operation, {k: v for k, v in params.items() if k in {"name", "mode"}})
                          for operation, params in self.control.calls])

    def test_unowned_bots_stay_fully_shared(self):
        self.service.execute(["Old", "spawn"], Principal("console"))
        self.assertTrue(self.service.execute(["Old", "rename", "Older"], self.bob).accepted)
        self.assertTrue(self.service.execute(["Older", "forget"], self.bob).accepted)

    def test_without_owners_no_profile_lookup_is_added(self):
        service = PlainCommandService(self.control, self.world, ownership=OwnershipStore())
        self.control.calls.clear()
        service.execute(["Mine", "status"], self.bob)
        self.assertNotIn("profiles", [operation for operation, _ in self.control.calls])


class SeatLimitTests(unittest.TestCase):
    """The [limits] budget: Bots must leave the reserved slots for players."""

    def service(self, bots, *, limits, max_players=10, humans=0):
        control = FakeControl()
        for index, (name, state) in enumerate(bots.items(), start=1):
            control.bots[name] = {
                "identityId": f"00000000-0000-4000-8000-{index:012d}",
                "name": name,
                "connectionState": state,
            }
        seen = []

        def capacity(bot_ids):
            seen.append(set(bot_ids))
            return max_players, humans

        return PlainCommandService(control, FakeWorld(), limits=limits, capacity=capacity), control, seen

    def test_a_bot_that_would_take_the_reserved_slot_is_refused(self):
        service, control, _ = self.service(
            {"Alice": "online", "Bob": "online"},
            limits=BotLimits(max_bots=0, reserved_player_slots=1),
            max_players=3,
        )
        result = service.execute(["Carol", "spawn"], object())
        self.assertIn("No free slot for another Bot", result.message)
        self.assertNotIn("spawn", [call[0] for call in control.calls])

    def test_the_reserve_counts_players_already_online(self):
        service, control, _ = self.service(
            {"Alice": "online"},
            limits=BotLimits(max_bots=0, reserved_player_slots=1),
            max_players=3,
            humans=1,
        )
        result = service.execute(["Bob", "spawn"], object())
        self.assertIn("No free slot for another Bot", result.message)
        self.assertNotIn("spawn", [call[0] for call in control.calls])

    def test_a_free_slot_lets_the_bot_join(self):
        service, control, seen = self.service(
            {"Alice": "online"},
            limits=BotLimits(max_bots=0, reserved_player_slots=1),
            max_players=4,
        )
        result = service.execute(["Bob", "spawn"], object())
        self.assertIn("created and connecting", result.message)
        self.assertIn("spawn", [call[0] for call in control.calls])
        self.assertEqual(seen[-1], {"00000000-0000-4000-8000-000000000001"})

    def test_max_bots_counts_online_and_connecting_bots(self):
        service, control, _ = self.service(
            {"Alice": "online", "Bob": "connecting"},
            limits=BotLimits(max_bots=2, reserved_player_slots=0),
        )
        result = service.execute(["Carol", "spawn"], object())
        self.assertIn("Bot limit reached", result.message)
        self.assertNotIn("spawn", [call[0] for call in control.calls])

    def test_an_already_active_bot_keeps_its_seat(self):
        service, _, _ = self.service(
            {"Alice": "online"},
            limits=BotLimits(max_bots=1, reserved_player_slots=0),
            max_players=1,
        )
        result = service.execute(["Alice", "spawn"], object())
        self.assertIn("already online", result.message)
        self.assertNotIn("Bot limit reached", result.message)

    def test_resume_is_budgeted_but_despawn_is_not(self):
        service, control, _ = self.service(
            {"Alice": "offline", "Bob": "online"},
            limits=BotLimits(max_bots=0, reserved_player_slots=1),
            max_players=2,
        )
        refused = service.execute(["Alice", "resume"], object())
        self.assertIn("No free slot for another Bot", refused.message)
        self.assertNotIn("resume", [call[0] for call in control.calls])
        control.bots["Alice"]["connectionState"] = "online"
        dropped = service.execute(["Alice", "despawn"], object())
        self.assertNotIn("No free slot", dropped.message)

    def test_zero_limits_keep_the_previous_unlimited_behavior(self):
        service, control, _ = self.service(
            {"Alice": "online"},
            limits=BotLimits(max_bots=0, reserved_player_slots=0),
            max_players=1,
        )
        result = service.execute(["Bob", "spawn"], object())
        self.assertIn("created and connecting", result.message)
        self.assertIn("spawn", [call[0] for call in control.calls])


class PerControllerLimitTests(unittest.TestCase):
    """`max-bots-per-controller` counts a controller's owned Bots for a new spawn (BOT-024 5)."""

    def setUp(self) -> None:
        self.control = FakeControl()
        self.world = FakeWorld()
        self.ownership = OwnershipStore()
        self.service = PlainCommandService(
            self.control, self.world, limits=BotLimits(max_bots_per_controller=2), ownership=self.ownership,
        )

    @staticmethod
    def player(name="Alice", xuid="2535400000000001", player_id="0000000a-0000-4000-8000-00000000000b"):
        return Principal("player", name=name, xuid=xuid, player_id=player_id)

    def spawn(self, names, sender):
        return [self.service.execute([name, "spawn"], sender) for name in names]

    def test_the_third_new_bot_is_a_conflict_and_is_not_created(self):
        alice = self.player()
        for result in self.spawn(("Alpha", "Beta"), alice):
            self.assertTrue(result.accepted)
        calls = len(self.control.calls)
        refused = self.service.execute(["Gamma", "spawn"], alice)
        self.assertFalse(refused.accepted)
        self.assertEqual(refused.code, "conflict")
        self.assertIn(
            "You own 2 Bots, the most a controller may own on this server (2); forget one first",
            refused.message,
        )
        self.assertNotIn("Gamma", self.control.bots)
        # Only lookups ran (the existing-profile probe, the ownership check's profile list); nothing was created.
        self.assertNotIn("spawn", [operation for operation, _ in self.control.calls[calls:]])

    def test_another_player_has_their_own_count(self):
        alice, bob = self.player(), self.player("Bob", xuid="2535400000000002",
                                                player_id="0000000c-0000-4000-8000-00000000000d")
        self.spawn(("Alpha", "Beta"), alice)
        self.assertTrue(self.service.execute(["Gamma", "spawn"], bob).accepted)
        self.assertIn("Gamma", self.control.bots)

    def test_the_console_is_never_limited(self):
        self.spawn(("Alpha", "Beta"), self.player())
        for name in ("ConsoleOne", "ConsoleTwo", "ConsoleThree"):
            self.assertTrue(self.service.execute([name, "spawn"], Principal("console")).accepted)

    def test_spawning_an_existing_profile_is_never_limited(self):
        alice = self.player()
        self.spawn(("Alpha", "Beta"), alice)
        again = self.service.execute(["Alpha", "spawn"], alice)
        self.assertTrue(again.accepted)
        self.assertIn("already connecting", again.message)

    def test_forget_frees_a_place(self):
        alice = self.player()
        self.spawn(("Alpha", "Beta"), alice)
        self.assertFalse(self.service.execute(["Gamma", "spawn"], alice).accepted)
        self.service.execute(["Alpha", "forget"], alice)
        self.assertTrue(self.service.execute(["Gamma", "spawn"], alice).accepted)

    def test_zero_means_no_limit(self):
        self.service = PlainCommandService(
            self.control, self.world, limits=BotLimits(max_bots_per_controller=0), ownership=self.ownership,
        )
        for result in self.spawn(("Alpha", "Beta", "Gamma"), self.player()):
            self.assertTrue(result.accepted)

    def test_a_run_counts_the_bots_its_invoker_owns(self):
        alice = self.player()
        self.service.execute(["Alpha", "spawn"], alice)
        run = Principal("run", name="Alice", xuid="2535400000000001",
                        player_id="0000000a-0000-4000-8000-00000000000b", run_id="run-1")
        self.assertTrue(self.service.execute(["Beta", "spawn"], run).accepted)
        # The run-created Bot is owned by the invoker, so both see the same count.
        for sender in (alice, run):
            refused = self.service.execute(["Gamma", "spawn"], sender)
            self.assertEqual((refused.accepted, refused.code), (False, "conflict"))


class ResolverWorld:
    """Raycast stand-in for the per-item resolver tests."""

    def __init__(self):
        self.calls = []

    def reachable_block(self, identity_id, block_type, reach):
        self.calls.append(("reachable_block", identity_id, block_type, reach))
        if identity_id == "no-sight":
            raise LocalizedError("No {block} in sight within {reach} blocks", block=block_type, reach=reach)
        return [1, 64, 2]

    def resolve_mining(self, identity_id, parameters, sender):
        self.calls.append(("resolve_mining", identity_id, parameters, sender))
        if identity_id == "no-block":
            raise ValueError("Target is air/liquid or blocked by another block")
        if identity_id == "boom":
            raise RuntimeError("raycast failed")
        return {"blockPosition": [1, 64, 2], "blockName": "minecraft:stone", "identityId": identity_id}


class ResolveMiningBatchTests(unittest.TestCase):
    def test_resolves_typed_then_untyped_requests_in_order(self):
        world = ResolverWorld()
        results = resolve_mining_batch(world, [
            {"identityId": "typed", "blockType": "minecraft:oak_log"},
            {"identityId": "untyped", "blockType": None},
        ])
        self.assertEqual(results, [
            {"target": {"blockPosition": [1, 64, 2], "blockName": "minecraft:stone",
                        "identityId": "typed", "turn": True}},
            {"target": {"blockPosition": [1, 64, 2], "blockName": "minecraft:stone", "identityId": "untyped"}},
        ])
        self.assertEqual(world.calls, [
            ("reachable_block", "typed", "minecraft:oak_log", 5.0),
            ("resolve_mining", "typed", {"coordinates": [1, 64, 2]}, None),
            ("resolve_mining", "untyped", {"continuous": True}, None),
        ])

    def test_typed_value_errors_wait_instead_of_failing(self):
        results = resolve_mining_batch(ResolverWorld(), [
            {"identityId": "no-sight", "blockType": "minecraft:oak_log"},
            {"identityId": "no-block", "blockType": "minecraft:stone"},
        ])
        self.assertEqual(results, [{"target": None}, {"target": None}])

    def test_one_item_failure_does_not_affect_the_others(self):
        results = resolve_mining_batch(ResolverWorld(), [
            {"identityId": "typed", "blockType": "minecraft:oak_log"},
            {"identityId": "boom", "blockType": None},
            {"identityId": "untyped", "blockType": None},
        ])
        self.assertIn("target", results[0])
        self.assertIn("target", results[2])
        self.assertEqual(results[1]["error"]["code"], "failed")

    def test_error_items_decode_to_their_exception_class(self):
        class World:
            def resolve_mining(self, identity_id, parameters, sender):
                if identity_id == "localized":
                    raise LocalizedError("No {block} in sight", block="stone")
                if identity_id == "invalid":
                    raise ValueError("blocked")
                raise RuntimeError("raycast failed")

        results = resolve_mining_batch(World(), [
            {"identityId": "localized"},
            {"identityId": "invalid"},
            {"identityId": "failed"},
        ])
        localized = error_from_wire(results[0]["error"])
        self.assertIsInstance(localized, LocalizedError)
        self.assertEqual((localized.template, localized.params), ("No {block} in sight", {"block": "stone"}))
        self.assertIsInstance(error_from_wire(results[1]["error"]), ValueError)
        failed = error_from_wire(results[2]["error"])
        self.assertIsInstance(failed, LinkError)
        self.assertEqual((failed.code, str(failed)), ("failed", "raycast failed"))

    def test_malformed_requests_raise_before_any_world_call(self):
        world = ResolverWorld()
        for requests in (
            None,
            {"identityId": "typed", "blockType": None},
            [None],
            [{}],
            [{"identityId": ""}],
            [{"identityId": 3}],
            [{"identityId": "typed", "blockType": 3}],
            [{"identityId": "ok"}, {"identityId": "bad", "blockType": []}],
        ):
            with self.subTest(requests=requests), self.assertRaises(ValueError):
                resolve_mining_batch(world, requests)
        self.assertEqual(world.calls, [])


class MiningBatchPollTests(unittest.TestCase):
    def setUp(self):
        self.control = FakeControl()
        self.world = FakeWorld()
        self.service = PlainCommandService(self.control, self.world)
        names = ("Alice", "Bob", "Carol")
        for name in names:
            self.service.execute([name, "spawn"])
            self.service.execute([name, "destroy", "continuous"])
        self.names = names
        self.loop_ids = {name: self.control.bots[name]["mining"]["loopId"] for name in names}

    def install_batch(self, result_factory):
        calls = []

        def batch(requests):
            calls.append([dict(request) for request in requests])
            return result_factory(requests)

        self.world.resolve_mining_batch = batch
        self.world.resolve_mining = lambda *args: self.fail("poll_mining must not call resolve_mining directly")
        return calls

    def mine_calls(self, mode):
        return [parameters for operation, parameters in self.control.calls
                if operation == "mine" and parameters.get("mode") == mode]

    def test_one_batch_call_retargets_every_looping_bot(self):
        calls = self.install_batch(
            lambda requests: [{"target": {"blockPosition": [1, 64, 2]}} for _ in requests]
        )
        self.assertEqual(self.service.poll_mining(lambda _: True), [])
        self.assertEqual(calls, [[
            {"identityId": self.control.bots[name]["identityId"], "blockType": None} for name in self.names
        ]])
        continues = self.mine_calls("continue")
        self.assertEqual([parameters["loopId"] for parameters in continues],
                         [self.loop_ids[name] for name in self.names])
        self.assertTrue(all(parameters["blockPosition"] == [1, 64, 2] for parameters in continues))

    def test_one_failed_item_ends_only_that_loop(self):
        def results(requests):
            return [
                {"error": error_to_wire(RuntimeError("raycast failed"))}
                if request["identityId"] == self.control.bots["Alice"]["identityId"]
                else {"target": {"blockPosition": [1, 64, 2]}}
                for request in requests
            ]

        calls = self.install_batch(results)
        notices = self.service.poll_mining(lambda _: True)
        self.assertEqual(len(notices), 1)
        self.assertIn("Alice", notices[0][2])
        self.assertIn("raycast failed", notices[0][2])
        self.assertEqual([parameters["loopId"] for parameters in self.mine_calls("stop")],
                         [self.loop_ids["Alice"]])
        self.service.poll_mining(lambda _: True)
        self.assertEqual(len(calls), 2)
        self.assertEqual([request["identityId"] for request in calls[1]],
                         [self.control.bots[name]["identityId"] for name in ("Bob", "Carol")])

    def test_a_failed_batch_call_ends_every_retargeting_loop_with_a_warning(self):
        def failed(requests):
            raise LinkError("timeout", "world call timed out")

        calls = self.install_batch(failed)
        notices = self.service.poll_mining(lambda _: True)
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(notices), len(self.names))
        self.assertTrue(all(notice[1] == "warning" for notice in notices))
        self.assertTrue(all("world call timed out" in notice[2] for notice in notices))
        self.assertEqual(len(self.mine_calls("stop")), len(self.names))
        self.assertEqual(self.service.poll_mining(lambda _: True), [])
        self.assertEqual(len(calls), 1)

    def test_a_batch_result_of_the_wrong_shape_ends_every_loop(self):
        calls = self.install_batch(lambda requests: [])
        notices = self.service.poll_mining(lambda _: True)
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(notices), len(self.names))
        self.assertTrue(all("unexpected result" in notice[2] for notice in notices))

    def test_active_loops_need_no_batch_call(self):
        for name in self.names:
            self.control.bots[name]["mining"]["state"] = "active"
        calls = self.install_batch(lambda requests: self.fail("active loops must not be batched"))
        self.assertEqual(self.service.poll_mining(lambda _: True), [])
        self.assertEqual(calls, [])
        self.assertEqual(len(self.mine_calls("continue")), len(self.names))


if __name__ == "__main__":
    unittest.main()
