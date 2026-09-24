"""Tests for the `endbot` command-line entry point."""

from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from fakeinstance import build_instance

from endbot_cli.__main__ import main
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

    def test_doctor_before_the_first_start_warns_about_missing_secrets(self) -> None:
        self.paths.control_token.unlink(missing_ok=True)
        self.paths.owner_public_key.unlink(missing_ok=True)
        (self.paths.state_run / "last-exit.json").unlink(missing_ok=True)
        _code, stdout, _stderr = self.run_main(["--instance", str(self.root), "doctor"])
        self.assertIn("WARN control-token:", stdout)
        self.assertIn("expected before the first `endbot start`", stdout)
        self.assertNotIn("FAIL control-token:", stdout)

        self.paths.state_run.mkdir(parents=True, exist_ok=True)
        (self.paths.state_run / "last-exit.json").write_text("{}", encoding="utf-8")
        _code, stdout, _stderr = self.run_main(["--instance", str(self.root), "doctor"])
        self.assertIn("FAIL control-token:", stdout)

    def test_setup_is_wired_and_still_dry_run_only(self) -> None:
        empty = Path(self.directory.name) / "empty"
        empty.mkdir()
        code, stdout, stderr = self.run_main(
            ["--instance", str(empty), "setup", "--fresh", "--controller", "ExampleTag"]
        )
        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        self.assertIn("plan (fresh)", stdout)
        self.assertIn("download BDS", stdout)
        self.assertFalse((empty / "endbot.toml").exists())

    def test_setup_refuses_an_instance_that_already_exists(self) -> None:
        code, _stdout, stderr = self.run_main(["--instance", str(self.root), "setup", "--fresh"])
        self.assertEqual(code, 1)
        self.assertIn("endbot.toml already exists", stderr)

    def test_update_requires_exactly_one_of_archive_or_rollback(self) -> None:
        code, _stdout, stderr = self.run_main(["--instance", str(self.root), "update"])
        self.assertEqual(code, 2)
        self.assertIn("exactly one", stderr)
        code, _stdout, stderr = self.run_main(
            ["--instance", str(self.root), "update", "bundle.zip", "--rollback"]
        )
        self.assertEqual(code, 2)
        self.assertIn("exactly one", stderr)

    def test_update_rollback_is_wired(self) -> None:
        code, _stdout, stderr = self.run_main(["--instance", str(self.root), "update", "--rollback"])
        self.assertEqual(code, 1)
        self.assertIn("app", stderr)  # no previous version in this instance

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
