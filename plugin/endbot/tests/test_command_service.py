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
        return {**self.bots[name], "connectionState": "online"}


class FakeWorld:
    def __init__(self) -> None:
        self.placements = []
        self.cleared_placements = []
        self.collisions = {}
        self.invalid_dimensions = set()
        self.look_targets = []

    def default_spawn(self, sender):
        return {"coordinates": [1, 2, 3], "dimension": "minecraft:overworld", "rotation": [0, 0]}

    def resolve_spawn_placement(self, placement, sender):
        if placement.get("dimension") in self.invalid_dimensions:
            raise ValueError("Unknown dimension")
        if sender is None and "dimension" not in placement:
            raise ValueError("A dimension is required")
        return {**placement, "resolved": True}

    def resolve_interaction(self, identity_id, parameters, sender):
        return {"blockPosition": [1, 64, 2], "blockRuntimeId": 42, "face": parameters["face"]}

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

    def test_spawn_queues_world_placement_after_profile_creation(self) -> None:
        result = self.service.execute(["Alice", "spawn"], object())
        self.assertIn("created and connecting", result.message)
        self.assertEqual([call[0] for call in self.control.calls], ["status", "spawn"])
        self.assertEqual(self.world.placements[0][1]["coordinates"], [1, 2, 3])

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
        self.world.collisions = {"Alice": identity_id}
        result = self.service.execute(["Alice", "spawn"], object())
        self.assertIn("already online", result.message)

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
        self.assertIn("online", result.message)

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


if __name__ == "__main__":
    unittest.main()
