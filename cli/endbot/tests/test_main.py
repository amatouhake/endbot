"""Tests for the `endbot` command-line entry point."""

from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from fakeinstance import build_instance

from endbot_cli.__main__ import PLACEHOLDER_COMMANDS, main
from endbot_cli.doctor import detect_endstone_version
from endbot_cli.lock import load_lock


class MainTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.lock = load_lock()
        self.paths = build_instance(self.root, self.lock)

    def run_main(self, argv: list[str]) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(argv)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_doctor_prints_one_line_per_check(self) -> None:
        code, stdout, stderr = self.run_main(["--instance", str(self.root), "doctor"])
        # The endstone-version result depends on the environment it runs in.
        expected_code = 0 if detect_endstone_version() == self.lock.endstone_package_version else 1
        self.assertEqual(code, expected_code)
        self.assertEqual(stderr, "")
        lines = stdout.splitlines()
        self.assertIn("config", lines[0])
        for line in lines:
            self.assertRegex(line, r"^(PASS|WARN|FAIL|SKIP) [a-z-]+: .+")

    def test_placeholder_commands_exit_two(self) -> None:
        for name in PLACEHOLDER_COMMANDS:
            with self.subTest(name=name):
                code, stdout, stderr = self.run_main(["--instance", str(self.root), name])
                self.assertEqual(code, 2)
                self.assertEqual(stdout, "")
                self.assertIn("not implemented yet", stderr)

    def test_missing_instance_directory_fails(self) -> None:
        missing = self.root / "missing"
        code, stdout, stderr = self.run_main(["--instance", str(missing), "doctor"])
        self.assertEqual(code, 1)
        self.assertEqual(stdout, "")
        self.assertIn("--instance", stderr)

    def test_live_flag_is_accepted(self) -> None:
        code, stdout, _ = self.run_main(["--instance", str(self.root), "doctor", "--live"])
        self.assertIn("runtime", stdout)
        self.assertIn(code, (0, 1))


if __name__ == "__main__":
    unittest.main()
