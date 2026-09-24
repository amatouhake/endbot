"""Tests for the read-only Bedrock level.dat parser (synthetic NBT only)."""

from __future__ import annotations

import unittest

import fakenbt

from endbot_cli.leveldat import LevelDatError, parse_level_dat

EXPERIMENTS = fakenbt.compound(
    {
        "experiments_ever_used": fakenbt.byte(0),
        "saved_with_toggled_experiments": fakenbt.byte(0),
    }
)


class LevelDatTests(unittest.TestCase):
    def test_decodes_history_flags(self) -> None:
        data = fakenbt.level_dat(
            {
                "commandsEnabled": fakenbt.byte(0),
                "hasBeenLoadedInCreative": fakenbt.byte(0),
                "cheatsEnabled": fakenbt.byte(0),
                "GameType": fakenbt.int32(0),
                "experiments": EXPERIMENTS,
                "other": fakenbt.string("ignored"),
                "array": fakenbt.long_array([1, 2, 3]),
                "blob": fakenbt.byte_array(b"\x01\x02"),
                "list": fakenbt.list_tag(fakenbt.TAG_INT, [fakenbt.int32(7).payload]),
            }
        )
        summary = parse_level_dat(data)
        self.assertEqual(summary.storage_version, 10)
        self.assertFalse(summary.commands_enabled)
        self.assertFalse(summary.has_been_loaded_in_creative)
        self.assertFalse(summary.cheats_enabled)
        self.assertEqual(summary.game_type, 0)
        self.assertFalse(summary.experiments_ever_used)
        self.assertFalse(summary.saved_with_toggled_experiments)
        self.assertEqual(summary.other_experiment_keys, ())
        self.assertEqual(summary.unsafe_flags(), ())

    def test_truthy_flags_are_reported(self) -> None:
        data = fakenbt.level_dat(
            {
                "commandsEnabled": fakenbt.byte(1),
                "hasBeenLoadedInCreative": fakenbt.byte(1),
                "experiments": fakenbt.compound({"experiments_ever_used": fakenbt.byte(1)}),
            }
        )
        summary = parse_level_dat(data)
        self.assertEqual(
            summary.unsafe_flags(),
            ("hasBeenLoadedInCreative", "commandsEnabled", "experiments_ever_used"),
        )

    def test_missing_tags_decode_to_none(self) -> None:
        summary = parse_level_dat(fakenbt.level_dat({}))
        self.assertIsNone(summary.commands_enabled)
        self.assertIsNone(summary.has_been_loaded_in_creative)
        self.assertIsNone(summary.game_type)
        self.assertIsNone(summary.cheats_enabled)
        self.assertIsNone(summary.experiments_ever_used)
        self.assertIsNone(summary.saved_with_toggled_experiments)
        self.assertEqual(summary.unsafe_flags(), ())

    def test_other_experiment_keys_are_reported(self) -> None:
        data = fakenbt.level_dat(
            {
                "experiments": fakenbt.compound(
                    {
                        "experiments_ever_used": fakenbt.byte(0),
                        "saved_with_toggled_experiments": fakenbt.byte(0),
                        "crawling_experimental": fakenbt.byte(1),
                        "data_driven_biomes": fakenbt.byte(0),
                    }
                )
            }
        )
        summary = parse_level_dat(data)
        self.assertEqual(summary.other_experiment_keys, ("crawling_experimental", "data_driven_biomes"))

    def test_root_level_experiment_flags_still_fail_closed(self) -> None:
        data = fakenbt.level_dat(
            {
                "experiments_ever_used": fakenbt.byte(1),
                "experiments": fakenbt.compound({}),
            }
        )
        summary = parse_level_dat(data)
        self.assertTrue(summary.experiments_ever_used)

    def test_data_compound_fallback(self) -> None:
        data = fakenbt.level_dat({"Data": fakenbt.compound({"GameType": fakenbt.int32(1)})})
        summary = parse_level_dat(data)
        self.assertEqual(summary.game_type, 1)

    def test_truncated_header_is_a_clear_error(self) -> None:
        with self.assertRaisesRegex(LevelDatError, "header is incomplete"):
            parse_level_dat(b"\x0a\x00")

    def test_truncated_payload_is_a_clear_error(self) -> None:
        full = fakenbt.level_dat({"commandsEnabled": fakenbt.byte(0)})
        with self.assertRaisesRegex(LevelDatError, "declares"):
            parse_level_dat(full[:-3])
        with self.assertRaisesRegex(LevelDatError, "declares"):
            parse_level_dat(full + b"junk")

    def test_corrupt_payload_is_a_clear_error(self) -> None:
        garbage = b"\x01\x00\x00\x00\x04\x00\x00\x00\xff\xff\xff\xff"
        with self.assertRaises(LevelDatError):
            parse_level_dat(garbage)

    def test_truncated_compound_is_a_clear_error(self) -> None:
        full = fakenbt.level_dat({"commandsEnabled": fakenbt.byte(1), "GameType": fakenbt.int32(0)})
        body = full[8:-2]  # drop the compound terminator and part of the trailing int payload
        corrupt = (10).to_bytes(4, "little", signed=True) + len(body).to_bytes(4, "little", signed=True) + body
        with self.assertRaisesRegex(LevelDatError, "corrupt or truncated"):
            parse_level_dat(corrupt)

    def test_non_compound_root_is_a_clear_error(self) -> None:
        payload = fakenbt.named("", fakenbt.int32(3))
        corrupt = (10).to_bytes(4, "little", signed=True) + len(payload).to_bytes(4, "little", signed=True) + payload
        with self.assertRaisesRegex(LevelDatError, "root tag must be a compound"):
            parse_level_dat(corrupt)


if __name__ == "__main__":
    unittest.main()
