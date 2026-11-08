"""The plugin's block.change source (BOT-022 step 3b): poll phases, confirms, unloaded chunks, Bot attribution."""

import unittest
from types import SimpleNamespace

from endstone_endbot.block_source import KIND, POLL_TICKS, BlockChangeSource

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
        self.world.blocks[(0, 64, 0)] = "minecraft:wooden_door[open]"
        self.run_ticks(POLL_TICKS)
        self.assertEqual([(item["from"], item["to"]) for item in self.items()],
                         [("minecraft:wooden_door", "minecraft:wooden_door")])

    def test_player_actions_are_confirmed_on_the_next_tick_and_name_only_bots(self):
        self.source.apply_interest(interest((0, 64, 0), (1, 64, 0), (2, 64, 0)))
        self.run_ticks(2)
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

    def test_the_nether_is_polled_with_its_own_dimension(self):
        nether = self.server.dimensions["Nether"]
        self.source.apply_interest(interest((0, 64, 0), dimension="nether"))
        nether.blocks[(0, 64, 0)] = "minecraft:stone"
        self.run_ticks(POLL_TICKS)
        self.assertEqual([item["dimension"] for item in self.items()], ["nether"])


if __name__ == "__main__":
    unittest.main()
