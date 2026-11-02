"""BOT-027: one area syntax (within/except) for blocks, typed destroy and typed interact."""

import unittest

import test_world  # noqa: F401 - installs the endstone stub that world.py imports
from endbot_core import areas
from endbot_core.commands import CommandSyntaxError, parse_command, typed_areas
from endbot_core.i18n import LocalizedError
from endbot_core.link import resolve_mining_batch
from endbot_core.operations import Operation, parse_operation
from plain_service import PlainCommandService
from test_command_service import FakeControl, FakeWorld
from test_world_queries import BOT, Block, world

FEET = [("relative", 0.0), ("relative", -1.0), ("relative", 0.0)]
LOG = "minecraft:oak_log"


def area_of(*arguments):
    return parse_command(["Alice", "blocks", "oak_log", *arguments]).parameters["area"]


class AreaParserTests(unittest.TestCase):
    def test_the_number_of_values_selects_the_form(self):
        self.assertEqual(area_of("within", "1", "64", "~2")["within"],
                         {"shape": "cell", "at": [1.0, 64.0, ("relative", 2.0)]})
        self.assertEqual(area_of("within", "0", "60", "0", "~4", "66", "4")["within"],
                         {"shape": "box", "from": [0.0, 60.0, 0.0], "to": [("relative", 4.0), 66.0, 4.0]})
        self.assertEqual(area_of("within", "6")["within"], {"shape": "sphere", "radius": 6.0})
        self.assertEqual(area_of("within", "2.5", "of", "1", "~", "3")["within"],
                         {"shape": "sphere", "radius": 2.5, "at": [1.0, ("relative", 0.0), 3.0]})
        # Compact ~ works as in vanilla commands, and except takes every form too.
        self.assertEqual(area_of("except", "~~-1~"), {"within": None, "except": [{"shape": "cell", "at": FEET}]})
        self.assertEqual(area_of("within", "6", "except", "3", "of", "~", "~", "~")["except"],
                         [{"shape": "sphere", "radius": 3.0, "at": [("relative", 0.0)] * 3}])

    def test_wrong_value_counts_and_shapes_are_syntax_errors(self):
        for values, message in (
            ((), "within takes an area"),
            (("1", "2"), "within takes an area"),
            (("1", "2", "3", "4"), "within takes an area"),
            (("1", "2", "3", "4", "5"), "within takes an area"),
            (("1", "2", "3", "4", "5", "6", "7"), "within takes an area"),
            (("2", "of", "1", "2"), "within takes an area"),
            (("1", "2", "of", "1", "2", "3"), "within takes an area"),
            (("cylinder", "3", "4"), "Unknown area shape 'cylinder'"),
            (("0",), "greater than 0"),
            (("~3",), "plain number"),
            (("17",), "radius-16 sphere"),
            (("0", "0", "0", "40", "40", "40"), "radius-16 sphere"),
            (("1e9",), "within takes an area"),
        ):
            with self.subTest(values=values), self.assertRaisesRegex(CommandSyntaxError, message):
                area_of("within", *values)
        with self.assertRaisesRegex(CommandSyntaxError, "except takes an area"):
            parse_command(["Alice", "destroy", "oak_log", "except", "count", "5"])

    def test_chat_takes_each_clause_once_and_typed_operations_chain_except(self):
        twice = ["Alice", "destroy", "oak_log", "within", "6", "except", "~", "~-1", "~", "except", "1", "2", "3"]
        with self.assertRaisesRegex(CommandSyntaxError, "except can be given once"):
            parse_command(twice)
        with self.assertRaisesRegex(CommandSyntaxError, "within can be given once"):
            parse_command(["Alice", "blocks", "oak_log", "within", "6", "within", "3"])
        with typed_areas():
            area = parse_command(twice).parameters["area"]
            with self.assertRaisesRegex(CommandSyntaxError, "within can be given once"):
                parse_command(["Alice", "blocks", "oak_log", "within", "6", "within", "3"])
        self.assertEqual(len(area["except"]), 2)
        # The typed context ends with the operation.
        with self.assertRaises(CommandSyntaxError):
            parse_command(twice)

    def test_areas_apply_to_typed_targets_and_continuous_loops_only(self):
        destroy = parse_command(["Alice", "destroy", "acacia_log", "within", "6", "except", "~", "~-1", "~",
                                 "count", "20"]).parameters
        self.assertEqual(destroy, {"mode": "continuous", "blockCount": 20, "blockType": "minecraft:acacia_log",
                                   "area": {"within": {"shape": "sphere", "radius": 6.0},
                                            "except": [{"shape": "cell", "at": FEET}]}})
        # Clause order is free: the area may come first.
        self.assertEqual(parse_command(["Alice", "destroy", "within", "6", "acacia_log", "count", "20"]).parameters,
                         {**destroy, "area": {"within": {"shape": "sphere", "radius": 6.0}, "except": []}})
        self.assertIn("area", parse_command(["Alice", "destroy", "oak_log", "within", "6"]).parameters)
        self.assertIn("area", parse_command(["Alice", "destroy", "continuous", "within", "6"]).parameters)
        self.assertIn("area", parse_command(["Alice", "destroy", "for", "20", "within", "6"]).parameters)
        self.assertEqual(parse_command(["Alice", "interact", "chest", "within", "1", "64", "2"]).parameters,
                         {"blockType": "minecraft:chest",
                          "area": {"within": {"shape": "cell", "at": [1.0, 64.0, 2.0]}, "except": []}})
        for arguments in (["destroy", "within", "6"], ["destroy", "once", "within", "6"],
                          ["destroy", "1", "64", "2", "within", "6"], ["destroy", "stop", "within", "6"],
                          ["interact", "within", "6"], ["interact", "1", "64", "2", "within", "6"],
                          ["interact", "@cow", "within", "6"], ["interact", "@cow", "continuous", "within", "6"]):
            with self.subTest(arguments=arguments), self.assertRaisesRegex(CommandSyntaxError, "An area applies"):
                parse_command(["Alice", *arguments])

    def test_typed_operations_write_and_read_back_the_canonical_clauses(self):
        operation = Operation("destroy", "Alice", {"type": "oak_log", "count": 20,
                                                   "area": "within 6 except ~ ~-1 ~ except 0 64 0 2 66 2"})
        argv = operation.argv()
        self.assertEqual(argv, ["Alice", "destroy", "oak_log", "count", "20", "within", "6", "except", "~", "~-1",
                                "~", "except", "0", "64", "0", "2", "66", "2"])
        self.assertEqual(parse_operation(argv), operation)
        # The value is re-rendered canonically, so text that is not an area never reaches the command.
        self.assertEqual(Operation("blocks", "Alice", {"type": "oak_log", "area": "within  6.0 of 1 ~0 3"}).argv(),
                         ["Alice", "blocks", "oak_log", "within", "6", "of", "1", "~", "3"])
        for value in ("", "6", "within 6 count 5", "within 6; stop", "within 17", {"within": 6}, 6):
            with self.subTest(value=value), self.assertRaises(ValueError):
                Operation("blocks", "Alice", {"type": "oak_log", "area": value}).argv()


class AreaGeometryTests(unittest.TestCase):
    def test_resolution_fixes_tilde_and_orders_box_corners(self):
        area = areas.resolve_area(area_of("within", "~2", "~", "~-2", "~-2", "~3", "~2", "except", "~", "~-1", "~"),
                                  [10.5, 64.0, -3.2])
        self.assertEqual(area, {"within": {"shape": "box", "from": [8, 64, -6], "to": [12, 67, -2]},
                                "except": [{"shape": "cell", "at": [10, 63, -4]}]})
        self.assertEqual(areas.resolve_area(area, [0, 0, 0]), area)  # a resolved area resolves to itself
        self.assertTrue(areas.contains(area, (8, 64, -6)) and areas.contains(area, (12, 67, -2)))
        self.assertFalse(areas.contains(area, (10, 63, -4)) or areas.contains(area, (13, 64, -4)))
        sphere = areas.resolve_area(area_of("within", "2"), [0.5, 64.0, 0.5])
        self.assertEqual(sphere["within"], {"shape": "sphere", "radius": 2.0, "at": [0.5, 64.0, 0.5]})
        self.assertTrue(areas.contains(sphere, (1, 64, 1)))  # cell centre 1.5 away
        self.assertFalse(areas.contains(sphere, (0, 64, 2)))  # cell centre 2.06 away
        self.assertEqual(sorted(areas.cells(areas.resolve_area(area_of("within", "1", "of", "0.5", "0.5", "0.5"),
                                                               [0, 0, 0]))),
                         [(-1, 0, 0), (0, -1, 0), (0, 0, -1), (0, 0, 0), (0, 0, 1), (0, 1, 0), (1, 0, 0)])

    def test_a_box_over_the_cap_is_refused_once_its_size_is_known(self):
        box = area_of("within", "0", "0", "0", "~", "~", "~")  # the size depends on where the Bot stands
        self.assertEqual(areas.resolve_area(box, [20, 20, 20])["within"]["to"], [20, 20, 20])
        with self.assertRaisesRegex(LocalizedError, "radius-16 sphere"):
            areas.resolve_area(box, [40, 40, 40])
        # except only narrows, so it is not capped.
        self.assertEqual(len(area_of("except", "0", "0", "0", "99", "99", "99")["except"]), 1)


class AreaWorldTests(unittest.TestCase):
    def fell(self, blocks, area):
        """Break the nearest reachable log in the area until none is left, as a typed loop retargets."""
        query = world(blocks)
        resolved = None if area is None else query.resolve_area(BOT["identityId"], area)
        broken = []
        while len(broken) < 20:
            try:
                cell = query.reachable_block(BOT["identityId"], LOG, 6.0, area=resolved)
            except LocalizedError:
                break
            broken.append(tuple(cell))
            blocks[tuple(cell)] = Block("minecraft:air")
        return broken

    def tree(self):
        # The Bot stands on a log (its footing) beside a trunk; one more log is out of reach.
        blocks = {(0, 63, 0): Block(LOG), (0, 62, 0): Block("minecraft:dirt"), (0, 64, 9): Block(LOG)}
        blocks.update({(2, y, 0): Block(LOG) for y in range(63, 70)})
        return blocks

    def test_except_the_footing_breaks_every_other_reachable_log(self):
        unguarded = self.fell(self.tree(), None)
        self.assertIn((0, 63, 0), unguarded)  # without except the footing is broken too
        blocks = self.tree()
        broken = self.fell(blocks, area_of("except", "~", "~-1", "~"))
        self.assertEqual(sorted(broken), [(2, y, 0) for y in range(63, 70)])
        self.assertEqual(blocks[(0, 63, 0)].type, LOG)  # the footing stays
        self.assertEqual(blocks[(0, 64, 9)].type, LOG)  # out of reach, area or not
        # A within/except pair narrows the same way.
        blocks = self.tree()
        broken = self.fell(blocks, area_of("within", "2", "60", "0", "2", "66", "0", "except", "~", "~-1", "~"))
        self.assertEqual(sorted(broken), [(2, y, 0) for y in range(63, 67)])

    def test_an_area_never_reaches_past_the_normal_limits(self):
        blocks = {(0, 64, 12): Block(LOG), (0, 64, 20): Block(LOG), (0, 64, 7): Block(LOG)}
        query = world(blocks)
        find = lambda area: [(entry["x"], entry["y"], entry["z"]) for entry in  # noqa: E731
                             query.find_blocks(BOT["identityId"], LOG, 10, None if area is None else
                                               query.resolve_area(BOT["identityId"], area))]
        self.assertEqual(find(None), [(0, 64, 7)])  # the default sphere of 8
        self.assertEqual(find(area_of("within", "16")), [(0, 64, 7), (0, 64, 12)])
        # A box or sphere around a far point never lists what lies past 16 blocks from the Bot.
        self.assertEqual(find(area_of("within", "-2", "60", "10", "2", "68", "22")), [(0, 64, 12)])
        self.assertEqual(find(area_of("within", "16", "of", "0", "64", "20")), [(0, 64, 7), (0, 64, 12)])
        self.assertEqual(find(area_of("within", "2", "of", "0", "64", "20")), [])
        # destroy and interact keep their 6-block reach: an area around a 7-block log does not reach it.
        with self.assertRaisesRegex(LocalizedError, "No oak_log in sight"):
            query.reachable_block(BOT["identityId"], LOG, 6.0,
                                  area=query.resolve_area(BOT["identityId"], area_of("within", "0", "64", "7")))

    def test_an_area_hides_blocks_but_not_the_reach_rule(self):
        blocks = {(0, 64, 3): Block(LOG), (1, 64, 3): Block(LOG)}
        query = world(blocks)
        area = query.resolve_area(BOT["identityId"], area_of("except", "0", "64", "3"))
        self.assertEqual(query.reachable_block(BOT["identityId"], LOG, 6.0), [0, 64, 3])
        self.assertEqual(query.reachable_block(BOT["identityId"], LOG, 6.0, area=area), [1, 64, 3])
        self.assertEqual([entry["x"] for entry in query.find_blocks(BOT["identityId"], LOG, 10, area)], [1])


class AreaWorld(FakeWorld):
    def __init__(self):
        super().__init__()
        self.reached = []

    def resolve_area(self, identity_id, area):
        return areas.resolve_area(area, [0.5, 64.0, 0.5])

    def reachable_block(self, identity_id, block_type, reach, area=None):
        self.reached.append((block_type, area))
        return [1, 64, 2]

    def resolve_interaction(self, identity_id, parameters, sender):
        return {"blockPosition": [1, 64, 2], "blockRuntimeId": 42, "face": parameters.get("face", 1)}


class AreaServiceTests(unittest.TestCase):
    def setUp(self):
        self.control = FakeControl()
        self.world = AreaWorld()
        self.service = PlainCommandService(self.control, self.world)
        self.service.execute(["Alice", "spawn"])

    def mines(self):
        return [parameters for operation, parameters in self.control.calls if operation == "mine"]

    def test_typed_destroy_and_interact_pass_the_resolved_area(self):
        resolved = {"within": {"shape": "sphere", "radius": 6.0, "at": [0.5, 64.0, 0.5]},
                    "except": [{"shape": "cell", "at": [0, 63, 0]}]}
        self.assertTrue(self.service.execute(["Alice", "destroy", "oak_log", "within", "6", "except", "~", "~-1",
                                              "~"]).accepted)
        self.assertTrue(self.service.execute(["Alice", "interact", "chest", "within", "6", "except", "~", "~-1",
                                              "~"]).accepted)
        self.assertEqual(self.world.reached, [(LOG, resolved), ("minecraft:chest", resolved)])
        # Without an area nothing changes.
        self.service.execute(["Alice", "destroy", "oak_log"])
        self.assertEqual(self.world.reached[-1], (LOG, None))

    def test_a_typed_loop_keeps_the_area_it_resolved_when_accepted(self):
        result = self.service.execute(["Alice", "destroy", "oak_log", "within", "6", "except", "~", "~-1", "~",
                                       "count", "5"])
        self.assertTrue(result.accepted)
        self.world.resolve_area = lambda *_: self.fail("an accepted loop never resolves its area again")
        self.service.poll_mining(lambda _: True)
        self.service.poll_mining(lambda _: True)
        expected = {"within": {"shape": "sphere", "radius": 6.0, "at": [0.5, 64.0, 0.5]},
                    "except": [{"shape": "cell", "at": [0, 63, 0]}]}
        self.assertEqual(self.world.reached, [(LOG, expected), (LOG, expected)])
        self.assertEqual(self.mines()[-1]["blockPosition"], [1, 64, 2])

    def test_a_sight_loop_waits_while_its_target_is_outside_the_area(self):
        self.service.execute(["Alice", "destroy", "continuous", "within", "5", "5", "5"])
        self.service.poll_mining(lambda _: True)
        self.assertNotIn("blockPosition", self.mines()[-1])  # crosshair block [1, 64, 2] is outside
        self.service.execute(["Alice", "destroy", "continuous", "within", "1", "64", "2"])
        self.service.poll_mining(lambda _: True)
        self.assertEqual(self.mines()[-1]["blockPosition"], [1, 64, 2])

    def test_the_mining_batch_carries_and_checks_areas(self):
        self.assertEqual(resolve_mining_batch(self.world, [
            {"identityId": "a", "blockType": LOG, "area": {"within": None, "except": []}},
            {"identityId": "b", "blockType": None, "area": {"within": {"shape": "cell", "at": [9, 9, 9]},
                                                             "except": []}},
        ])[1], {"target": None})
        self.assertEqual(self.world.reached, [(LOG, {"within": None, "except": []})])
        with self.assertRaisesRegex(ValueError, "area must be an object"):
            resolve_mining_batch(self.world, [{"identityId": "a", "blockType": None, "area": "within 6"}])

    def test_scripts_chain_except_and_chat_refuses_a_second_one(self):
        operation = Operation("destroy", "Alice", {"type": "oak_log", "count": 5,
                                                   "area": "within 6 except ~ ~-1 ~ except 1 64 3"})
        self.assertTrue(self.service.execute_operation(operation).accepted)
        self.service.poll_mining(lambda _: True)
        self.assertEqual(len(self.world.reached[-1][1]["except"]), 2)
        refused = self.service.execute(operation.argv())
        self.assertFalse(refused.accepted)
        self.assertIn("except can be given once", refused.message)


if __name__ == "__main__":
    unittest.main()
