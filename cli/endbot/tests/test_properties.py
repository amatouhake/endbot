"""Tests for the server.properties parser, safety-invariant check, and editor."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from endbot_cli.properties import PreflightError, edit_properties, parse_properties, verify_server_properties

SAFE = "online-mode=true\nallow-cheats=false\nlevel-name=world\n"


class PropertiesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "server.properties"

    def test_safe_properties_pass(self) -> None:
        self.path.write_text(SAFE, encoding="utf-8")
        messages = verify_server_properties(self.path)
        self.assertEqual(messages, ["PASS online-mode=true", "PASS allow-cheats=false"])
        self.assertEqual(parse_properties(self.path)["level-name"], "world")

    def test_unsafe_properties_fail(self) -> None:
        self.path.write_text("online-mode=false\nallow-cheats=true\n", encoding="utf-8")
        with self.assertRaisesRegex(PreflightError, "online-mode"):
            verify_server_properties(self.path)

    def test_missing_property_fails(self) -> None:
        self.path.write_text("online-mode=true\n", encoding="utf-8")
        with self.assertRaisesRegex(PreflightError, "allow-cheats"):
            verify_server_properties(self.path)

    def test_malformed_lines_fail(self) -> None:
        self.path.write_text("online-mode=true\nallow-cheats\n", encoding="utf-8")
        with self.assertRaisesRegex(PreflightError, "expected key=value"):
            parse_properties(self.path)

    def test_duplicate_property_fails(self) -> None:
        self.path.write_text("online-mode=true\nonline-mode=false\nallow-cheats=false\n", encoding="utf-8")
        with self.assertRaisesRegex(PreflightError, "duplicate"):
            parse_properties(self.path)


class EditPropertiesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "server.properties"

    def test_edit_preserves_everything_but_the_touched_values(self) -> None:
        original = (
            "# server.properties kept by hand\n"
            "\n"
            "online-mode=false\n"
            "! legacy comment\n"
            "allow-cheats = true\n"
            "weird line without equals\n"
            "level-name=world\n"
            "max-players = 10\n"
        )
        self.path.write_text(original, encoding="utf-8")
        messages = edit_properties(self.path, {"online-mode": "true", "allow-cheats": "false"})
        self.assertEqual(
            self.path.read_text(encoding="utf-8"),
            (
                "# server.properties kept by hand\n"
                "\n"
                "online-mode=true\n"
                "! legacy comment\n"
                "allow-cheats = false\n"
                "weird line without equals\n"
                "level-name=world\n"
                "max-players = 10\n"
            ),
        )
        self.assertEqual(len(messages), 2)
        self.assertIn("online-mode=true", messages[0])
        self.assertIn("was 'false'", messages[0])

    def test_edit_preserves_crlf_line_endings(self) -> None:
        self.path.write_bytes(b"# header\r\nonline-mode=false\r\nallow-cheats=false\r\n")
        edit_properties(self.path, {"online-mode": "true"})
        self.assertEqual(self.path.read_bytes(), b"# header\r\nonline-mode=true\r\nallow-cheats=false\r\n")

    def test_missing_keys_are_appended_and_reported(self) -> None:
        self.path.write_text("level-name=world\n", encoding="utf-8")
        messages = edit_properties(self.path, {"online-mode": "true", "allow-cheats": "false"})
        self.assertEqual(
            self.path.read_text(encoding="utf-8"),
            "level-name=world\nonline-mode=true\nallow-cheats=false\n",
        )
        self.assertIn("added online-mode=true (was missing)", messages)

    def test_correct_values_report_already_set_and_do_not_rewrite(self) -> None:
        self.path.write_text("online-mode=true\nallow-cheats=false\n", encoding="utf-8")
        before = self.path.stat().st_mtime_ns
        messages = edit_properties(self.path, {"online-mode": "true"})
        self.assertEqual(messages, ["online-mode=true already set"])
        self.assertEqual(self.path.stat().st_mtime_ns, before)

    def test_missing_file_is_created_with_just_the_requested_keys(self) -> None:
        messages = edit_properties(self.path, {"online-mode": "true", "allow-cheats": "false"})
        self.assertEqual(self.path.read_text(encoding="utf-8"), "online-mode=true\nallow-cheats=false\n")
        self.assertEqual(len(messages), 2)


if __name__ == "__main__":
    unittest.main()
