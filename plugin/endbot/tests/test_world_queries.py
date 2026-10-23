import unittest
from types import SimpleNamespace

from plain_service import PlainCommandService

from endstone_endbot.commands import CommandSyntaxError, parse_command
from endstone_endbot.world import EndstoneWorld

BOT = {"identityId": "00000000-0000-4000-8000-000000000001", "name": "Alice", "desiredState": "online",
       "connectionState": "online"}


class Block:
    def __init__(self, block_type, states=None):
        self.type = block_type
        self.data = SimpleNamespace(block_states=states or {}, runtime_id=1)


class Dimension:
    name = "Overworld"

    def __init__(self, blocks, actors=()):
        self.blocks, self.actors = blocks, list(actors)

    def get_block_at(self, x, y, z):
        return self.blocks.get((x, y, z), Block("minecraft:air"))


def world(blocks, actors=(), yaw=0.0, pitch=0.0, position=(0.5, 64.0, 0.5)):
    dimension = Dimension(blocks, actors)
    location = SimpleNamespace(x=position[0], y=position[1], z=position[2], yaw=yaw, pitch=pitch, dimension=dimension)
    bot = SimpleNamespace(location=location)
    return EndstoneWorld(SimpleNamespace(get_player=lambda _identity: bot))


def actor(runtime_id, actor_type, x, y, z):
    return SimpleNamespace(runtime_id=runtime_id, type=actor_type, location=SimpleNamespace(x=x, y=y, z=z))


class WorldQueryTests(unittest.TestCase):
    def test_target_hits_the_nearer_of_block_and_entity(self):
        log = {(0, 65, 3): Block("minecraft:oak_log")}  # straight ahead along +Z at eye height
        hit = world(log).target(BOT["identityId"])
        self.assertEqual(hit["kind"], "block")
        self.assertEqual((hit["block"]["x"], hit["block"]["y"], hit["block"]["z"], hit["block"]["type"]),
                         (0, 65, 3, "minecraft:oak_log"))
        self.assertEqual(hit["block"]["face"], "north")
        cow = actor(9, "minecraft:cow", 0.5, 65.0, 2.0)
        self.assertEqual(world(log, [cow]).target(BOT["identityId"])["entity"]["runtimeId"], "9")
        player = actor(10, "minecraft:player", 0.5, 65.0, 2.0)
        self.assertEqual(world(log, [player]).target(BOT["identityId"])["kind"], "block")
        self.assertIsNone(world({}).target(BOT["identityId"])["kind"])

    def test_block_info_resolves_relative_coordinates_and_states(self):
        blocks = {(0, 63, 0): Block("minecraft:wheat", {"growth": 7})}
        info = world(blocks).block_info(BOT["identityId"], [("relative", 0), ("relative", -1), ("relative", 0)])
        self.assertEqual(info, {"x": 0, "y": 63, "z": 0, "type": "minecraft:wheat", "states": {"growth": 7}})

    def test_find_blocks_skips_hidden_blocks_and_sorts_by_distance(self):
        stone = Block("minecraft:stone")
        ore = Block("minecraft:diamond_ore")
        blocks = {(3, 64, 0): ore, (1, 64, 0): ore}
        # A buried ore: every neighbour solid.
        buried = (0, 60, 5)
        blocks[buried] = ore
        for dx, dy, dz in ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)):
            blocks[(buried[0] + dx, buried[1] + dy, buried[2] + dz)] = stone
        found = world(blocks).find_blocks(BOT["identityId"], "minecraft:diamond_ore", 8, 10)
        self.assertEqual([(entry["x"], entry["y"], entry["z"]) for entry in found], [(1, 64, 0), (3, 64, 0)])
        self.assertEqual(len(world(blocks).find_blocks(BOT["identityId"], "minecraft:diamond_ore", 8, 1)), 1)


class Control:
    def request(self, method, **params):
        return BOT


class FakeQueries:
    def target(self, identity):
        return {"kind": "entity", "block": None, "distance": 2.0,
                "entity": {"runtimeId": "9", "type": "minecraft:cow"}}

    def block_info(self, identity, coordinates):
        self.coordinates = coordinates
        return {"x": 0, "y": 63, "z": 0, "type": "minecraft:wheat", "states": {"growth": 7}}

    def find_blocks(self, identity, block_type, radius, limit):
        self.search = (block_type, radius, limit)
        return [{"x": 1, "y": 64, "z": 0, "distance": 1.2}]


class QueryCommandTests(unittest.TestCase):
    def test_parsing(self):
        self.assertEqual(parse_command(["Alice", "blocks", "OAK_LOG", "limit", "3"]).parameters,
                         {"type": "minecraft:oak_log", "radius": 8, "limit": 3})
        for arguments in (["blocks"], ["blocks", "oak_log", "radius", "13"], ["blocks", "oak_log", "radius"],
                          ["blocks", "oak_log", "radius", "2", "radius", "3"], ["block", "1", "2"], ["target", "x"]):
            with self.subTest(arguments=arguments), self.assertRaises(CommandSyntaxError):
                parse_command(["Alice", *arguments])

    def test_replies_and_data(self):
        queries = FakeQueries()
        service = PlainCommandService(Control(), queries)
        target = service.execute(["Alice", "target"])
        self.assertEqual(target.messages, ("Endbot: Alice: looking at cow (id 9) (2.0m)",))
        self.assertEqual(target.data["entity"]["runtimeId"], "9")
        block = service.execute(["Alice", "block", "~", "~-1", "~"])
        self.assertEqual(block.messages, ("Endbot: Alice: block at 0 63 0: wheat [growth=7]",))
        self.assertEqual(block.data["states"], {"growth": 7})
        found = service.execute(["Alice", "blocks", "oak_log", "radius", "12"])
        self.assertEqual(queries.search, ("minecraft:oak_log", 12, 10))
        self.assertEqual(found.messages, ("Endbot: Alice: oak_log: 1 64 0 (1.2m)",))
        self.assertEqual(found.data["blocks"][0]["x"], 1)


if __name__ == "__main__":
    unittest.main()


class FeedbackTests(unittest.TestCase):
    def test_compact_relative_coordinates_split_like_vanilla(self):
        for arguments, expected in (
            (["block", "~~-1~"], [("relative", 0.0), ("relative", -1.0), ("relative", 0.0)]),
            (["block", "~1~", "~-2.5"], [("relative", 1.0), ("relative", 0.0), ("relative", -2.5)]),
        ):
            with self.subTest(arguments=arguments):
                self.assertEqual(parse_command(["Alice", *arguments]).parameters["coordinates"], expected)
        self.assertEqual(parse_command(["Alice", "tp", "~~1~"]).parameters["coordinates"],
                         [("relative", 0.0), ("relative", 1.0), ("relative", 0.0)])

    def test_item_summary_reports_durability_only_for_damageable_items(self):
        from endstone_endbot.inventory_sync import item_summary

        pickaxe = SimpleNamespace(type=SimpleNamespace(id="minecraft:iron_pickaxe", max_durability=250), amount=1,
                                  item_meta=SimpleNamespace(damage=40))
        self.assertEqual(item_summary(pickaxe)["durability"], {"remaining": 210, "max": 250})
        stone = SimpleNamespace(type=SimpleNamespace(id="minecraft:stone", max_durability=0), amount=3, item_meta=None)
        self.assertNotIn("durability", item_summary(stone))

    def test_entity_details_and_hotbar_items(self):
        class Control:
            def __init__(self):
                self.calls = []

            def request(self, method, **params):
                self.calls.append((method, params))
                if method == "entities":
                    return {**BOT, "entities": [{"runtimeId": "9", "type": "minecraft:cow", "distance": 2.0}]}
                if method == "hotbar":
                    return {**BOT, "selectedHotbarSlot": 2}
                return BOT

        class World:
            def describe_entities(self, identity, ids):
                return {"9": {"position": {"x": 1.0, "y": 64.0, "z": 2.5}, "health": 8.0, "maxHealth": 10.0,
                              "nameTag": "Bessie"}}

            def inventory(self, name, identity=None):
                return {"name": "Alice", "slots": [
                    {"slot": "2", "name": "minecraft:iron_pickaxe", "count": 1,
                     "durability": {"remaining": 210, "max": 250}},
                    {"slot": "12", "name": "minecraft:stone", "count": 3}]}

        control = Control()
        service = PlainCommandService(control, World())
        result = service.execute(["Alice", "entity", "cow"])
        self.assertEqual(result.messages,
                         ("Endbot: Alice: cow (id 9) | 2.0m | 1.0 64.0 2.5 | health 8/10 | named Bessie",))
        self.assertEqual(control.calls[-1], ("entities", {"name": "Alice", "type": "minecraft:cow"}))
        self.assertEqual(result.data["entity"]["nameTag"], "Bessie")
        hotbar = service.execute(["Alice", "hotbar"])
        self.assertEqual(hotbar.messages, ("Endbot: Alice: hotbar slot 2: 2: iron_pickaxe x1 (210/250)",))
        inventory = service.execute(["Alice", "inventory"])
        self.assertIn("iron_pickaxe x1 (210/250)", inventory.messages[0])
