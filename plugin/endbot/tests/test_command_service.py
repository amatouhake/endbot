import unittest

from endstone_endbot.commands import BotCommandService


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
            return self.bots[name]
        if operation == "spawn":
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
        return {**self.bots[name], "connectionState": "online"}


class FakeWorld:
    def __init__(self) -> None:
        self.placements = []
        self.collisions = set()

    def default_spawn(self, sender):
        return {"coordinates": [1, 2, 3], "dimension": "minecraft:overworld", "rotation": [0, 0]}

    def queue_spawn_placement(self, identity_id, placement, sender):
        self.placements.append((identity_id, placement))

    def observe(self, identity_id):
        return None

    def assert_name_available(self, name):
        if name in self.collisions:
            raise ValueError("online player collision")


class CommandServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.control = FakeControl()
        self.world = FakeWorld()
        self.service = BotCommandService(self.control, self.world)

    def test_spawn_queues_world_placement_after_profile_creation(self) -> None:
        result = self.service.execute(["Alice", "spawn"], object())
        self.assertIn("created and connecting", result.message)
        self.assertEqual(self.control.calls[0][0], "spawn")
        self.assertEqual(self.world.placements[0][1]["coordinates"], [1, 2, 3])

    def test_commands_target_independent_names(self) -> None:
        self.service.execute(["Alice", "spawn"], object())
        self.service.execute(["Bob", "spawn"], object())
        self.service.execute(["Alice", "move", "forward"], object())
        self.assertEqual(self.control.calls[-1], ("move", {"name": "Alice", "direction": "forward"}))

    def test_rename_checks_online_player_before_runtime_update(self) -> None:
        self.service.execute(["Alice", "spawn"], object())
        self.world.collisions.add("Steve")
        result = self.service.execute(["Alice", "rename", "Steve"], object())
        self.assertIn("collision", result.message)
        self.assertNotIn("rename", [call[0] for call in self.control.calls])


if __name__ == "__main__":
    unittest.main()
