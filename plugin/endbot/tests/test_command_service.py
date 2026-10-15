import unittest

from endstone_endbot.commands import BotCommandService
from endstone_endbot.control import RuntimeControlError


class FakeControl:
    def __init__(self) -> None:
        self.calls = []
        self.bots = {}

    def request(self, operation, **parameters):
        self.calls.append((operation, parameters))
        name = parameters.get("name", "")
        if operation == "list":
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

    def queue_spawn_placement(self, identity_id, placement):
        self.placements.append((identity_id, placement))

    def clear_pending_placement(self, identity_id):
        self.cleared_placements.append(identity_id)
        self.placements = [entry for entry in self.placements if entry[0] != identity_id]

    def observe(self, identity_id):
        return None

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
        self.service = BotCommandService(self.control, self.world)

    def test_continuous_mining_retargets_then_waits_without_restarting_active_block(self):
        self.service.execute(["Alice", "spawn"])
        source = object()
        reply = self.service.execute(["Alice", "mine", "start"], source)
        self.assertIn("continuous mining started", reply.message)
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
        self.assertIn("continuous waiting", self.service.execute(["Alice", "status"]).message)
        self.service.execute(["Alice", "mine", "stop"])
        before = len(self.control.calls)
        self.service.poll_mining(lambda _: True)
        self.assertEqual(len(self.control.calls), before)

    def test_continuous_mining_keeps_waiting_when_runtime_rejects_a_target(self):
        self.service.execute(["Alice", "spawn"])
        self.service.execute(["Alice", "mine", "start"])
        loop_id = self.control.bots["Alice"]["mining"]["loopId"]
        request = self.control.request

        def reject_targets(operation, **parameters):
            if operation == "mine" and "blockPosition" in parameters:
                self.control.calls.append((operation, parameters))
                raise RuntimeControlError("invalid_request", "Mining target is out of reach")
            return request(operation, **parameters)

        self.control.request = reject_targets
        self.service.poll_mining(lambda _: True)
        self.assertEqual(self.control.calls[-1], ("mine", {"name": "Alice", "mode": "continue", "loopId": loop_id}))
        self.service.poll_mining(lambda _: True)
        self.assertNotIn("stop", [call[1].get("mode") for call in self.control.calls if call[0] == "mine"])
        # An unreachable runtime still ends the loop.
        def unavailable(operation, **parameters):
            self.control.calls.append((operation, parameters))
            if operation == "mine" and parameters.get("mode") == "stop":
                return {}
            raise RuntimeControlError("unavailable", "runtime unavailable")

        self.control.request = unavailable
        self.service.poll_mining(lambda _: True)
        self.assertEqual(self.control.calls[-1], ("mine", {"name": "Alice", "mode": "stop", "loopId": loop_id}))

    def test_continuous_mining_stops_on_revocation_and_does_not_adopt_new_session(self):
        self.service.execute(["Alice", "spawn"])
        self.service.execute(["Alice", "mine", "start"])
        loop_id = self.control.bots["Alice"]["mining"]["loopId"]
        self.service.poll_mining(lambda _: False)
        self.assertEqual(self.control.calls[-1], ("mine", {"name": "Alice", "mode": "stop", "loopId": loop_id}))
        self.service.execute(["Alice", "mine", "start"])
        self.control.bots["Alice"]["mining"]["loopId"] = "replacement-session"
        self.service.poll_mining(lambda _: True)
        self.assertEqual(self.control.calls[-1][0], "status")
        self.service.close()
        self.assertEqual(self.control.calls[-1][0], "status")

    def test_continuous_mining_close_and_world_failure_stop_only_owned_loop(self):
        self.service.execute(["Alice", "spawn"])
        for shutdown in (False, True):
            self.service.execute(["Alice", "mine", "start"])
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
        self.assertIn("requested=12 (bot)", updated.message)
        self.assertIn("server=unconfirmed", updated.message)
        self.control.bots["Alice"]["serverChunkRadius"] = 10
        query = self.service.execute(["Alice", "radius"])
        self.assertIn("server=10", query.message)
        reset = self.service.execute(["Alice", "radius", "default"])
        self.assertIn("requested=5 (default)", reset.message)

    def test_mining_starts_authoritative_target_and_stop_skips_world_resolution(self):
        self.service.execute(["Alice", "spawn"], object())
        reply = self.service.execute(["Alice", "mine", "1", "64", "2"], object())
        self.assertIn("mining started at 1 64 2 (minecraft:stone)", reply.message)
        self.assertEqual(self.control.calls[-1][0], "mine")
        self.assertEqual(self.control.calls[-1][1]["blockRuntimeId"], 42)
        self.world.resolve_mining = lambda *args: self.fail("stop must not resolve a new target")
        stopped = self.service.execute(["Alice", "mine", "stop"], object())
        self.assertIn("mining stopped", stopped.message)
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

    def test_failed_action_identifies_bot_and_operation(self):
        def unavailable(operation, **parameters):
            raise RuntimeControlError("not_online", "Bot is offline")

        self.control.request = unavailable
        result = self.service.execute(["Alice", "move", "forward"], object())
        self.assertEqual(result.message, "Endbot: Alice move failed: Bot is offline")

    def test_lifecycle_replies_identify_change_instead_of_only_state(self):
        self.service.execute(["Alice", "spawn"], object())
        self.assertIn("renamed Alice to Bob", self.service.execute(["Alice", "rename", "Bob"], object()).message)
        for operation in ("resume", "reconnect", "despawn"):
            self.assertIn(f"{operation} requested", self.service.execute(["Bob", operation], object()).message)
        self.assertIn("profile registration removed", self.service.execute(["Bob", "forget"], object()).message)


if __name__ == "__main__":
    unittest.main()
