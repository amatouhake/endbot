"""Tests for `endbot setup` planning and applying (section 6).

Everything runs against temp-directory instances with fake BDS acquisition
(no network, no real BDS): the fake records the server folder it was called
with and leaves behind what Endstone's `_download` would write.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from fakeinstance import build_bds_dir, simulate_bds_download

from endbot_cli.allowlist import add_gamertags
from endbot_cli.instance import InstancePaths
from endbot_cli.lock import load_lock
from endbot_cli.properties import parse_properties
from endbot_cli.setupcmd import BDS_DOWNLOAD, BDS_NONE, BDS_UPDATE, compute_setup_plan, plan_bds, run_setup


class RecordingAcquire:
    """Fake Endstone acquisition step: records calls, simulates the download."""

    def __init__(self, *, simulate: bool = True) -> None:
        self.calls: list[Path] = []
        self.simulate = simulate

    def __call__(self, server: Path) -> None:
        self.calls.append(Path(server))
        if self.simulate:
            simulate_bds_download(Path(server))


class SetupTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.paths = InstancePaths.for_root(self.root)
        self.lock = load_lock()

    def run_setup(self, **kwargs) -> tuple[int, str, str]:
        kwargs.setdefault("installed_endstone_version", lambda: self.lock.endstone_package_version)
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = run_setup(self.paths, **kwargs)
        return code, stdout.getvalue(), stderr.getvalue()

    def existing_args(self, server: Path, **kwargs) -> dict:
        values = {
            "fresh": False,
            "existing": server,
            "gamertags": ["ExampleTag"],
            "apply": False,
            "have_world_backup": True,
        }
        values.update(kwargs)
        return values


class PlanTests(SetupTestCase):
    def test_fresh_plan_lists_every_change_and_downloads_bds(self) -> None:
        code, out, err = self.run_setup(fresh=True, existing=None, gamertags=["Alice"], apply=False)
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertIn("plan (fresh)", out)
        self.assertIn("download BDS", out)
        self.assertIn("Endstone's acquisition path", out)
        self.assertIn("create endbot.toml", out)
        self.assertIn("state/secrets", out)
        self.assertIn("online-mode=true and allow-cheats=false", out)
        self.assertIn("nothing was changed", out)
        self.assertFalse(self.paths.endbot_toml.exists())
        self.assertFalse((self.root / "state").exists())
        self.assertFalse((self.root / "server").exists())

    def test_existing_plan_versions(self) -> None:
        cases = {
            "26.51": ("no BDS change", BDS_NONE),
            "26.50": ("update BDS", BDS_UPDATE),
            "1.21.51": ("update BDS", BDS_UPDATE),
        }
        for version, (expected, action) in cases.items():
            with self.subTest(version=version):
                server = build_bds_dir(self.root / f"server-{version}", version=version)
                code, out, err = self.run_setup(**self.existing_args(server))
                self.assertEqual(code, 0)
                self.assertEqual(err, "")
                self.assertIn(expected, out)
                self.assertEqual(plan_bds(server, self.lock).action, action)

    def test_existing_plan_equal_version_mentions_the_match(self) -> None:
        server = build_bds_dir(self.root / "server", version="26.51")
        _code, out, _err = self.run_setup(**self.existing_args(server))
        self.assertIn("matching locked", out)

    def test_missing_version_txt_will_overwrite(self) -> None:
        server = build_bds_dir(self.root / "server", version=None)
        _code, out, _err = self.run_setup(**self.existing_args(server))
        self.assertIn("version.txt is missing", out)
        self.assertIn("older than supported", out)
        self.assertIn("overwrites the server executable", out)
        self.assertIn("behavior_packs/, resource_packs/, definitions/", out)
        self.assertIn("are kept", out)

    def test_newer_version_refuses_the_plan(self) -> None:
        server = build_bds_dir(self.root / "server", version="26.99")
        code, out, _err = self.run_setup(**self.existing_args(server))
        self.assertEqual(code, 0)  # dry-run still exits 0 and prints
        self.assertIn("NEWER than locked", out)
        self.assertIn("this plan cannot be applied", out)
        code, out, err = self.run_setup(**self.existing_args(server, apply=True))
        self.assertEqual(code, 1)
        self.assertIn("refusing to apply", err)

    def test_existing_plan_lists_backup_set_and_world_policy(self) -> None:
        server = build_bds_dir(self.root / "server", worlds=True)
        (server / "allowlist.json").write_text("[]\n", encoding="utf-8")
        code, out, _err = self.run_setup(**self.existing_args(server))
        self.assertEqual(code, 0)
        self.assertIn("back up server.properties, allowlist.json, behavior_packs/, resource_packs/, definitions/", out)
        self.assertIn("manifest.json", out)
        self.assertIn("worlds/ is NOT copied", out)
        _code, out, _err = self.run_setup(**self.existing_args(server, backup_worlds=True))
        self.assertIn("back up worlds/ as well", out)

    def test_fresh_warns_about_unconfigured_controllers(self) -> None:
        _code, out, _err = self.run_setup(fresh=True, existing=None, gamertags=[], apply=False)
        self.assertIn("no --controller GamerTags given", out)


class RefusalTests(SetupTestCase):
    def test_existing_endbot_toml_is_refused(self) -> None:
        self.paths.endbot_toml.write_text("[server]\n", encoding="utf-8")
        code, _out, err = self.run_setup(fresh=True, existing=None, gamertags=[], apply=False)
        self.assertEqual(code, 1)
        self.assertIn("endbot.toml already exists", err)
        self.assertIn("endbot update", err)

    def test_running_supervisor_is_refused(self) -> None:
        self.paths.state_run.mkdir(parents=True)
        (self.paths.state_run / "supervisor.pid").write_text(str(os.getpid()), encoding="utf-8")
        code, _out, err = self.run_setup(fresh=True, existing=None, gamertags=[], apply=False)
        self.assertEqual(code, 1)
        self.assertIn("supervisor", err)

    def test_unsafe_properties_fail_the_plan_without_changes(self) -> None:
        server = build_bds_dir(self.root / "server", properties="online-mode=false\nallow-cheats=false\n")
        code, out, _err = self.run_setup(**self.existing_args(server))
        self.assertEqual(code, 0)  # dry-run prints the FAIL
        self.assertIn("online-mode must be true", out)
        self.assertIn("never silently changes an existing server's settings", out)
        code, _out, err = self.run_setup(**self.existing_args(server, apply=True))
        self.assertEqual(code, 1)
        self.assertIn("refusing to apply", err)
        self.assertFalse(self.paths.endbot_toml.exists())
        self.assertFalse(self.paths.backups.exists())

    def test_allow_cheats_true_fails_the_plan(self) -> None:
        server = build_bds_dir(
            self.root / "server", properties="online-mode=true\nallow-cheats=true\n", version=None
        )
        _code, out, _err = self.run_setup(**self.existing_args(server))
        self.assertIn("allow-cheats must be false", out)

    def test_creative_world_fails_the_plan(self) -> None:
        server = build_bds_dir(self.root / "server", worlds=True, creative=True)
        code, out, _err = self.run_setup(**self.existing_args(server))
        self.assertEqual(code, 0)
        self.assertIn("world check", out)
        self.assertIn("hasBeenLoadedInCreative", out)
        self.assertIn("Endbot does not change world flags", out)
        code, _out, _err = self.run_setup(**self.existing_args(server, apply=True))
        self.assertEqual(code, 1)
        self.assertFalse(self.paths.endbot_toml.exists())

    def test_apply_requires_the_world_backup_acknowledgement(self) -> None:
        server = build_bds_dir(self.root / "server", worlds=True)
        code, _out, err = self.run_setup(
            fresh=False,
            existing=server,
            gamertags=["ExampleTag"],
            apply=True,
            backup_worlds=False,
            have_world_backup=False,
        )
        self.assertEqual(code, 1)
        self.assertIn("--i-have-a-world-backup", err)
        self.assertFalse(self.paths.endbot_toml.exists())
        self.assertFalse(self.paths.backups.exists())

    def test_missing_existing_directory_fails_the_plan(self) -> None:
        code, out, _err = self.run_setup(**self.existing_args(self.root / "no-such"))
        self.assertEqual(code, 0)
        self.assertIn("does not exist", out)

    def test_empty_controller_gamertag_is_refused(self) -> None:
        code, _out, err = self.run_setup(fresh=True, existing=None, gamertags=[""], apply=False)
        self.assertEqual(code, 1)
        self.assertIn("non-empty strings", err)


class ApplyTests(SetupTestCase):
    def test_apply_fresh_downloads_edits_properties_and_writes_the_instance(self) -> None:
        acquire = RecordingAcquire()
        code, out, err = self.run_setup(
            fresh=True,
            existing=None,
            gamertags=["Alice", "Bob"],
            apply=True,
            acquire_bds=acquire,
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(acquire.calls, [self.root / "server"])

        config = self.paths.endbot_toml.read_text(encoding="utf-8")
        self.assertIn("path = 'server'", config)
        self.assertIn('gamertags = ["Alice", "Bob"]', config)
        self.assertIn("control-port = 19142", config)
        for name in ("secrets", "profiles", "generated", "run"):
            self.assertTrue((self.root / "state" / name).is_dir())

        properties_text = (self.root / "server" / "server.properties").read_text(encoding="utf-8")
        self.assertIn("# Bedrock Dedicated Server properties", properties_text)
        self.assertIn("online-mode = true", properties_text)  # only the value changed
        self.assertIn("allow-cheats = false", properties_text)
        self.assertIn("level-name=world", properties_text)
        properties = parse_properties(self.root / "server" / "server.properties")
        self.assertEqual(properties["online-mode"], "true")
        self.assertEqual(properties["allow-cheats"], "false")

        self.assertIn("doctor after apply:", out)
        self.assertIn("the first `endbot start` generates the owner keys and control token", out)
        self.assertIn("next steps:", out)
        self.assertIn("join once with each controller GamerTag (Alice, Bob)", out)

    def test_apply_fresh_flips_unsafe_defaults_from_the_download(self) -> None:
        acquire = RecordingAcquire()
        self.run_setup(fresh=True, existing=None, gamertags=[], apply=True, acquire_bds=acquire)
        properties = parse_properties(self.root / "server" / "server.properties")
        self.assertEqual(properties["online-mode"], "true")
        self.assertEqual(properties["allow-cheats"], "false")

    def test_apply_existing_equal_version_skips_bds_and_backs_up(self) -> None:
        server = build_bds_dir(self.root / "elsewhere", version="26.51", worlds=True)
        (server / "allowlist.json").write_text("[]\n", encoding="utf-8")
        original_properties = (server / "server.properties").read_bytes()
        acquire = RecordingAcquire()
        code, out, err = self.run_setup(
            **self.existing_args(server, apply=True, acquire_bds=acquire),
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(acquire.calls, [])
        self.assertEqual((server / "server.properties").read_bytes(), original_properties)

        backups = [entry for entry in self.paths.backups.iterdir() if entry.is_dir()]
        self.assertEqual(len(backups), 1)
        manifest = backups[0] / "manifest.json"
        self.assertTrue(manifest.is_file())
        self.assertIn("backed up", out)
        self.assertFalse((backups[0] / "worlds").exists())

        config = self.paths.endbot_toml.read_text(encoding="utf-8")
        self.assertIn(f"path = '{server}'", config)

    def test_apply_existing_older_version_runs_the_bds_step(self) -> None:
        server = build_bds_dir(self.root / "server", version="26.50")
        acquire = RecordingAcquire()
        code, _out, err = self.run_setup(**self.existing_args(server, apply=True, acquire_bds=acquire))
        self.assertEqual(code, 0, err)
        self.assertEqual(acquire.calls, [server])
        self.assertEqual((server / "version.txt").read_text(encoding="utf-8"), "26.51")

    def test_apply_fails_when_the_acquisition_step_fails(self) -> None:
        from endbot_cli.bds import BdsError

        def broken_acquire(server: Path) -> None:
            raise BdsError("download failed")

        code, _out, err = self.run_setup(
            fresh=True, existing=None, gamertags=[], apply=True, acquire_bds=broken_acquire
        )
        self.assertEqual(code, 1)
        self.assertIn("download failed", err)
        self.assertFalse(self.paths.endbot_toml.exists())


class AllowlistTests(SetupTestCase):
    """setup and the BDS allow-list (sections 4 and 6)."""

    ALLOW_LIST_PROPERTIES = "online-mode=true\nallow-cheats=false\nallow-list=true\nlevel-name=world\n"

    @staticmethod
    def read_allowlist(server: Path) -> list:
        return json.loads((server / "allowlist.json").read_text(encoding="utf-8"))

    def test_fresh_plan_lists_the_allowlist_additions_but_writes_nothing(self) -> None:
        code, out, err = self.run_setup(fresh=True, existing=None, gamertags=["Alice", "Bob"], apply=False)
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertIn("add Alice to allowlist.json (a fresh BDS enables allow-list=true)", out)
        self.assertIn("add Bob to allowlist.json (a fresh BDS enables allow-list=true)", out)
        self.assertFalse((self.root / "server" / "allowlist.json").exists())

    def test_fresh_apply_adds_controllers_and_creates_the_file(self) -> None:
        acquire = RecordingAcquire()
        code, out, err = self.run_setup(
            fresh=True, existing=None, gamertags=["Alice", "Bob"], apply=True, acquire_bds=acquire
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(
            self.read_allowlist(self.root / "server"),
            [
                {"ignoresPlayerLimit": False, "name": "Alice"},
                {"ignoresPlayerLimit": False, "name": "Bob"},
            ],
        )
        self.assertIn("added Alice to allowlist.json (a fresh BDS enables allow-list=true)", out)
        self.assertIn("added Bob to allowlist.json", out)

    def test_fresh_apply_keeps_existing_entries_order_and_is_idempotent(self) -> None:
        server = self.root / "server"
        server.mkdir()
        original = '[\n  {"ignoresPlayerLimit": true, "name": "alice", "xuid": "123"},\n  {"name": "Carol"}\n]\n'
        (server / "allowlist.json").write_text(original, encoding="utf-8")
        acquire = RecordingAcquire()
        code, _out, err = self.run_setup(
            fresh=True, existing=None, gamertags=["Alice", "Bob"], apply=True, acquire_bds=acquire
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(
            self.read_allowlist(server),
            [
                {"ignoresPlayerLimit": True, "name": "alice", "xuid": "123"},  # kept as-is (case-insensitive match)
                {"name": "Carol"},
                {"ignoresPlayerLimit": False, "name": "Bob"},  # appended
            ],
        )
        before = (server / "allowlist.json").read_bytes()
        self.assertEqual(add_gamertags(server / "allowlist.json", ["Alice", "BOB"]), [])  # idempotent
        self.assertEqual((server / "allowlist.json").read_bytes(), before)

    def test_fresh_plan_is_idempotent_and_case_insensitive(self) -> None:
        server = self.root / "server"
        server.mkdir()
        (server / "allowlist.json").write_text('[{"name": "alice"}]\n', encoding="utf-8")
        code, out, _err = self.run_setup(fresh=True, existing=None, gamertags=["Alice"], apply=False)
        self.assertEqual(code, 0)
        self.assertNotIn("add Alice to allowlist.json", out)

    def test_fresh_refuses_a_malformed_allowlist_without_overwriting(self) -> None:
        for text in ("{not json", '{"name": "Alice"}', "[1, 2]"):
            with self.subTest(text=text):
                server = self.root / "server"
                server.mkdir(exist_ok=True)
                path = server / "allowlist.json"
                path.write_text(text, encoding="utf-8")
                code, out, _err = self.run_setup(
                    fresh=True, existing=None, gamertags=["Alice"], apply=False
                )
                self.assertEqual(code, 0)  # dry-run prints the FAIL
                self.assertIn("allowlist.json", out)
                self.assertIn("FAIL", out)
                self.assertIn("nothing was changed", out)
                code, _out, err = self.run_setup(
                    fresh=True, existing=None, gamertags=["Alice"], apply=True, acquire_bds=RecordingAcquire()
                )
                self.assertEqual(code, 1)
                self.assertIn("refusing to apply", err)
                self.assertEqual(path.read_text(encoding="utf-8"), text)  # never overwritten
                path.unlink()

    def test_existing_never_edits_allowlist_and_prints_the_console_hint(self) -> None:
        server = build_bds_dir(self.root / "server", properties=self.ALLOW_LIST_PROPERTIES)
        path = server / "allowlist.json"
        path.write_text("[]\n", encoding="utf-8")
        code, out, _err = self.run_setup(**self.existing_args(server))
        self.assertEqual(code, 0)
        self.assertIn("run `endbot console allowlist add ExampleTag` after `endbot start`", out)
        self.assertIn("or add it to allowlist.json while the server is stopped", out)
        before = path.read_bytes()
        code, _out, err = self.run_setup(**self.existing_args(server, apply=True, acquire_bds=RecordingAcquire()))
        self.assertEqual(code, 0, err)
        self.assertEqual(path.read_bytes(), before)

    def test_existing_skips_the_hint_when_allow_list_is_off_or_controllers_are_listed(self) -> None:
        properties = self.ALLOW_LIST_PROPERTIES.replace("allow-list=true", "allow-list=false")
        server = build_bds_dir(self.root / "server-off", properties=properties)
        (server / "allowlist.json").write_text("[]\n", encoding="utf-8")
        _code, out, _err = self.run_setup(**self.existing_args(server))
        self.assertNotIn("allowlist add", out)

        listed = build_bds_dir(self.root / "server-listed", properties=self.ALLOW_LIST_PROPERTIES)
        (listed / "allowlist.json").write_text('[{"name": "exampletag"}]\n', encoding="utf-8")
        _code, out, _err = self.run_setup(**self.existing_args(listed))
        self.assertNotIn("allowlist add", out)

    def test_existing_malformed_allowlist_warns_but_the_plan_applies(self) -> None:
        server = build_bds_dir(self.root / "server", properties=self.ALLOW_LIST_PROPERTIES)
        path = server / "allowlist.json"
        path.write_text("{not json", encoding="utf-8")
        code, out, _err = self.run_setup(**self.existing_args(server))
        self.assertEqual(code, 0)
        self.assertIn("setup: WARN", out)
        self.assertIn("allowlist.json", out)
        code, _out, err = self.run_setup(**self.existing_args(server, apply=True, acquire_bds=RecordingAcquire()))
        self.assertEqual(code, 0, err)
        self.assertEqual(path.read_text(encoding="utf-8"), "{not json")


class BdsPlanTests(SetupTestCase):
    def test_fresh_empty_directory_downloads(self) -> None:
        plan = plan_bds(self.root / "server", self.lock)
        self.assertEqual(plan.action, BDS_DOWNLOAD)
        self.assertIn("no server executable found", plan.lines[0])

    def test_equal_version_with_missing_executable_downloads(self) -> None:
        server = build_bds_dir(self.root / "server", version="26.51", executable=False)
        plan = plan_bds(server, self.lock)
        self.assertEqual(plan.action, BDS_DOWNLOAD)
        self.assertIn("executable is missing", plan.lines[0])

    def test_plan_fresh_on_existing_worlds_warns(self) -> None:
        build_bds_dir(self.root / "server", worlds=True)
        plan = compute_setup_plan(self.paths, fresh=True, existing=None, gamertags=[])
        self.assertTrue(any("worlds" in warning for warning in plan.warnings))


if __name__ == "__main__":
    unittest.main()
