"""The generated block table matches the locked BDS and the pinned minecraft-data (BOT-028, BOT-030)."""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugin/endbot/src"))

from endstone_endbot import block_properties


class BlockPropertiesTests(unittest.TestCase):
    def test_the_table_names_the_locked_bds_and_the_pinned_minecraft_data(self):
        # Regenerate with scripts/generate_block_properties.py when either moves.
        bds = json.loads((ROOT / "endstone.lock").read_text(encoding="utf-8"))["bds"]["version"]
        self.assertEqual(block_properties.BEDROCK_DATA_VERSION, ".".join(bds.split(".")[:3]))
        lock = json.loads((ROOT / "runtime/package-lock.json").read_text(encoding="utf-8"))
        self.assertEqual(block_properties.MINECRAFT_DATA_VERSION,
                         lock["packages"]["node_modules/minecraft-data"]["version"])

    def test_representative_blocks_fall_on_the_agreed_side(self):
        see, through, unbreakable = (block_properties.SEE_THROUGH, block_properties.NO_COLLISION,
                                     block_properties.UNBREAKABLE)
        for name in ("oak_leaves", "glass", "ice", "oak_slab", "oak_stairs", "oak_fence", "glass_pane", "iron_bars",
                     "cobblestone_wall", "short_grass", "torch"):
            self.assertIn(f"minecraft:{name}", see, name)
        for name in ("stone", "oak_log", "dirt", "diamond_ore", "bedrock"):
            self.assertNotIn(f"minecraft:{name}", see, name)
        for name in ("short_grass", "tall_grass", "torch", "snow_layer", "redstone_wire", "lever"):
            self.assertIn(f"minecraft:{name}", through, name)
        for name in ("oak_leaves", "glass", "oak_slab", "oak_fence", "stone"):
            self.assertNotIn(f"minecraft:{name}", through, name)
        for name in ("bedrock", "barrier", "end_portal_frame", "command_block"):
            self.assertIn(f"minecraft:{name}", unbreakable, name)
        self.assertNotIn("minecraft:obsidian", unbreakable)


if __name__ == "__main__":
    unittest.main()
