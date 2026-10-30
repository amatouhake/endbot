"""Tests for the pure helpers in ``scripts/e2e_store_migration.py``."""

from __future__ import annotations

import importlib.util
import io
import json
import shutil
import sys
import tarfile
import unittest
import uuid
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
MODULE_NAME = "e2e_store_migration_under_test"
_MODULE = None


def _load_module():
    global _MODULE
    if _MODULE is None:
        sys.path.insert(0, str(SCRIPTS))
        try:
            spec = importlib.util.spec_from_file_location(MODULE_NAME, SCRIPTS / "e2e_store_migration.py")
            assert spec is not None and spec.loader is not None
            module = importlib.util.module_from_spec(spec)
            sys.modules[MODULE_NAME] = module
            spec.loader.exec_module(module)
            _MODULE = module
        finally:
            sys.path.remove(str(SCRIPTS))
    return _MODULE


def _write_json(path: Path, document: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document), encoding="utf-8")


class _TemporaryDirectory:
    """A workspace temp directory created without tempfile's restrictive permissions."""

    def __init__(self) -> None:
        root = ROOT / "build" / "test-e2e-store-migration"
        root.mkdir(parents=True, exist_ok=True)
        self.name = str(root / uuid.uuid4().hex)
        Path(self.name).mkdir()

    def cleanup(self) -> None:
        shutil.rmtree(self.name, ignore_errors=True)

    def __enter__(self) -> str:
        return self.name

    def __exit__(self, *_exc) -> None:
        self.cleanup()


def _temporary_directory() -> _TemporaryDirectory:
    # The sandbox denies tempfile's mode-0700 directories; build/ is ignored and writable.
    return _TemporaryDirectory()


def _groups(*members: str) -> dict[str, object]:
    return {
        "version": 1,
        "groups": [
            {
                "identityId": "00000000-0000-0000-0000-000000000000",
                "name": "migration",
                "members": list(members),
            }
        ],
    }


def _scripts(source: str) -> dict[str, object]:
    return {
        "version": 2,
        "scripts": [
            {
                "name": "migration_saved",
                "source": source,
                "revision": "r1",
                "owner": "console",
                "ownerName": "console",
                "visibility": "private",
                "savedAt": 0,
                "history": [],
            }
        ],
    }


class BundleVersionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = _load_module()

    def test_zip_bundle_version_uses_the_app_directory(self) -> None:
        with _temporary_directory() as directory:
            archive = Path(directory) / "endbot-0.4.0.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("endbot-0.4.0/app/0.4.0/python/python.exe", "")
                bundle.writestr("endbot-0.4.0/app/current", "0.4.0\n")
                bundle.writestr("endbot-0.4.0/endbot.cmd", "")
            self.assertEqual(self.module.bundle_version(archive), "0.4.0")

    def test_tar_bundle_version_uses_the_app_directory(self) -> None:
        with _temporary_directory() as directory:
            archive = Path(directory) / "endbot-0.5.0.tar.gz"
            with tarfile.open(archive, "w:gz") as bundle:
                for name in (
                    "endbot-0.5.0/app/0.5.0/python/bin/python3",
                    "endbot-0.5.0/app/current",
                    "endbot-0.5.0/endbot",
                ):
                    info = tarfile.TarInfo(name)
                    info.size = 0
                    bundle.addfile(info, io.BytesIO(b""))
            self.assertEqual(self.module.bundle_version(archive), "0.5.0")

    def test_bundle_version_refuses_an_ambiguous_archive(self) -> None:
        with _temporary_directory() as directory:
            archive = Path(directory) / "endbot.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("app/0.4.0/python.exe", "")
                bundle.writestr("app/0.5.0/python.exe", "")
            with self.assertRaises(self.module.ConsumerError):
                self.module.bundle_version(archive)

    def test_version_key_orders_numbers_not_strings(self) -> None:
        self.assertEqual(self.module.version_key("0.4.0"), (0, 4, 0))
        self.assertGreater(self.module.version_key("0.10.0"), self.module.version_key("0.9.0"))
        self.assertEqual(self.module.version_key("0.4.0-e2e-next"), self.module.version_key("0.4.0"))
        with self.assertRaises(self.module.ConsumerError):
            self.module.version_key("candidate")


class ArgumentParsingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = _load_module()

    def test_defaults_are_nethernet_and_a_named_bot(self) -> None:
        args = self.module.parse_args(["old.zip", "new.tar.gz"])
        self.assertEqual(args.old_archive, Path("old.zip"))
        self.assertEqual(args.new_archive, Path("new.tar.gz"))
        self.assertEqual(args.transport, "nethernet")
        self.assertEqual(args.bot, "MigrationBot")
        self.assertIsNone(args.work_dir)

    def test_explicit_options_are_parsed(self) -> None:
        args = self.module.parse_args(
            ["old.zip", "new.tar.gz", "--work-dir", "output", "--transport", "nethernet", "--bot", "Alice"]
        )
        self.assertEqual(args.work_dir, Path("output"))
        self.assertEqual(args.transport, "nethernet")
        self.assertEqual(args.bot, "Alice")


class ScriptingToggleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = _load_module()

    def test_flips_false_keeping_the_inline_comment(self) -> None:
        with _temporary_directory() as directory:
            path = Path(directory) / "endbot.toml"
            path.write_text(
                "[server]\npath = 'server'\n\n[scripting]\nenabled = false # opt in\n",
                encoding="utf-8",
            )
            self.module.enable_scripting(path)
            text = path.read_text(encoding="utf-8")
            self.assertIn("enabled = true # opt in", text)
            self.assertIn("[server]\npath = 'server'", text)
            self.module.enable_scripting(path)
            self.assertEqual(path.read_text(encoding="utf-8"), text)

    def test_appends_a_missing_table(self) -> None:
        with _temporary_directory() as directory:
            path = Path(directory) / "endbot.toml"
            path.write_text("[server]\npath = 'server'\n", encoding="utf-8")
            self.module.enable_scripting(path)
            self.assertIn("[scripting]\nenabled = true\n", path.read_text(encoding="utf-8"))

    def test_adds_the_key_to_an_existing_table(self) -> None:
        with _temporary_directory() as directory:
            path = Path(directory) / "endbot.toml"
            path.write_text("[scripting]\n# comment\n", encoding="utf-8")
            self.module.enable_scripting(path)
            self.assertEqual(
                path.read_text(encoding="utf-8"), "[scripting]\nenabled = true\n# comment\n"
            )


class StoreAssertionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = _load_module()

    def setUp(self) -> None:
        directory = _temporary_directory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.member = "00000000-0000-0000-0000-000000000001"

    def test_group_members_are_case_insensitive(self) -> None:
        path = self.root / "groups.json"
        _write_json(path, _groups(self.member))
        self.assertEqual(self.module.group_members(path, "MIGRATION"), (self.member,))
        self.module.assert_group_members(path, "migration", (self.member,))
        with self.assertRaises(self.module.ConsumerError):
            self.module.assert_group_members(path, "migration", ())

    def test_a_missing_group_is_an_error(self) -> None:
        path = self.root / "groups.json"
        _write_json(path, _groups())
        with self.assertRaises(self.module.ConsumerError):
            self.module.group_members(path, "other")

    def test_script_source_assertion(self) -> None:
        path = self.root / "scripts.json"
        _write_json(path, _scripts("original"))
        self.assertEqual(self.module.script_source(path, "Migration_Saved"), "original")
        self.module.assert_script_source(path, "migration_saved", "original")
        with self.assertRaises(self.module.ConsumerError):
            self.module.assert_script_source(path, "migration_saved", "changed")

    def test_text_marker_and_clean_exit_assertions(self) -> None:
        text = self.root / "README.txt"
        text.write_text("moved\n", encoding="utf-8")
        self.module.assert_text(text, "moved\n", "readme")
        with self.assertRaises(self.module.ConsumerError):
            self.module.assert_text(text, "different", "readme")

        marker = self.root / "migrations.json"
        _write_json(marker, {"stores": 2})
        self.module.assert_marker(marker, {"stores": 2})
        with self.assertRaises(self.module.ConsumerError):
            self.module.assert_marker(marker, {"stores": 1})

        exit_record = self.root / "state" / "run" / "last-exit.json"
        _write_json(exit_record, {"runtimeExit": 0, "serverExit": 0, "unclean": False})
        self.module.assert_clean_exit(self.root, endbotd=False)
        with self.assertRaises(self.module.ConsumerError):
            self.module.assert_clean_exit(self.root, endbotd=True)
        _write_json(
            exit_record,
            {"runtimeExit": 0, "endbotdExit": 0, "serverExit": 0, "unclean": False},
        )
        self.module.assert_clean_exit(self.root, endbotd=True)
        _write_json(
            exit_record,
            {"runtimeExit": 0, "endbotdExit": 1, "serverExit": 0, "unclean": False},
        )
        with self.assertRaises(self.module.ConsumerError):
            self.module.assert_clean_exit(self.root, endbotd=True)

    def test_migration_and_rollback_assertions(self) -> None:
        instance = self.root / "endbot-0.4.0"
        data = instance / "server" / "plugins" / "endbot"
        state = instance / "state"
        state.mkdir(parents=True)
        (data / "scripts").mkdir(parents=True)
        (instance / "scripts").mkdir()
        _write_json(state / "migrations.json", {"stores": 2})
        _write_json(state / "groups.json", _groups(self.member))
        _write_json(state / "scripts.json", _scripts("original"))
        _write_json(data / "groups.json", _groups(self.member))
        _write_json(data / "scripts.json", _scripts("original"))
        (instance / "scripts" / "migration_file.luau").write_text("original\n", encoding="utf-8")
        (data / "scripts" / "README.txt").write_text("moved\n", encoding="utf-8")
        backup = instance / "backups" / "stores-update-20260101-000000"
        _write_json(backup / "plugin" / "groups.json", _groups(self.member))
        _write_json(backup / "plugin" / "scripts.json", _scripts("original"))
        (backup / "plugin-scripts").mkdir(parents=True)
        (backup / "plugin-scripts" / "migration_file.luau").write_text("original\n", encoding="utf-8")

        self.assertEqual(
            self.module.assert_migrated(
                instance, "migration", "migration_saved", "original", "migration_file", "original\n", self.member
            ),
            backup,
        )

        _write_json(state / "migrations.json", {"stores": 1})
        _write_json(data / "groups.json", _groups())
        _write_json(data / "scripts.json", _scripts("original"))
        (data / "scripts" / "README.txt").unlink()
        (data / "scripts" / "migration_file.luau").write_text("changed\n", encoding="utf-8")
        (instance / "scripts" / "migration_file.luau").unlink()
        (instance / "backups" / "stores-rollback-20260101-000001").mkdir(parents=True)
        self.module.assert_rolled_back(
            instance, "migration", "migration_saved", "original", "migration_file", "changed\n"
        )


class ReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = _load_module()

    def test_stages_record_ok_and_report_is_json(self) -> None:
        report = self.module.new_report()
        with self.module.stage(report, "first") as entry:
            entry["detail"] = "value"
        with self.assertRaises(RuntimeError), self.module.stage(report, "second"):
            raise RuntimeError("failure")
        self.assertEqual(
            report["stages"],
            [{"stage": "first", "ok": True, "detail": "value"}, {"stage": "second", "ok": False}],
        )
        self.assertFalse(report["ok"])
        with _temporary_directory() as directory:
            path = Path(directory) / "report.json"
            self.module.write_report(report, path)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["stages"], report["stages"])


if __name__ == "__main__":
    unittest.main()
