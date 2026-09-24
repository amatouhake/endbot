"""Tests keeping the bundled lock copy in sync and BDS version comparison."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from endbot_cli.lock import (
    LockError,
    bds_versions_match,
    compare_bds_versions,
    load_lock,
    normalize_bds_version,
    packaged_lock_path,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
REPO_LOCK = REPO_ROOT / "endstone.lock"


class LockSyncTests(unittest.TestCase):
    def test_packaged_lock_matches_the_repository_lock(self) -> None:
        self.assertTrue(
            REPO_LOCK.is_file(),
            f"{REPO_LOCK} is missing; the CLI lock copy cannot be validated from this checkout",
        )
        packaged = json.loads(packaged_lock_path().read_text(encoding="utf-8"))
        repository = json.loads(REPO_LOCK.read_text(encoding="utf-8"))
        self.assertEqual(
            packaged,
            repository,
            "src/endbot_cli/endstone.lock.json has drifted from endstone.lock; regenerate the copy",
        )

    def test_load_lock_reads_the_locked_versions(self) -> None:
        repository = json.loads(REPO_LOCK.read_text(encoding="utf-8"))
        lock = load_lock()
        self.assertEqual(lock.bds_version, repository["bds"]["version"])
        self.assertEqual(lock.endstone_package_version, repository["endstone"]["package_version"])


class VersionComparisonTests(unittest.TestCase):
    def test_normalize_strips_a_leading_one(self) -> None:
        self.assertEqual(normalize_bds_version("1.26.51.1"), (26, 51, 1))
        self.assertEqual(normalize_bds_version("1.26.51"), (26, 51))
        self.assertEqual(normalize_bds_version("26.51"), (26, 51))
        self.assertEqual(normalize_bds_version("1.2"), (1, 2))

    def test_normalize_rejects_non_versions(self) -> None:
        for value in ("abc", "1.2.x", ""):
            with self.subTest(value=value), self.assertRaises(LockError):
                normalize_bds_version(value)

    def test_versions_match_on_first_three_components(self) -> None:
        self.assertTrue(bds_versions_match("26.51", "1.26.51.1"))
        self.assertTrue(bds_versions_match("1.26.51.1", "26.51.1"))

    def test_compare_orders_versions(self) -> None:
        self.assertEqual(compare_bds_versions("26.51", "1.26.51.1"), 0)
        self.assertEqual(compare_bds_versions("26.51.1", "1.26.51.1"), 0)
        self.assertEqual(compare_bds_versions("26.50", "1.26.51.1"), -1)
        self.assertEqual(compare_bds_versions("1.21.51", "1.26.51.1"), -1)
        self.assertEqual(compare_bds_versions("26.99", "1.26.51.1"), 1)
        self.assertEqual(compare_bds_versions("26.51.2", "1.26.51.1"), 1)
        self.assertTrue(bds_versions_match("26.51.1", "26.51"))
        self.assertFalse(bds_versions_match("26.52", "1.26.51.1"))
        self.assertFalse(bds_versions_match("26.5", "1.26.51.1"))
        self.assertFalse(bds_versions_match("26.51.2", "1.26.51.1"))
        self.assertFalse(bds_versions_match("1.26.51.1", "1.26.52.1"))


if __name__ == "__main__":
    unittest.main()
