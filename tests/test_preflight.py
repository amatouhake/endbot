import tempfile
import unittest
from pathlib import Path

from scripts.preflight import PreflightError, parse_properties, verify_server_properties


class PreflightTests(unittest.TestCase):
    def write_properties(self, contents: str) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "server.properties"
        path.write_text(contents, encoding="utf-8")
        return path

    def test_accepts_required_safe_properties(self) -> None:
        path = self.write_properties("online-mode=true\nallow-cheats=false\n")
        self.assertEqual(
            verify_server_properties(path),
            ["PASS online-mode=true", "PASS allow-cheats=false"],
        )

    def test_rejects_authentication_or_cheat_weakening(self) -> None:
        for contents in (
            "online-mode=false\nallow-cheats=false\n",
            "online-mode=true\nallow-cheats=true\n",
        ):
            with self.subTest(contents=contents), self.assertRaises(PreflightError):
                verify_server_properties(self.write_properties(contents))

    def test_rejects_missing_and_ambiguous_values(self) -> None:
        with self.assertRaisesRegex(PreflightError, "missing"):
            verify_server_properties(self.write_properties("online-mode=true\n"))
        with self.assertRaisesRegex(PreflightError, "duplicate"):
            parse_properties(self.write_properties("online-mode=true\nonline-mode=true\n"))


if __name__ == "__main__":
    unittest.main()
