"""Tests for state/controllers.json loading (docs/OPERATIONS.md section 3 schema)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from endbot_cli.controllers import CONTROLLERS_VERSION, ControllersError, load_controllers


class ControllersTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "controllers.json"

    def write(self, document: object) -> None:
        self.path.write_text(json.dumps(document), encoding="utf-8")

    def test_missing_file_means_everything_pending(self) -> None:
        state = load_controllers(self.path)
        self.assertEqual(state.version, CONTROLLERS_VERSION)
        self.assertEqual(state.bindings, ())
        self.assertIsNone(state.find("ExampleTag"))

    def test_bindings_are_found_case_insensitively(self) -> None:
        self.write(
            {
                "version": 1,
                "bindings": [
                    {
                        "gamertag": "ExampleTag",
                        "xuid": "2535412345678901",
                        "uuid": "u",
                        "boundAt": "2026-09-19T00:00:00Z",
                    }
                ],
            }
        )
        state = load_controllers(self.path)
        binding = state.find("exampletag")
        self.assertIsNotNone(binding)
        self.assertEqual(binding.xuid, "2535412345678901")
        self.assertEqual(binding.bound_at, "2026-09-19T00:00:00Z")

    def test_corrupt_json_fails_closed(self) -> None:
        self.path.write_text("{not json", encoding="utf-8")
        with self.assertRaisesRegex(ControllersError, "invalid JSON"):
            load_controllers(self.path)

    def test_wrong_version_is_named(self) -> None:
        self.write({"version": 2, "bindings": []})
        with self.assertRaisesRegex(ControllersError, "'version'"):
            load_controllers(self.path)

    def test_unknown_key_is_named(self) -> None:
        self.write({"version": 1, "bindings": [], "extra": []})
        with self.assertRaisesRegex(ControllersError, "'extra'"):
            load_controllers(self.path)

    def test_unknown_binding_key_is_named(self) -> None:
        self.write(
            {"version": 1, "bindings": [{"gamertag": "Tag", "xuid": "1", "uuid": "u", "boundAt": "t", "extra": 1}]}
        )
        with self.assertRaisesRegex(ControllersError, "bindings\\[0\\].extra"):
            load_controllers(self.path)

    def test_missing_binding_key_is_named(self) -> None:
        self.write({"version": 1, "bindings": [{"gamertag": "Tag"}]})
        with self.assertRaisesRegex(ControllersError, "bindings\\[0\\].xuid"):
            load_controllers(self.path)

    def test_non_string_binding_value_is_named(self) -> None:
        self.write({"version": 1, "bindings": [{"gamertag": "Tag", "xuid": 1, "uuid": "u", "boundAt": "t"}]})
        with self.assertRaisesRegex(ControllersError, "bindings\\[0\\].xuid"):
            load_controllers(self.path)


if __name__ == "__main__":
    unittest.main()
