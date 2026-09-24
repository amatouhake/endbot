"""Tests for the relocated-interpreter Endstone entry point."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from endbot_cli.endstone_entry import relocated_libdir, shared_library_name


class RelocatedLibdirTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.library = shared_library_name()

    def test_stale_build_time_libdir_is_repointed_at_the_interpreter(self) -> None:
        (self.root / "python" / "lib").mkdir(parents=True)
        (self.root / "python" / "lib" / self.library).write_bytes(b"")
        replacement = relocated_libdir("/install/lib", str(self.root / "python"), self.library)
        self.assertEqual(replacement, str(self.root / "python" / "lib"))

    def test_working_libdir_is_left_alone(self) -> None:
        (self.root / "system").mkdir()
        (self.root / "system" / self.library).write_bytes(b"")
        self.assertIsNone(relocated_libdir(str(self.root / "system"), str(self.root / "python"), self.library))

    def test_nothing_changes_when_no_shared_library_exists(self) -> None:
        self.assertIsNone(relocated_libdir("/install/lib", str(self.root / "python"), self.library))
        self.assertIsNone(relocated_libdir(None, str(self.root / "python"), self.library))


if __name__ == "__main__":
    unittest.main()
