import unittest
from types import SimpleNamespace

from endbot_core.commands import CommandSyntaxError, parse_command
from plain_service import PlainCommandService

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
    def create_block_data(name):
        if name == "minecraft:start":
            raise RuntimeError("unknown block")
        return SimpleNamespace(runtime_id=1)

    return EndstoneWorld(SimpleNamespace(get_player=lambda _identity: bot, create_block_data=create_block_data))


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


    def test_find_blocks_stops_early_but_keeps_the_nearest_matches(self):
        ore = Block("minecraft:diamond_ore")
        blocks = {(x, 64, 0): ore for x in range(-6, 7) if x}
        reads = []
        dimension_get = Dimension.get_block_at

        def counting(self, x, y, z):
            reads.append((x, y, z))
            return dimension_get(self, x, y, z)

        Dimension.get_block_at = counting
        try:
            found = world(blocks).find_blocks(BOT["identityId"], "minecraft:diamond_ore", 12, 2)
        finally:
            Dimension.get_block_at = dimension_get
        self.assertEqual(sorted((entry["x"], entry["distance"]) for entry in found), [(-1, 1.1), (1, 1.1)])
        self.assertLess(len(reads), 2000)  # a full radius-12 scan reads over 7000 cells

    def test_an_entity_behind_the_block_is_not_the_target_and_mushroom_stems_hide_ores(self):
        log = {(0, 65, 2): Block("minecraft:oak_log")}
        cow = actor(9, "minecraft:cow", 0.5, 65.0, 2.4)  # past the log face (1.5) but before its centre (2.0)
        self.assertEqual(world(log, [cow]).target(BOT["identityId"])["kind"], "block")
        ore = Block("minecraft:diamond_ore")
        stem = Block("minecraft:mushroom_stem")
        blocks = {(0, 60, 5): ore}
        for dx, dy, dz in ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)):
            blocks[(dx, 60 + dy, 5 + dz)] = stem
        self.assertEqual(world(blocks).find_blocks(BOT["identityId"], "minecraft:diamond_ore", 8, 10), [])

    def test_rafts_and_wood_boats_are_standable_and_coordinates_stay_in_the_world(self):
        from endstone_endbot.world import _standable_half_width

        for kind in ("minecraft:oak_boat", "minecraft:bamboo_raft", "minecraft:cherry_chest_boat", "minecraft:boat"):
            self.assertEqual(_standable_half_width(kind), 0.7, kind)
        self.assertIsNone(_standable_half_width("minecraft:cow"))
        for coordinates in ([30_000_001, 64, 0], [float("inf"), 64, 0], [1e308, 64, 0]):
            with self.assertRaisesRegex(ValueError, "within the world"):
                world({}).block_info(BOT["identityId"], coordinates)


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

    def find_blocks(self, identity, block_type, radius, limit, coordinates=None):
        self.search = (block_type, radius, limit, coordinates)
        return [{"x": 1, "y": 64, "z": 0, "distance": 1.2}]


class QueryCommandTests(unittest.TestCase):
    def test_parsing(self):
        self.assertEqual(parse_command(["Alice", "blocks", "OAK_LOG", "limit", "3"]).parameters,
                         {"type": "minecraft:oak_log", "radius": 8, "limit": 3})
        self.assertEqual(parse_command(["Alice", "blocks", "oak_log", "radius", "16"]).parameters,
                         {"type": "minecraft:oak_log", "radius": 16, "limit": 10})
        self.assertEqual(parse_command(["Alice", "blocks", "oak_log", "at", "~", "~10", "~", "limit", "4"]).parameters,
                         {"type": "minecraft:oak_log", "radius": 8, "limit": 4,
                          "coordinates": [("relative", 0.0), ("relative", 10.0), ("relative", 0.0)]})
        self.assertEqual(parse_command(["Alice", "blocks", "oak_log", "at", "1", "2", "3"]).parameters,
                         {"type": "minecraft:oak_log", "radius": 8, "limit": 10, "coordinates": [1.0, 2.0, 3.0]})
        for arguments in (["blocks"], ["blocks", "oak_log", "radius", "17"], ["blocks", "oak_log", "radius"],
                          ["blocks", "oak_log", "at", "1", "2"], ["blocks", "oak_log", "at", "1", "2", "3", "at",
                                                                    "1", "2", "3"],
                          ["blocks", "oak_log", "radius", "2", "radius", "3"], ["block", "1", "2"], ["target", "x"]):
            with self.subTest(arguments=arguments), self.assertRaises(CommandSyntaxError):
                parse_command(["Alice", *arguments])

    def test_replies_and_data(self):
        queries = FakeQueries()
        service = PlainCommandService(Control(), queries)
        target = service.execute(["Alice", "target"])
        self.assertEqual(target.messages, ("Endbot: Alice: looking at cow @9 (2.0m)",))
        self.assertEqual(target.data["entity"]["runtimeId"], "9")
        block = service.execute(["Alice", "block", "~", "~-1", "~"])
        self.assertEqual(block.messages, ("Endbot: Alice: block at 0 63 0: wheat [growth=7]",))
        self.assertEqual(block.data["states"], {"growth": 7})
        found = service.execute(["Alice", "blocks", "oak_log", "radius", "12"])
        self.assertEqual(queries.search, ("minecraft:oak_log", 12, 10, None))
        # at <x y z> moves the scan centre without moving the Bot.
        service.execute(["Alice", "blocks", "oak_log", "at", "~", "~10", "~"])
        self.assertEqual(queries.search,
                         ("minecraft:oak_log", 8, 10, [("relative", 0.0), ("relative", 10.0), ("relative", 0.0)]))
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
        result = service.execute(["Alice", "entity", "@cow"])
        self.assertEqual(result.messages,
                         ("Endbot: Alice: cow @9 | 2.0m | 1.0 64.0 2.5 | health 8/10 | named Bessie",))
        self.assertEqual(control.calls[-1], ("entities", {"name": "Alice", "type": "minecraft:cow"}))
        self.assertEqual(result.data["entity"]["nameTag"], "Bessie")
        hotbar = service.execute(["Alice", "hotbar"])
        self.assertEqual(hotbar.messages, ("Endbot: Alice: hotbar slot 2: 2: iron_pickaxe x1 (210/250)",))
        inventory = service.execute(["Alice", "inventory"])
        self.assertIn("iron_pickaxe x1 (210/250)", inventory.messages[0])


class SelectorTests(unittest.TestCase):
    def test_target_forms_parse_alike(self):
        cases = {
            ("interact",): {"crosshair": True},
            ("interact", "@cow"): {"entity": "type", "type": "minecraft:cow"},
            ("interact", "@9"): {"entity": "id", "runtimeId": "9"},
            ("interact", "@nearest"): {"entity": "nearest"},
            ("interact", "Chest"): {"blockType": "minecraft:chest"},
            ("attack", "@zombie"): {"entity": "type", "type": "minecraft:zombie"},
            ("destroy", "oak_log"): {"blockType": "minecraft:oak_log"},
            ("entity", "@cow"): {"entity": "type", "type": "minecraft:cow"},
        }
        for arguments, parameters in cases.items():
            with self.subTest(arguments=arguments):
                self.assertEqual(parse_command(["Alice", *arguments]).parameters, parameters)
        self.assertNotIn("face", parse_command(["Alice", "interact", "1", "64", "2"]).parameters)
        self.assertEqual(parse_command(["Alice", "attack"]).operation, "action")

    def test_reachable_block_needs_line_of_sight_and_known_types(self):
        from endbot_core.i18n import LocalizedError

        log = Block("minecraft:oak_log")
        stone = Block("minecraft:stone")
        # A log 3 blocks ahead at eye level is visible; a second one behind it is not chosen first.
        blocks = {(0, 65, 3): log, (0, 65, 5): log}
        self.assertEqual(world(blocks).reachable_block(BOT["identityId"], "minecraft:oak_log", 5.0), [0, 65, 3])
        # Walled off by stone in between: no visible log.
        walled = {(0, 65, 3): log, (0, 65, 2): stone, (1, 65, 2): stone, (-1, 65, 2): stone,
                  (0, 66, 2): stone, (0, 64, 2): stone}
        with self.assertRaises(LocalizedError):
            world(walled).reachable_block(BOT["identityId"], "minecraft:oak_log", 5.0)
        with self.assertRaisesRegex(ValueError, "Unknown block type"):
            world(blocks).find_blocks(BOT["identityId"], "minecraft:start", 8, 10)

    def test_sight_passes_grass_and_sees_a_partly_hidden_side(self):
        log = Block("minecraft:oak_log")
        # A log at foot level two blocks ahead; the line from the eyes crosses short grass.
        grassy = {(0, 64, 3): log, (0, 64, 2): Block("minecraft:short_grass")}
        self.assertEqual(world(grassy).reachable_block(BOT["identityId"], "minecraft:oak_log", 5.0), [0, 64, 3])
        target = world(grassy).resolve_mining(BOT["identityId"], {"coordinates": [0, 64, 3]}, None)
        self.assertEqual((target["blockPosition"], target["face"]), ([0, 64, 3], 2))
        # Leaves hide the centre of every facing side, but a corner of the north side shows.
        leafy = {(-2, 65, 4): log, (-1, 65, 3): Block("minecraft:oak_leaves")}
        self.assertEqual(world(leafy).reachable_block(BOT["identityId"], "minecraft:oak_log", 5.0), [-2, 65, 4])
        target = world(leafy).resolve_mining(BOT["identityId"], {"coordinates": [-2, 65, 4]}, None)
        self.assertEqual(target["face"], 2)
        # Leaves are not see-through: a log boxed in by them stays hidden.
        boxed = {(0, 65, 3): log, **{(dx, 65 + dy, 3 + dz): Block("minecraft:oak_leaves")
                                     for dx, dy, dz in ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, -1))}}
        with self.assertRaisesRegex(ValueError, "blocked"):
            world(boxed).resolve_mining(BOT["identityId"], {"coordinates": [0, 65, 3]}, None)

    def test_interaction_face_defaults_toward_the_bot(self):
        w = world({(0, 64, 3): Block("minecraft:chest")})
        interaction = w.resolve_interaction(BOT["identityId"], {"coordinates": [0, 64, 3]}, None)
        self.assertEqual(interaction["face"], 2)  # north faces the Bot at z=0.5


class SelectorCommandTests(unittest.TestCase):
    class Control:
        def __init__(self):
            self.calls = []

        def request(self, method, **params):
            self.calls.append((method, params))
            if method in {"interactEntity", "attackEntity"}:
                return {**BOT, "entity": {"runtimeId": "9", "type": "minecraft:zombie", "distance": 2.0}}
            return BOT

    class World:
        def __init__(self, seen):
            self.seen = seen

        def target(self, identity):
            return self.seen

        def reachable_block(self, identity, block_type, reach):
            self.reach = (block_type, reach)
            return [1, 64, 2]

        def resolve_interaction(self, identity, parameters, sender):
            self.interaction = parameters
            return {"blockPosition": [1, 64, 2], "blockRuntimeId": 3, "face": parameters.get("face", 2)}

        def resolve_mining(self, identity, parameters, sender):
            self.mining = parameters
            return {"blockPosition": [1, 64, 2], "blockRuntimeId": 3, "blockName": "minecraft:oak_log", "face": 2,
                    "yaw": 0.0, "pitch": 0.0, "dimension": "Overworld"}

    def test_attack_entity_and_crosshair_interact(self):
        control = self.Control()
        entity_view = {"kind": "entity", "entity": {"runtimeId": "9", "type": "minecraft:cow"}, "block": None,
                       "distance": 2.0}
        service = PlainCommandService(control, self.World(entity_view))
        self.assertEqual(service.execute(["Alice", "attack", "@zombie"]).messages,
                         ("Endbot: Alice: attack on zombie @9 requested",))
        self.assertEqual(control.calls[-1], ("attackEntity", {"name": "Alice", "type": "minecraft:zombie"}))
        service.execute(["Alice", "interact"])
        self.assertEqual(control.calls[-1], ("interactEntity", {"name": "Alice", "runtimeId": "9"}))
        block_view = {"kind": "block", "entity": None, "distance": 2.0,
                      "block": {"x": 1, "y": 64, "z": 2, "face": "up", "type": "minecraft:chest"}}
        world_view = self.World(block_view)
        PlainCommandService(control, world_view).execute(["Alice", "interact"])
        self.assertEqual(world_view.interaction, {"coordinates": [1, 64, 2], "face": 1})

    def test_block_types_resolve_the_nearest_visible_block(self):
        control, world_view = self.Control(), self.World(None)
        service = PlainCommandService(control, world_view)
        service.execute(["Alice", "interact", "chest"])
        self.assertEqual(world_view.reach, ("minecraft:chest", 6.0))
        self.assertEqual(world_view.interaction, {"coordinates": [1, 64, 2]})
        service.execute(["Alice", "destroy", "oak_log"])
        self.assertEqual(world_view.reach, ("minecraft:oak_log", 5.0))
        self.assertEqual(world_view.mining, {"coordinates": [1, 64, 2]})


class RepetitionTests(unittest.TestCase):
    def test_targeted_repetition_and_strict_at(self):
        from endbot_core.commands import CommandSyntaxError

        attack = parse_command(["Alice", "attack", "@zombie", "interval", "20", "for", "200"])
        self.assertEqual(attack.operation, "action")
        self.assertEqual(attack.parameters, {"action": "attack", "mode": "interval", "intervalTicks": 20,
                                             "durationTicks": 200, "target": {"type": "minecraft:zombie"}})
        feed = parse_command(["Alice", "interact", "@cow", "count", "5"])
        self.assertEqual((feed.parameters["mode"], feed.parameters["count"], feed.parameters["target"]),
                         ("continuous", 5, {"type": "minecraft:cow"}))
        self.assertEqual(parse_command(["Alice", "interact", "@nearest", "interval", "10"]).parameters["target"], {})
        self.assertEqual(parse_command(["Alice", "interact", "stop"]).parameters,
                         {"action": "interact", "mode": "stop"})
        mining = parse_command(["Alice", "destroy", "oak_log", "count", "10"])
        self.assertEqual(mining.parameters, {"mode": "continuous", "blockCount": 10, "blockType": "minecraft:oak_log"})
        self.assertEqual(parse_command(["Alice", "destroy", "oak_log", "continuous"]).parameters,
                         {"mode": "continuous", "blockType": "minecraft:oak_log"})
        self.assertIn("coordinates", parse_command(["Alice", "destroy", "~", "~-1", "~"]).parameters)
        # once and stop match the Luau table: one click, or stop the repeated action.
        once = parse_command(["Alice", "attack", "@cow", "once"])
        self.assertEqual((once.operation, once.parameters), ("attack", {"entity": "type", "type": "minecraft:cow"}))
        self.assertEqual(parse_command(["Alice", "attack", "@cow", "stop"]).parameters,
                         {"action": "attack", "mode": "stop"})
        for arguments in (["interact", "9"], ["entity", "cow"], ["attack", "@cow", "once", "count", "2"],
                          ["interact", "@cow", "count", "0"],
                          ["destroy", "oak_log", "stop"]):
            with self.subTest(arguments=arguments), self.assertRaises(CommandSyntaxError):
                parse_command(["Alice", *arguments])

    def test_status_shows_a_targeted_repetition_and_its_remaining_count(self):
        summary = PlainCommandService._timed_inputs_summary({"actions": {
            "interact": {"mode": "continuous", "intervalTicks": None, "target": {"type": "minecraft:cow"},
                         "remainingCount": 2},
            "jump": {"mode": "continuous", "intervalTicks": None}}})
        self.assertEqual(summary, "timed: interact @cow continuous 2 left")
        self.assertIn("残り 2 回", PlainCommandService._timed_inputs_summary({"actions": {
            "attack": {"mode": "continuous", "target": {"runtimeId": "42"}, "remainingCount": 2}}}, "ja"))

    def test_repetition_reply_names_target_and_count(self):
        class Control:
            def request(self, method, **params):
                self.params = params
                return {**BOT, "connectionState": "online"}

        control = Control()
        result = PlainCommandService(control).execute(["Alice", "interact", "@cow", "interval", "20", "count", "3"])
        self.assertIn("interact @cow started every 20 ticks", result.messages[0])
        self.assertIn("up to 3 times", result.messages[0])
        self.assertEqual((control.params["target"], control.params["count"]), ({"type": "minecraft:cow"}, 3))
