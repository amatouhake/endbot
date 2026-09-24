"""Tests for section 6 backups and the SHA-256 manifest."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from fakeinstance import build_bds_dir

from endbot_cli.backup import collect_backup_entries, create_backup, describe_entries

NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


class BackupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.server = self.root / "server"
        self.backups = self.root / "backups"

    def test_manifest_lists_every_backed_up_file_with_sha256(self) -> None:
        build_bds_dir(self.server, worlds=True)
        (self.server / "allowlist.json").write_text("[]\n", encoding="utf-8")
        (self.server / "permissions.json").write_text("{}\n", encoding="utf-8")
        (self.server / "endstone.toml").write_text("[x]\n", encoding="utf-8")
        (self.server / "packetlimitconfig.json").write_text("{}\n", encoding="utf-8")

        result = create_backup(self.server, self.backups, worlds=False, reason="test", now=NOW)
        self.assertEqual(result.directory.name, "20260102-030405Z")
        manifest = json.loads(result.manifest.read_text(encoding="utf-8"))
        self.assertEqual(manifest["schema"], 1)
        self.assertEqual(manifest["created"], "2026-01-02T03:04:05Z")
        self.assertEqual(manifest["reason"], "test")
        self.assertFalse(manifest["worlds-included"])

        listed = {entry["path"]: entry for entry in manifest["files"]}
        expected = {
            "server.properties",
            "allowlist.json",
            "permissions.json",
            "endstone.toml",
            "packetlimitconfig.json",
            "behavior_packs/vanilla/contents.json",
            "resource_packs/vanilla/contents.json",
            "definitions/vanilla/contents.json",
            "bedrock_server",
        }
        self.assertEqual(set(listed), expected)
        self.assertEqual(set(result.files), expected)
        for relative, entry in listed.items():
            payload = (result.directory / relative).read_bytes()
            self.assertEqual(entry["sha256"], hashlib.sha256(payload).hexdigest(), relative)
            self.assertEqual(entry["bytes"], len(payload))
        self.assertNotIn("manifest.json", listed)

    def test_worlds_are_copied_only_on_request(self) -> None:
        build_bds_dir(self.server, worlds=True)
        without_worlds = create_backup(self.server, self.backups, worlds=False, now=NOW)
        self.assertFalse(any(name.startswith("worlds/") for name in without_worlds.files))
        with_worlds = create_backup(self.server, self.backups, worlds=True, now=NOW)
        self.assertIn("worlds/world/level.dat", with_worlds.files)
        manifest = json.loads(with_worlds.manifest.read_text(encoding="utf-8"))
        self.assertTrue(manifest["worlds-included"])

    def test_absent_entries_are_reported_not_invented(self) -> None:
        self.server.mkdir()
        (self.server / "server.properties").write_text("online-mode=true\n", encoding="utf-8")
        entries = collect_backup_entries(self.server)
        self.assertEqual(describe_entries(entries), "server.properties")
        result = create_backup(self.server, self.backups, worlds=True, now=NOW)
        self.assertEqual(result.files, ("server.properties",))
        self.assertIn("server executable", result.missing)
        self.assertIn("worlds", result.missing)
        self.assertIn("allowlist.json", result.missing)

    def test_same_second_backups_get_a_unique_suffix(self) -> None:
        build_bds_dir(self.server)
        first = create_backup(self.server, self.backups, now=NOW)
        second = create_backup(self.server, self.backups, now=NOW)
        self.assertNotEqual(first.directory, second.directory)
        self.assertTrue(second.directory.name.startswith("20260102-030405Z"))


if __name__ == "__main__":
    unittest.main()
