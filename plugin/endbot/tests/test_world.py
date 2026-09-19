import sys
import types
import unittest
from types import SimpleNamespace
from uuid import UUID

if "endstone.level" not in sys.modules:
    endstone = types.ModuleType("endstone")
    level = types.ModuleType("endstone.level")
    level.Location = type("Location", (), {})
    sys.modules["endstone"] = endstone
    sys.modules["endstone.level"] = level

import endstone_endbot.world as world_module
from endstone_endbot.world import EndstoneWorld


class FakeLocation:
    def __init__(self, dimension, x, y, z, pitch=0, yaw=0):
        self.dimension = dimension
        self.x, self.y, self.z = x, y, z
        self.pitch, self.yaw = pitch, yaw


class FakePlayer:
    def __init__(self, identity, name, location):
        self.unique_id = UUID(identity)
        self.name = name
        self.location = location
        self.teleports = []

    def teleport(self, target):
        self.teleports.append(target)
        if hasattr(target, "location"):
            self.location = target.location
        else:
            self.location = target
        return True


class FakeServer:
    def __init__(self):
        self.dimensions = {}
        for name in ("Overworld", "Nether", "TheEnd"):
            dimension = SimpleNamespace(name=name)
            dimension.get_block_at = lambda x, y, z: SimpleNamespace(data=SimpleNamespace(runtime_id=42))
            self.dimensions[name] = dimension
        self.level = SimpleNamespace(get_dimension=lambda name: self.dimensions.get(name))
        self.players = {}
        self.dispatched = []

    def get_player(self, key):
        return self.players.get(str(key).casefold())

    def dispatch_command(self, *args):
        self.dispatched.append(args)
        raise AssertionError("vanilla commands must not be used")


class WorldTests(unittest.TestCase):
    def setUp(self):
        world_module.Location = FakeLocation
        self.server = FakeServer()
        overworld = self.server.dimensions["Overworld"]
        self.alice = FakePlayer("00000000-0000-4000-8000-000000000001", "Alice", FakeLocation(overworld, 0, 64, 0))
        self.steve = FakePlayer(
            "00000000-0000-4000-8000-000000000002",
            "Steve",
            FakeLocation(overworld, 8, 70, 9, 20, 30),
        )
        for player in (self.alice, self.steve):
            self.server.players[str(player.unique_id)] = player
            self.server.players[player.name.casefold()] = player
        self.world = EndstoneWorld(self.server)

    def test_tp_player_uses_actor_api_not_vanilla_command(self):
        result = self.world.teleport(str(self.alice.unique_id), {"target": "Steve"}, self.steve)
        self.assertIn("Overworld", result)
        self.assertIs(self.alice.teleports[-1], self.steve)
        self.assertEqual(self.server.dispatched, [])

    def test_cross_dimension_relative_tp_and_rotation(self):
        result = self.world.teleport(
            str(self.alice.unique_id),
            {"dimension": "nether", "coordinates": [("relative", 2), 80.0, -3.0], "rotation": [90, 10]},
            self.steve,
        )
        location = self.alice.teleports[-1]
        self.assertEqual((location.dimension.name, location.x, location.y, location.z), ("Nether", 10.0, 80.0, -3.0))
        self.assertEqual((location.yaw, location.pitch), (90, 10))
        self.assertIn("Nether", result)

    def test_pending_spawn_placement_targets_uuid_only(self):
        placement = self.world.default_spawn(self.steve)
        placement = self.world.resolve_spawn_placement(placement, self.steve)
        self.server.players.pop(str(self.alice.unique_id))
        self.server.players.pop(self.alice.name.casefold())
        self.assertFalse(self.world.queue_spawn_placement(str(self.alice.unique_id), placement))
        unrelated = FakePlayer("00000000-0000-4000-8000-000000000003", "Bob", self.steve.location)
        self.assertFalse(self.world.apply_pending_placement(unrelated))
        self.server.players[str(self.alice.unique_id)] = self.alice
        self.server.players[self.alice.name.casefold()] = self.alice
        self.assertTrue(self.world.apply_pending_placement(self.alice))
        self.assertEqual(self.alice.location.x, self.steve.location.x)

    def test_spawn_placement_reconciles_a_player_who_joined_before_queueing(self):
        placement = self.world.resolve_spawn_placement(self.world.default_spawn(self.steve), self.steve)

        self.assertTrue(self.world.queue_spawn_placement(str(self.alice.unique_id), placement))

        self.assertEqual(self.alice.location.x, self.steve.location.x)
        self.assertFalse(self.world.apply_pending_placement(self.alice))

    def test_pending_spawn_placement_can_be_cleared_before_resume(self):
        placement = self.world.resolve_spawn_placement(self.world.default_spawn(self.steve), self.steve)
        self.server.players.pop(str(self.alice.unique_id))
        self.server.players.pop(self.alice.name.casefold())
        self.world.queue_spawn_placement(str(self.alice.unique_id), placement)

        self.world.clear_pending_placement(str(self.alice.unique_id))

        self.server.players[str(self.alice.unique_id)] = self.alice
        self.server.players[self.alice.name.casefold()] = self.alice
        self.assertFalse(self.world.apply_pending_placement(self.alice))
        self.assertEqual(self.alice.location.x, 0)

    def test_spawn_resolution_rejects_missing_and_unknown_console_dimensions(self):
        with self.assertRaisesRegex(ValueError, "dimension is required"):
            self.world.resolve_spawn_placement({"coordinates": [1, 64, 2]}, None)
        with self.assertRaisesRegex(ValueError, "Unknown dimension"):
            self.world.resolve_spawn_placement(
                {"coordinates": [1, 64, 2], "dimension": "missing:dimension"},
                None,
            )

    def test_relative_facing_uses_the_command_source_reference(self):
        self.world.teleport(
            str(self.alice.unique_id),
            {
                "coordinates": [100.0, 64.0, 100.0],
                "facingCoordinates": [("relative", 1), ("relative", 0), ("relative", 0)],
            },
            self.steve,
        )
        location = self.alice.teleports[-1]
        expected = self.world._facing([100.0, 64.0, 100.0], [9.0, 70.0, 9.0])
        self.assertAlmostEqual(location.yaw, expected[0])
        self.assertAlmostEqual(location.pitch, expected[1])

    def test_interaction_uses_bot_dimension_and_public_block_runtime_id(self):
        result = self.world.resolve_interaction(
            str(self.alice.unique_id),
            {"coordinates": [("relative", 1), 64.9, -2.1], "face": 1},
            self.steve,
        )
        self.assertEqual(result, {"blockPosition": [9, 64, -3], "blockRuntimeId": 42, "face": 1})


if __name__ == "__main__":
    unittest.main()
