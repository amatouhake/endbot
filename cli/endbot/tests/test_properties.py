"""Tests for the server.properties parser and safety-invariant check."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from endbot_cli.properties import PreflightError, parse_properties, verify_server_properties

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


if __name__ == "__main__":
    unittest.main()
