"""Tests for the store migration (docs/ENDBOTD.md P7, S1): update and rollback together, and the doctor check."""

from __future__ import annotations

import contextlib
import io
import json
import os
import unittest
from pathlib import Path
from unittest import mock

from test_update import FakeDoctor, UpdateTestCase

from endbot_cli import stores
from endbot_cli.doctor import FAIL, PASS, WARN, check_stores
from endbot_cli.stores import StoreError, StoreLayout
from endbot_cli.updatecmd import run_rollback


class StoreTestCase(UpdateTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.server = self.root / "server"
        self.layout = StoreLayout.for_instance(self.paths, self.server)
        plugin = self.layout.plugin_data
        (plugin / "scripts").mkdir(parents=True)
        (plugin / "groups.json").write_text('{"groups": ["old"]}\n', encoding="utf-8")
        (plugin / "scripts.json").write_text('{"scripts": ["old"]}\n', encoding="utf-8")
        (plugin / "scripts" / "dig.luau").write_text("-- dig v1\n", encoding="utf-8")
        (plugin / "scripts" / "notes.md").write_text("not a script\n", encoding="utf-8")

    def quiet(self, function, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = function(*args)
        return code, stdout.getvalue(), stderr.getvalue()

    def rollback(self):
        (self.root / "app" / "0.2.0").mkdir(exist_ok=True)
        os.utime(self.root / "app" / "0.1.0", (1_000_000_000, 1_000_000_000))
        self.paths.app_current.write_text("0.2.0\n", encoding="utf-8")
        return self.quiet(run_rollback, self.paths)

    def marker(self):
        return json.loads(self.layout.marker.read_text(encoding="utf-8"))

    def status(self, server: Path | None = None):
        return check_stores(self.paths, self.server if server is None else server)


class UpdateRollbackTests(StoreTestCase):
    def test_update_then_rollback_carries_changes_made_in_between(self) -> None:
        self.assertEqual(self.status().status, WARN)
        code, out, err = self.run_update(doctor=FakeDoctor())
        self.assertEqual(code, 0, err)
        self.assertEqual(self.paths.app_current.read_text(encoding="utf-8"), "0.2.0\n")
        self.assertEqual(self.marker(), {"stores": 2})
        self.assertIn("state/ is now authoritative", out)
        state = self.layout.state
        self.assertEqual((state / "groups.json").read_text(encoding="utf-8"), '{"groups": ["old"]}\n')
        # The originals stay, backed up; the script files move once (S1).
        self.assertTrue((self.layout.plugin_data / "groups.json").is_file())
        self.assertEqual((self.layout.scripts / "dig.luau").read_text(encoding="utf-8"), "-- dig v1\n")
        self.assertFalse((self.layout.plugin_scripts / "dig.luau").exists())
        self.assertTrue((self.layout.plugin_scripts / "notes.md").exists())
        self.assertIn(str(self.layout.scripts), (self.layout.plugin_scripts / "README.txt").read_text(encoding="utf-8"))
        backups = sorted(self.paths.backups.glob("stores-update-*"))
        self.assertEqual(len(backups), 1)
        self.assertTrue((backups[0] / "plugin" / "groups.json").is_file())
        self.assertTrue((backups[0] / "plugin-scripts" / "dig.luau").is_file())
        self.assertEqual(self.status().status, PASS)

        # endbotd changes the stores and the operator edits and adds script files.
        (state / "groups.json").write_text('{"groups": ["new"]}\n', encoding="utf-8")
        (state / "scripts.json").unlink()
        (self.layout.scripts / "dig.luau").write_text("-- dig v2\n", encoding="utf-8")
        (self.layout.scripts / "farm.luau").write_text("-- farm\n", encoding="utf-8")

        code, out, err = self.rollback()
        self.assertEqual(code, 0, err)
        self.assertEqual(self.paths.app_current.read_text(encoding="utf-8"), "0.1.0\n")
        self.assertEqual(self.marker(), {"stores": 1})
        plugin = self.layout.plugin_data
        self.assertEqual((plugin / "groups.json").read_text(encoding="utf-8"), '{"groups": ["new"]}\n')
        self.assertFalse((plugin / "scripts.json").exists())
        self.assertEqual((plugin / "scripts" / "dig.luau").read_text(encoding="utf-8"), "-- dig v2\n")
        self.assertTrue((plugin / "scripts" / "farm.luau").is_file())
        self.assertFalse((plugin / "scripts" / "README.txt").exists())
        self.assertEqual(stores.script_files(self.layout.scripts), [])
        rollback_backup = min(self.paths.backups.glob("stores-rollback-*"))
        self.assertTrue((rollback_backup / "plugin" / "scripts.json").is_file())  # the stale copy it removed
        self.assertEqual(self.status().status, WARN)

        # The next update carries the changes made under the old version forward again.
        (plugin / "groups.json").write_text('{"groups": ["older"]}\n', encoding="utf-8")
        self.assertTrue(any("stores: copied" in line for line in stores.migrate(self.layout)))
        self.assertEqual((state / "groups.json").read_text(encoding="utf-8"), '{"groups": ["older"]}\n')
        self.assertEqual(sorted(path.name for path in stores.script_files(self.layout.scripts)),
                         ["dig.luau", "farm.luau"])

    def test_failed_doctor_migrates_nothing(self) -> None:
        code, _out, _err = self.run_update(doctor=FakeDoctor((1, ["FAIL world: broken"])))
        self.assertEqual(code, 1)
        self.assertFalse(self.layout.marker.exists())
        self.assertTrue((self.layout.plugin_scripts / "dig.luau").exists())

    def test_reruns_change_nothing(self) -> None:
        def tree():
            return sorted(str(path.relative_to(self.root)) for path in self.root.rglob("*") if "app" not in path.parts)

        stores.migrate(self.layout)
        snapshot = tree()
        expected = [f"stores: state/ is already authoritative ({self.layout.marker})"]
        self.assertEqual(stores.migrate(self.layout), expected)
        self.assertEqual(tree(), snapshot)
        stores.rollback(self.layout)
        self.assertEqual(len(stores.rollback(self.layout)), 1)
        self.assertEqual(self.marker(), {"stores": 1})

    def test_rollback_without_a_migration_changes_nothing(self) -> None:
        code, out, err = self.rollback()
        self.assertEqual(code, 0, err)
        self.assertIn("the plugin data folder is already authoritative", out)
        self.assertFalse(self.layout.marker.exists())
        self.assertFalse(self.paths.backups.exists())

    def test_interrupted_migration_finishes_on_the_next_run(self) -> None:
        (self.layout.plugin_scripts / "farm.luau").write_text("-- farm\n", encoding="utf-8")
        real_copy, calls = stores._copy, []

        def failing_copy(source, target):
            calls.append(source)
            if len(calls) == 4:  # both data files and the first script file are done
                raise OSError("disk full")
            real_copy(source, target)

        with mock.patch.object(stores, "_copy", failing_copy), self.assertRaises(StoreError) as caught:
            stores.migrate(self.layout)
        self.assertIn("run the same command again", str(caught.exception))
        self.assertFalse(self.layout.marker.exists())  # the plugin layout is still authoritative
        self.assertEqual(self.status().status, WARN)
        self.assertEqual(len(stores.script_files(self.layout.plugin_scripts)), 1)

        stores.migrate(self.layout)
        self.assertEqual(self.marker(), {"stores": 2})
        self.assertEqual(sorted(path.name for path in stores.script_files(self.layout.scripts)),
                         ["dig.luau", "farm.luau"])
        self.assertEqual(stores.script_files(self.layout.plugin_scripts), [])

    def test_interrupted_rollback_keeps_app_current(self) -> None:
        stores.migrate(self.layout)
        with mock.patch.object(stores, "_copy", side_effect=OSError("denied")):
            code, _out, err = self.rollback()
        self.assertEqual(code, 1)
        self.assertIn("app/current still names 0.2.0", err)
        self.assertEqual(self.paths.app_current.read_text(encoding="utf-8"), "0.2.0\n")
        self.assertEqual(self.marker(), {"stores": 2})
        code, _out, err = self.rollback()
        self.assertEqual(code, 0, err)
        self.assertEqual(self.marker(), {"stores": 1})

    def test_fresh_layout(self) -> None:
        empty = StoreLayout.for_instance(self.paths, self.root / "fresh-server")
        stores.migrate(empty)
        self.assertEqual(self.marker(), {"stores": 2})
        self.assertTrue(empty.scripts.is_dir())
        self.assertFalse(empty.plugin_data.exists())
        self.assertFalse(self.paths.backups.exists())


class DoctorStoresTests(StoreTestCase):
    def write_marker(self, text: str) -> None:
        self.layout.marker.parent.mkdir(parents=True, exist_ok=True)
        self.layout.marker.write_text(text, encoding="utf-8")

    def test_every_outcome(self) -> None:
        result = self.status()
        self.assertEqual(result.status, WARN)
        self.assertIn("no migrations.json", result.message)
        self.assertIn("groups.json, scripts.json, 1 script file", result.message)
        self.write_marker('{"stores": 1}')
        self.assertIn("stores 1", self.status().message)
        self.assertEqual(self.status().status, WARN)
        self.assertEqual(self.status(server=None).status, WARN)

        stores.migrate(self.layout)
        result = self.status()
        self.assertEqual(result.status, PASS)
        self.assertIn("is kept but not read", result.message)
        (self.layout.plugin_scripts / "late.luau").write_text("-- late\n", encoding="utf-8")
        result = self.status()
        self.assertEqual(result.status, WARN)
        self.assertIn("are ignored", result.message)

        for text, expected in (("not json", "cannot be read"), ('{"stores": "2"}', "no valid"),
                               ('{"stores": 0}', "no valid"), ('{"stores": 3}', "newer Endbot")):
            with self.subTest(text=text):
                self.write_marker(text)
                result = self.status()
                self.assertEqual(result.status, FAIL)
                self.assertIn(expected, result.message)
                with self.assertRaises(StoreError):
                    stores.migrate(self.layout)


if __name__ == "__main__":
    unittest.main()
