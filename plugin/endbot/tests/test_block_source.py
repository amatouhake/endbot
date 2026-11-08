"""The plugin's block.change source (BOT-022 step 3b): poll phases, confirms, unloaded chunks, Bot attribution."""

import unittest
from types import SimpleNamespace

from endstone_endbot.block_source import DEATH_KIND, KIND, POLL_TICKS, BlockChangeSource

TYPES = {"minecraft:air": 1, "minecraft:stone": 2, "minecraft:dirt": 3, "minecraft:wooden_door": 4,
         "minecraft:wooden_door[open]": 5}


class FakeDimension:
    def __init__(self, name):
        self.name = name
        self.blocks = {}
        self.chunks = {(0, 0)}
        self.reads = 0

    @property
    def loaded_chunks(self):
        return [SimpleNamespace(x=x, z=z) for x, z in self.chunks]

    def get_block_at(self, x, y, z):
        self.reads += 1
        name = self.blocks.get((x, y, z), "minecraft:air")
        return SimpleNamespace(type=name.split("[")[0], data=SimpleNamespace(runtime_id=TYPES[name]),
                               x=x, y=y, z=z, dimension=self)


class FakeServer:
    def __init__(self):
        self.dimensions = {"Overworld": FakeDimension("Overworld"), "Nether": FakeDimension("Nether")}
        self.level = SimpleNamespace(get_dimension=self.dimensions.__getitem__)


def interest(*cells, dimension="overworld", kinds=(KIND,)):
    return {"revision": 1, "block": [{"dimension": dimension, "cells": [list(cell) for cell in cells]}],
            "kinds": list(kinds)}


class BlockChangeSourceTests(unittest.TestCase):
    def setUp(self):
        self.server = FakeServer()
        self.world = self.server.dimensions["Overworld"]
        self.reports = []
        self.source = BlockChangeSource(self.server, lambda tick, items, dropped: self.reports.append(
            (tick, items, dropped)))

    def items(self):
        return [item for _, items, _ in self.reports for item in items]

    def run_ticks(self, count):
        for _ in range(count):
            self.source.on_tick()

    def test_a_new_position_is_a_baseline_and_a_held_change_is_reported_within_the_poll_period(self):
        self.world.blocks[(1, 64, 1)] = "minecraft:stone"
        self.source.apply_interest(interest((1, 64, 1), (2, 64, 1)))
        self.run_ticks(POLL_TICKS)
        self.assertEqual(self.reports, [])
        self.world.blocks[(1, 64, 1)] = "minecraft:air"
        self.run_ticks(POLL_TICKS)
        self.assertEqual(self.items(), [{"kind": KIND, "x": 1, "y": 64, "z": 1, "dimension": "overworld",
                                         "from": "minecraft:stone", "to": "minecraft:air", "cause": "poll"}])

    def test_each_tick_reads_a_quarter_of_the_watched_positions(self):
        cells = [(x, 64, 0) for x in range(8)]
        self.source.apply_interest(interest(*cells))
        self.world.reads = 0
        self.run_ticks(1)
        self.assertEqual(self.world.reads, 8 // POLL_TICKS)
        self.run_ticks(POLL_TICKS - 1)
        self.assertEqual(self.world.reads, 8)

    def test_a_state_change_with_the_same_type_is_reported(self):
        self.world.blocks[(0, 64, 0)] = "minecraft:wooden_door"
        self.source.apply_interest(interest((0, 64, 0)))
        self.run_ticks(POLL_TICKS)  # the first read is the baseline
        self.world.blocks[(0, 64, 0)] = "minecraft:wooden_door[open]"
        self.run_ticks(POLL_TICKS)
        self.assertEqual([(item["from"], item["to"]) for item in self.items()],
                         [("minecraft:wooden_door", "minecraft:wooden_door")])

    def test_player_actions_are_confirmed_on_the_next_tick_and_name_only_bots(self):
        self.source.apply_interest(interest((0, 64, 0), (1, 64, 0), (2, 64, 0)))
        self.run_ticks(POLL_TICKS)
        bot = SimpleNamespace(name="Miner", xuid="")
        human = SimpleNamespace(name="Alice", xuid="2535400000000000")
        self.world.blocks[(0, 64, 0)] = "minecraft:dirt"
        self.source.player_changed(self.world.get_block_at(0, 64, 0), bot, "player-place")
        self.world.blocks[(1, 64, 0)] = "minecraft:dirt"
        self.source.player_changed(self.world.get_block_at(1, 64, 0), human, "player-place")
        # A cancelled or rewritten action leaves the block as it was: nothing is reported.
        self.source.player_changed(self.world.get_block_at(2, 64, 0), bot, "player-break")
        self.source.player_changed(self.world.get_block_at(9, 64, 9), bot, "player-break")  # not watched
        self.run_ticks(1)
        self.assertEqual([(item["x"], item["cause"], item.get("bot")) for item in self.items()],
                         [(0, "player-place", "Miner"), (1, "player-place", None)])
        self.assertNotIn("Alice", repr(self.reports))
        self.run_ticks(POLL_TICKS)
        self.assertEqual(len(self.items()), 2)  # the poll does not report a confirmed change again

    def test_unloaded_chunks_are_skipped_and_a_difference_is_reported_late(self):
        self.world.blocks[(20, 64, 0)] = "minecraft:stone"
        self.source.apply_interest(interest((20, 64, 0)))  # chunk (1, 0) is not loaded: no baseline yet
        self.world.chunks.add((1, 0))
        self.run_ticks(POLL_TICKS)  # the first loaded read is the baseline
        self.world.chunks.discard((1, 0))
        self.world.blocks[(20, 64, 0)] = "minecraft:dirt"
        self.run_ticks(POLL_TICKS * 2)
        self.assertEqual(self.reports, [])
        self.world.chunks.add((1, 0))
        self.run_ticks(POLL_TICKS)
        self.assertEqual([(item["from"], item["to"]) for item in self.items()],
                         [("minecraft:stone", "minecraft:dirt")])

    def test_interest_is_replaced_whole_and_ignores_malformed_or_unknown_entries(self):
        self.source.apply_interest(interest((0, 64, 0), (1, 64, 0)))
        self.source.apply_interest({"revision": 2, "kinds": [KIND], "block": [
            {"dimension": "overworld", "cells": [[1, 64, 0], [1.5, 64, 0], "x", [1, 2]]},
            {"dimension": "nether", "cells": [[0, 64, 0]]},
            {"dimension": "mars", "cells": [[0, 0, 0]]},
        ]})
        self.assertEqual(self.source.watched, 2)
        self.source.apply_interest(interest((0, 64, 0), kinds=()))
        self.assertEqual(self.source.watched, 0)
        self.world.blocks[(0, 64, 0)] = "minecraft:stone"
        self.run_ticks(POLL_TICKS)
        self.assertEqual(self.reports, [])

    def test_a_large_new_set_is_read_a_quarter_per_tick_and_a_resent_set_changes_nothing(self):
        cells = [(x, 64, 0) for x in range(8)]
        self.source.apply_interest(interest(*cells))
        self.assertEqual(self.world.reads, 0)  # no read on the tick the set arrives
        self.run_ticks(1)
        self.assertEqual(self.world.reads, 2)
        self.run_ticks(POLL_TICKS - 1)
        self.world.blocks[(0, 64, 0)] = "minecraft:stone"
        self.source.apply_interest(interest(*cells))  # a relink resends the same set: baselines kept
        self.run_ticks(POLL_TICKS)
        self.assertEqual([(item["x"], item["to"]) for item in self.items()], [(0, "minecraft:stone")])

    def test_a_player_action_before_the_first_read_is_reported_from_what_the_event_saw(self):
        self.source.apply_interest(interest((5, 64, 0)))
        bot = SimpleNamespace(name="Miner", xuid="")
        self.world.blocks[(5, 64, 0)] = "minecraft:stone"
        block = self.world.get_block_at(5, 64, 0)  # the event's block, before the break
        self.world.blocks[(5, 64, 0)] = "minecraft:air"
        self.source.player_changed(block, bot, "player-break")
        self.run_ticks(1)
        self.assertEqual([(item["from"], item["to"], item["cause"]) for item in self.items()],
                         [("minecraft:stone", "minecraft:air", "player-break")])

    def test_ticks_without_reads_do_not_scan_chunks(self):
        scans = []
        world = self.world
        original = type(world).loaded_chunks
        type(world).loaded_chunks = property(lambda dimension: scans.append(1) or original.fget(dimension))
        try:
            self.source.apply_interest(interest((0, 64, 0)))  # one cell: three of four ticks read nothing
            self.run_ticks(POLL_TICKS)
        finally:
            type(world).loaded_chunks = original
        self.assertEqual(len(scans), 1)

    def test_the_nether_is_polled_with_its_own_dimension(self):
        nether = self.server.dimensions["Nether"]
        self.source.apply_interest(interest((0, 64, 0), dimension="nether"))
        self.run_ticks(POLL_TICKS)
        nether.blocks[(0, 64, 0)] = "minecraft:stone"
        self.run_ticks(POLL_TICKS)
        self.assertEqual([item["dimension"] for item in self.items()], ["nether"])

    def test_a_bots_death_is_reported_only_with_interest_and_only_without_an_xuid(self):
        self.source.apply_interest(interest(kinds=(KIND, DEATH_KIND)))
        bot = SimpleNamespace(name="Miner", xuid="")
        human = SimpleNamespace(name="Alice", xuid="2535400000000000")
        self.source.player_died(bot, "death.attack.generic")
        self.source.player_died(human, "death.attack.generic")
        self.run_ticks(1)
        self.assertEqual(self.items(), [{"kind": DEATH_KIND, "bot": "Miner", "message": "death.attack.generic"}])
        self.assertNotIn("Alice", repr(self.reports))

    def test_a_death_is_reported_without_block_interest_on_the_same_tick_report(self):
        self.source.apply_interest({"revision": 1, "block": [], "kinds": [DEATH_KIND]})
        self.source.player_died(SimpleNamespace(name="Miner", xuid=""), "death.attack.drown")
        self.run_ticks(1)
        self.assertEqual(self.reports, [(1, [{"kind": DEATH_KIND, "bot": "Miner",
                                              "message": "death.attack.drown"}], 0)])

    def test_a_death_is_dropped_when_interest_no_longer_names_it(self):
        self.source.apply_interest(interest(kinds=(KIND, DEATH_KIND)))
        self.source.player_died(SimpleNamespace(name="Miner", xuid=""), "death.attack.generic")
        self.source.apply_interest(interest(kinds=(KIND,)))
        self.run_ticks(1)
        self.assertEqual(self.reports, [])



class PresenceTests(unittest.TestCase):
    """Step 6a: human joins, leaves and public chat, only while endbotd asks for them."""

    def setUp(self):
        self.reports = []
        self.source = BlockChangeSource(FakeServer(), lambda tick, items, dropped: self.reports.append(items))
        self.alice = SimpleNamespace(name="Alice", xuid="2535400000000001")
        self.bot = SimpleNamespace(name="Miner", xuid="")

    def items(self):
        return [item for items in self.reports for item in items]

    def test_only_kinds_in_the_interest_set_and_only_humans_are_reported(self):
        self.source.player_presence("player.join", self.alice)
        self.source.on_tick()
        self.assertEqual(self.reports, [])
        self.source.apply_interest({"revision": 1, "block": [], "kinds": ["player.join", "player.chat"]})
        self.source.player_presence("player.join", self.alice)
        self.source.player_presence("player.join", self.bot)
        self.source.player_presence("player.leave", self.alice)  # not asked for
        self.source.player_presence("player.chat", self.alice, "hi\x1b[31m \u00a7cthere" + "x" * 400)
        self.source.on_tick()
        items = self.items()
        self.assertEqual(items[0], {"kind": "player.join", "player": "Alice"})
        self.assertEqual([item["kind"] for item in items], ["player.join", "player.chat"])
        self.assertEqual(len(items[1]["message"]), 256)
        self.assertNotIn("\x1b", items[1]["message"])
        self.assertNotIn("\u00a7", items[1]["message"])


if __name__ == "__main__":
    unittest.main()
