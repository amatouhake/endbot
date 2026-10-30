"""End-to-end tests for `endbot doctor` against synthetic instances (section 7)."""

from __future__ import annotations

import json
import re
import socket
import socketserver
import tempfile
import threading
import unittest
from pathlib import Path

import fakenbt
from fakeinstance import build_instance, generate_key_pem, write_endbot_toml, write_endstone_toml

from endbot_cli.doctor import FAIL, PASS, SKIP, WARN, DoctorContext, exit_code, run_doctor
from endbot_cli.instance import InstancePaths
from endbot_cli.lock import load_lock


class ControlHandler(socketserver.StreamRequestHandler):
    def handle(self):
        request = json.loads(self.rfile.readline())
        if request["token"] != self.server.token:
            response = {
                "version": 1,
                "id": request["id"],
                "ok": False,
                "error": {"code": "unauthorized", "message": "no"},
            }
        else:
            response = {"version": 1, "id": request["id"], "ok": True, "result": {"method": request["method"]}}
        self.wfile.write(json.dumps(response).encode() + b"\n")


class DoctorTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.lock = load_lock()
        self.paths: InstancePaths = build_instance(self.root, self.lock)
        self.server = self.paths.root / "server"

    def doctor(self, *, live: bool = False, endstone_version: str | None = "") -> list:
        lookup = (lambda: self.lock.endstone_package_version) if endstone_version == "" else (lambda: endstone_version)
        context = DoctorContext(
            paths=self.paths,
            live=live,
            installed_endstone_version=lookup,
            lock=self.lock,
        )
        return run_doctor(context)

    @staticmethod
    def status_of(results: list, name: str) -> str:
        matches = [result.status for result in results if result.name == name]
        if len(matches) != 1:
            raise AssertionError(f"expected exactly one {name!r} result, found {matches}")
        return matches[0]

    @staticmethod
    def messages_of(results: list, name: str) -> list[str]:
        return [result.message for result in results if result.name == name]


class HealthyInstanceTests(DoctorTestCase):
    def test_healthy_instance_has_no_failures(self) -> None:
        results = self.doctor()
        self.assertEqual(exit_code(results), 0)
        expected = {
            "config": PASS,
            "server-dir": PASS,
            "server-properties": PASS,
            "world": SKIP,
            "bds-version": PASS,
            "local-bot-auth": PASS,
            "plugin-port": SKIP,
            "controllers-legacy": SKIP,
            "endstone-version": PASS,
            "control-token": PASS,
            "endbotd-token": PASS,
            "endbotd-config": SKIP,
            "endbotd-link": SKIP,
            "controllers": WARN,
            "allowlist": PASS,
            "operators": PASS,
            "runtime": SKIP,
            "stores": WARN,  # no migrations.json yet: the next start migrates
        }
        for name, status in expected.items():
            with self.subTest(name=name):
                self.assertEqual(self.status_of(results, name), status)
        self.assertIn("join once with this GamerTag", self.messages_of(results, "controllers")[0])

    def test_pending_and_bound_controllers(self) -> None:
        self.paths.state_controllers.write_text(
            json.dumps(
                {
                    "version": 1,
                    "bindings": [
                        {
                            "gamertag": "exampletag",
                            "xuid": "1",
                            "uuid": "11111111-2222-4333-8444-555555555555",
                            "boundAt": "2026-09-19T00:00:00Z",
                        },
                        {"gamertag": "OldTag", "xuid": "2", "uuid": "v", "boundAt": "2026-09-01T00:00:00Z"},
                    ],
                }
            ),
            encoding="utf-8",
        )
        write_endbot_toml(self.root, gamertags='["ExampleTag", "NewTag"]')
        results = self.doctor()
        self.assertEqual(exit_code(results), 0)
        messages = self.messages_of(results, "controllers")
        self.assertTrue(any("ExampleTag is bound" in message for message in messages))
        self.assertTrue(any("NewTag is pending" in message and "join once" in message for message in messages))
        self.assertTrue(any("OldTag" in message and "revoked" in message for message in messages))

    def test_plugin_port_agreement(self) -> None:
        plugins = self.server / "plugins" / "endbot"
        plugins.mkdir(parents=True)
        token = str(self.paths.endbotd_token.resolve())

        def plugin_config(port, token_file=token, extra=""):
            (plugins / "config.toml").write_text(
                f"[endbotd]\nport = {port}\ntoken-file = '{token_file}'\n{extra}", encoding="utf-8")
            return self.status_of(self.doctor(), "plugin-port")

        self.assertEqual(plugin_config(19143), PASS)
        self.assertEqual(plugin_config(25565), FAIL)
        self.assertEqual(plugin_config(19143, str(self.paths.control_token.resolve())), FAIL)
        # A plugin config that still carries the runtime token is regenerated, never accepted.
        self.assertEqual(plugin_config(19143, extra="[runtime]\nport = 19142\n"), FAIL)
        self.assertEqual(exit_code(self.doctor()), 1)
        # A pre-endbotd plugin config is regenerated by start; WARN so that version's update can switch.
        (plugins / "config.toml").write_text("[runtime]\nport = 19142\n", encoding="utf-8")
        self.assertEqual(self.status_of(self.doctor(), "plugin-port"), WARN)

    def test_missing_endbotd_token_does_not_block_an_update(self) -> None:
        self.paths.endbotd_token.unlink()
        self.assertEqual(self.status_of(self.doctor(), "endbotd-token"), WARN)

    def test_endbotd_config_token_and_link(self) -> None:
        from endbot_cli.config import load_config
        from endbot_cli.configgen import generate_all

        generate_all(load_config(self.paths.endbot_toml), self.paths, self.server)
        results = self.doctor()
        for name in ("plugin-port", "endbotd-token", "endbotd-config", "controllers-legacy"):
            with self.subTest(name=name):
                self.assertEqual(self.status_of(results, name), PASS)
        self.assertEqual(self.status_of(results, "endbotd-link"), SKIP)
        log = self.paths.state_run / "logs" / "20261029-000000" / "endbotd.log"
        log.parent.mkdir(parents=True)
        log.write_text("x INFO Endbot plugin 0.4.0 linked\n", encoding="utf-8")
        self.assertEqual(self.status_of(self.doctor(), "endbotd-link"), PASS)
        log.write_text("x WARNING Endbot plugin link refused: plugin and endbotd carry different operation "
                       "registries\n", encoding="utf-8")
        self.assertEqual(self.status_of(self.doctor(), "endbotd-link"), FAIL)
        self.paths.endbotd_token.write_text("short", encoding="utf-8")
        self.assertEqual(self.status_of(self.doctor(), "endbotd-token"), FAIL)


class FreshInstanceTests(DoctorTestCase):
    def test_fresh_instance_fails_gracefully(self) -> None:
        empty = self.root / "empty"
        empty.mkdir()
        context = DoctorContext(
            paths=InstancePaths.for_root(empty),
            installed_endstone_version=lambda: self.lock.endstone_package_version,
            lock=self.lock,
        )
        results = run_doctor(context)
        self.assertEqual(exit_code(results), 1)
        self.assertEqual(self.status_of(results, "config"), FAIL)
        self.assertIn("endbot setup", self.messages_of(results, "config")[0])
        self.assertEqual(self.status_of(results, "server-dir"), SKIP)
        self.assertEqual(self.status_of(results, "allowlist"), SKIP)
        self.assertEqual(self.status_of(results, "runtime"), SKIP)
        for result in results:
            self.assertIn(result.status, {PASS, WARN, FAIL, SKIP})


class SafetyInvariantTests(DoctorTestCase):
    def test_unsafe_properties_fail(self) -> None:
        (self.server / "server.properties").write_text("online-mode=false\nallow-cheats=true\nlevel-name=world\n")
        results = self.doctor()
        self.assertEqual(exit_code(results), 1)
        self.assertEqual(self.status_of(results, "server-properties"), FAIL)
        self.assertIn("online-mode", self.messages_of(results, "server-properties")[0])

    def test_missing_transport_fails_with_nethernet_hint(self) -> None:
        (self.server / "server.properties").write_text(
            "online-mode=true\nallow-cheats=false\nlevel-name=world\n", encoding="utf-8"
        )
        results = self.doctor()
        self.assertEqual(exit_code(results), 1)
        self.assertEqual(self.status_of(results, "server-properties"), FAIL)
        self.assertIn("transport", self.messages_of(results, "server-properties")[0])
        self.assertIn("nethernet", self.messages_of(results, "server-properties")[0])

    def test_wrong_transport_fails(self) -> None:
        (self.server / "server.properties").write_text(
            "online-mode=true\nallow-cheats=false\ntransport=raknet\nlevel-name=world\n",
            encoding="utf-8",
        )
        results = self.doctor()
        self.assertEqual(exit_code(results), 1)
        self.assertEqual(self.status_of(results, "server-properties"), FAIL)
        self.assertIn("transport", self.messages_of(results, "server-properties")[0])

    def test_nethernet_transport_passes(self) -> None:
        (self.server / "server.properties").write_text(
            "online-mode=true\nallow-cheats=false\ntransport=nethernet\nlevel-name=world\n",
            encoding="utf-8",
        )
        results = self.doctor()
        self.assertEqual(self.status_of(results, "server-properties"), PASS)
        self.assertIn("transport=nethernet", self.messages_of(results, "server-properties")[0])

    def test_raknet_passes_with_matching_config_but_not_with_nethernet_bds(self) -> None:
        write_endbot_toml(self.root, transport="raknet")
        path = self.root / "server/server.properties"
        path.write_text("online-mode=true\nallow-cheats=false\ntransport=raknet\nlevel-name=world\n", encoding="utf-8")
        results = self.doctor()
        self.assertIn("transport=raknet", self.messages_of(results, "server-properties")[0])
        self.assertEqual(next(r.status for r in results if r.name == "server-properties"), PASS)
        path.write_text(path.read_text().replace("transport=raknet", "transport=nethernet"), encoding="utf-8")
        results = self.doctor()
        self.assertEqual(next(r.status for r in results if r.name == "server-properties"), FAIL)

    def test_numeric_gamertag_is_rejected_with_quoting_hint(self) -> None:
        write_endbot_toml(self.root, gamertags="[1234]")
        results = self.doctor()
        self.assertEqual(exit_code(results), 1)
        message = self.messages_of(results, "config")[0]
        self.assertIn("quote", message)
        self.assertIn("controllers.gamertags[0]", message)

    def test_creative_world_history_fails(self) -> None:
        world = self.root / "server" / "worlds" / "world"
        world.mkdir(parents=True)
        (world / "level.dat").write_bytes(fakenbt.level_dat({"hasBeenLoadedInCreative": fakenbt.byte(1)}))
        results = self.doctor()
        self.assertEqual(exit_code(results), 1)
        self.assertEqual(self.status_of(results, "world"), FAIL)
        self.assertIn("hasBeenLoadedInCreative", self.messages_of(results, "world")[0])

    def test_experiment_history_fails(self) -> None:
        world = self.root / "server" / "worlds" / "world"
        world.mkdir(parents=True)
        (world / "level.dat").write_bytes(
            fakenbt.level_dat(
                {
                    "experiments": fakenbt.compound(
                        {
                            "experiments_ever_used": fakenbt.byte(0),
                            "saved_with_toggled_experiments": fakenbt.byte(1),
                        }
                    )
                }
            )
        )
        results = self.doctor()
        self.assertEqual(self.status_of(results, "world"), FAIL)
        self.assertIn("saved_with_toggled_experiments", self.messages_of(results, "world")[0])

    def test_unknown_experiment_keys_warn(self) -> None:
        world = self.root / "server" / "worlds" / "world"
        world.mkdir(parents=True)
        (world / "level.dat").write_bytes(
            fakenbt.level_dat(
                {
                    "experiments": fakenbt.compound(
                        {
                            "experiments_ever_used": fakenbt.byte(0),
                            "saved_with_toggled_experiments": fakenbt.byte(0),
                            "crawling_experimental": fakenbt.byte(1),
                        }
                    )
                }
            )
        )
        results = self.doctor()
        self.assertEqual(exit_code(results), 0)
        self.assertEqual(self.status_of(results, "world"), WARN)
        self.assertIn("crawling_experimental", self.messages_of(results, "world")[0])

    def test_corrupt_level_dat_fails_without_traceback(self) -> None:
        world = self.root / "server" / "worlds" / "world"
        world.mkdir(parents=True)
        (world / "level.dat").write_bytes(b"\x01\x00\x00\x00\x04\x00\x00\x00\xff\xff\xff\xff")
        results = self.doctor()
        self.assertEqual(exit_code(results), 1)
        self.assertEqual(self.status_of(results, "world"), FAIL)
        self.assertIn("corrupt", self.messages_of(results, "world")[0])

    def test_world_directory_without_level_dat_fails(self) -> None:
        world = self.root / "server" / "worlds" / "world"
        world.mkdir(parents=True)
        results = self.doctor()
        self.assertEqual(exit_code(results), 1)
        self.assertEqual(self.status_of(results, "world"), FAIL)
        self.assertIn("level.dat", self.messages_of(results, "world")[0])
        self.assertIn("falls back", self.messages_of(results, "world")[0])

    def test_missing_level_name_checks_the_default_world(self) -> None:
        # A missing level-name is valid BDS: the default world must still be inspected.
        (self.server / "server.properties").write_text("online-mode=true\nallow-cheats=false\n", encoding="utf-8")
        world = self.server / "worlds" / "Bedrock level"
        world.mkdir(parents=True)
        (world / "level.dat").write_bytes(
            fakenbt.level_dat({"GameType": fakenbt.int32(0), "cheatsEnabled": fakenbt.byte(0)})
        )
        results = self.doctor()
        self.assertEqual(self.status_of(results, "world"), PASS)
        self.assertIn("Bedrock level", self.messages_of(results, "world")[0])

    def test_missing_level_name_with_default_dir_but_no_level_dat_fails(self) -> None:
        (self.server / "server.properties").write_text("online-mode=true\nallow-cheats=false\n", encoding="utf-8")
        (self.server / "worlds" / "Bedrock level").mkdir(parents=True)
        results = self.doctor()
        self.assertEqual(exit_code(results), 1)
        self.assertEqual(self.status_of(results, "world"), FAIL)
        self.assertIn("level.dat", self.messages_of(results, "world")[0])

    def test_missing_level_name_without_default_dir_skips(self) -> None:
        (self.server / "server.properties").write_text("online-mode=true\nallow-cheats=false\n", encoding="utf-8")
        results = self.doctor()
        self.assertEqual(self.status_of(results, "world"), SKIP)


class VersionTests(DoctorTestCase):
    def test_missing_version_txt_explains_the_redownload_risk(self) -> None:
        (self.server / "version.txt").unlink()
        results = self.doctor()
        self.assertEqual(exit_code(results), 1)
        message = self.messages_of(results, "bds-version")[0]
        self.assertEqual(self.status_of(results, "bds-version"), FAIL)
        self.assertIn("re-download", message)

    def test_version_mismatch_points_at_update(self) -> None:
        (self.server / "version.txt").write_text("99.99", encoding="utf-8")
        results = self.doctor()
        self.assertEqual(self.status_of(results, "bds-version"), FAIL)
        self.assertIn("endbot update", self.messages_of(results, "bds-version")[0])

    def test_endstone_package_mismatch_and_missing(self) -> None:
        results = self.doctor(endstone_version="0.0.1+other")
        self.assertEqual(self.status_of(results, "endstone-version"), FAIL)
        self.assertEqual(exit_code(results), 1)
        results = self.doctor(endstone_version=None)
        self.assertEqual(self.status_of(results, "endstone-version"), FAIL)
        self.assertIn("not installed", self.messages_of(results, "endstone-version")[0])


class LocalBotAuthTests(DoctorTestCase):
    def test_key_mismatch_fails(self) -> None:
        other_private, _ = generate_key_pem()
        self.paths.owner_private_key.write_bytes(other_private)
        results = self.doctor()
        self.assertEqual(exit_code(results), 1)
        message = self.messages_of(results, "local-bot-auth")[0]
        self.assertEqual(self.status_of(results, "local-bot-auth"), FAIL)
        self.assertIn("does not match", message)

    def test_non_p384_key_fails(self) -> None:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec

        weak = ec.generate_private_key(ec.SECP256R1())
        self.paths.owner_public_key.write_bytes(
            weak.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        )
        results = self.doctor()
        self.assertEqual(self.status_of(results, "local-bot-auth"), FAIL)
        self.assertIn("P-384", self.messages_of(results, "local-bot-auth")[0])

    def test_disabled_local_bot_auth_fails(self) -> None:
        write_endstone_toml(self.server, enabled=False)
        results = self.doctor()
        self.assertEqual(self.status_of(results, "local-bot-auth"), FAIL)
        self.assertIn("local-bot-auth.enabled", self.messages_of(results, "local-bot-auth")[0])

    def test_missing_endstone_toml_fails(self) -> None:
        (self.server / "endstone.toml").unlink()
        results = self.doctor()
        self.assertEqual(self.status_of(results, "local-bot-auth"), FAIL)


class ControlTokenTests(DoctorTestCase):
    def test_short_token_fails(self) -> None:
        self.paths.control_token.write_text("x" * 10, encoding="utf-8")
        results = self.doctor()
        self.assertEqual(exit_code(results), 1)
        self.assertEqual(self.status_of(results, "control-token"), FAIL)

    def test_missing_token_fails(self) -> None:
        self.paths.control_token.unlink()
        results = self.doctor()
        self.assertEqual(self.status_of(results, "control-token"), FAIL)


class AllowlistTests(DoctorTestCase):
    """The read-only `allowlist` check (sections 4 and 7)."""

    def write_properties(self, allow_list: str | None = None) -> None:
        text = "online-mode=true\nallow-cheats=false\ntransport=nethernet\nlevel-name=world\n"
        if allow_list is not None:
            text += f"allow-list={allow_list}\n"
        (self.server / "server.properties").write_text(text, encoding="utf-8")

    def write_allowlist(self, entries: object) -> None:
        (self.server / "allowlist.json").write_text(json.dumps(entries), encoding="utf-8")

    def test_allow_list_off_passes(self) -> None:
        self.write_properties("false")
        results = self.doctor()
        self.assertEqual(exit_code(results), 0)
        self.assertEqual(self.status_of(results, "allowlist"), PASS)
        self.assertIn("allow-list is off", self.messages_of(results, "allowlist")[0])

    def test_missing_allow_list_key_passes(self) -> None:
        self.write_properties()
        results = self.doctor()
        self.assertEqual(self.status_of(results, "allowlist"), PASS)

    def test_every_controller_listed_passes_case_insensitively(self) -> None:
        self.write_properties("true")
        self.write_allowlist([{"ignoresPlayerLimit": False, "name": "exampletag"}])
        results = self.doctor()
        self.assertEqual(exit_code(results), 0)
        self.assertEqual(self.status_of(results, "allowlist"), PASS)

    def test_bound_xuid_entry_passes_even_with_another_name(self) -> None:
        self.write_properties("true")
        self.paths.state_controllers.write_text(
            json.dumps(
                {
                    "version": 1,
                    "bindings": [{"gamertag": "ExampleTag", "xuid": "2535412345678901", "uuid": "u", "boundAt": "t"}],
                }
            ),
            encoding="utf-8",
        )
        self.write_allowlist([{"ignoresPlayerLimit": False, "name": "RenamedTag", "xuid": "2535412345678901"}])
        results = self.doctor()
        self.assertEqual(self.status_of(results, "allowlist"), PASS)

    def test_no_controllers_configured_passes(self) -> None:
        self.write_properties("true")
        self.write_allowlist([])
        write_endbot_toml(self.root, gamertags="[]")
        results = self.doctor()
        self.assertEqual(self.status_of(results, "allowlist"), PASS)

    def test_missing_controllers_warn_with_the_fix(self) -> None:
        self.write_properties("true")
        self.write_allowlist([])
        write_endbot_toml(self.root, gamertags='["Alice", "Bob"]')
        results = self.doctor()
        self.assertEqual(exit_code(results), 0)  # WARN, not FAIL
        self.assertEqual(self.status_of(results, "allowlist"), WARN)
        message = self.messages_of(results, "allowlist")[0]
        self.assertIn("Alice", message)
        self.assertIn("Bob", message)
        self.assertIn("endbot console allowlist add <GamerTag>", message)
        self.assertIn("allowlist.json while the server is stopped", message)

    def test_missing_file_warns_when_allow_list_is_on(self) -> None:
        self.write_properties("true")
        results = self.doctor()
        self.assertEqual(self.status_of(results, "allowlist"), WARN)
        self.assertIn("ExampleTag", self.messages_of(results, "allowlist")[0])

    def test_invalid_allowlist_warns_but_never_fails(self) -> None:
        self.write_properties("true")
        (self.server / "allowlist.json").write_text("{not json", encoding="utf-8")
        results = self.doctor()
        self.assertEqual(exit_code(results), 0)
        self.assertEqual(self.status_of(results, "allowlist"), WARN)
        message = self.messages_of(results, "allowlist")[0]
        self.assertIn("allowlist.json", message)
        self.assertIn("BDS treats allowlist.json as its own file", message)

    def test_unreadable_properties_skip_the_check(self) -> None:
        (self.server / "server.properties").unlink()
        results = self.doctor()
        self.assertEqual(self.status_of(results, "allowlist"), SKIP)


class EffectiveControllersTests(DoctorTestCase):
    def test_saved_revocation_overrides_bootstrap_and_legacy_allowlist(self):
        identity = "11111111-2222-4333-8444-555555555555"
        self.paths.state_controllers.write_text(
            json.dumps(
                {
                    "version": 1,
                    "bindings": [
                        {"gamertag": "ExampleTag", "xuid": "123456789001", "uuid": identity, "boundAt": "synthetic"}
                    ],
                }
            )
        )
        policy = self.paths.state_controllers.parent / "controller-policy.json"
        policy.write_text(json.dumps({"version": 1, "deniedXuids": ["123456789001"]}))
        messages = self.messages_of(self.doctor(), "controllers-effective")
        self.assertTrue(any("ExampleTag: revoked - revoked XUID" in message for message in messages))
        policy.write_text(
            json.dumps(
                {
                    "version": 1,
                    "grants": [{"gamertag": "Other", "xuid": "123456789002", "uuid": identity, "boundAt": "synthetic"}],
                }
            )
        )
        messages = self.messages_of(self.doctor(), "controllers-effective")
        self.assertTrue(any("Other: allowed - runtime grant" in message for message in messages))
        self.assertTrue(any("ExampleTag: allowed - bootstrap binding" in message for message in messages))

    def test_grant_revisions_file_is_validated(self):
        path = self.paths.state_controllers.parent / "grant-revisions.json"
        self.assertEqual(self.status_of(self.doctor(), "grant-revisions"), PASS)
        path.write_text(json.dumps({"version": 1, "revisions": {"0000000a-0000-4000-8000-00000000000b": 2}}))
        self.assertEqual(self.status_of(self.doctor(), "grant-revisions"), PASS)
        corrupt = (
            "{invalid",
            "[]",
            json.dumps({"version": 2}),
            json.dumps({"version": 1, "revisions": {"x": 1}}),
            json.dumps({"version": 1, "revisions": {"0000000a-0000-4000-8000-00000000000b": 0}}),
        )
        for document in corrupt:
            with self.subTest(document=document):
                path.write_text(document)
                self.assertEqual(self.status_of(self.doctor(), "grant-revisions"), FAIL)

    def test_controller_roles_file_is_validated(self):
        path = self.paths.state_controllers.parent / "controller-roles.json"
        self.assertEqual(self.status_of(self.doctor(), "controller-roles"), PASS)
        path.write_text(json.dumps({"version": 1, "roles": {"2535400000000001": "controller"}}))
        self.assertEqual(self.status_of(self.doctor(), "controller-roles"), PASS)
        self.assertIn("records 1 role", self.messages_of(self.doctor(), "controller-roles")[0])
        path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "roles": {"2535400000000001": "controller"},
                    "pending": {"Some Tag": "scripter"},
                }
            )
        )
        self.assertEqual(self.status_of(self.doctor(), "controller-roles"), PASS)
        self.assertIn("1 pending", self.messages_of(self.doctor(), "controller-roles")[0])
        path.write_text(json.dumps({"version": 1, "roles": {}, "pending": {"Some Tag": "controller"}}))
        self.assertEqual(self.status_of(self.doctor(), "controller-roles"), PASS)
        corrupt = (
            "{invalid",
            "[]",
            json.dumps({"version": 2}),
            json.dumps({"version": 1, "roles": []}),
            json.dumps({"version": 1, "roles": {}, "extra": True}),
            json.dumps({"version": 1, "roles": {"not-an-xuid": "scripter"}}),
            json.dumps({"version": 1, "roles": {"2535400000000001": "owner"}}),
            json.dumps({"version": 1, "roles": {}, "pending": []}),
            json.dumps({"version": 1, "roles": {}, "pending": None}),
            json.dumps({"version": 1, "roles": {}, "pending": "Some Tag"}),
            json.dumps({"version": 1, "roles": {}, "pending": {"": "scripter"}}),
            json.dumps({"version": 1, "roles": {}, "pending": {"x" * 257: "scripter"}}),
            json.dumps({"version": 1, "roles": {}, "pending": {"Bad\nName": "scripter"}}),
            json.dumps({"version": 1, "roles": {}, "pending": {"Some Tag": "owner"}}),
            json.dumps({"version": 1, "roles": {}, "pending": {"Some Tag": 1}}),
        )
        for document in corrupt:
            with self.subTest(document=document):
                path.write_text(document)
                self.assertEqual(self.status_of(self.doctor(), "controller-roles"), FAIL)

    def test_ownership_file_is_validated(self):
        path = self.paths.state_controllers.parent / "ownership.json"
        self.assertEqual(self.status_of(self.doctor(), "ownership"), PASS)
        self.assertIn("no owners recorded; every Bot and Group is shared",
                      self.messages_of(self.doctor(), "ownership")[0])
        identity = "0000000a-0000-4000-8000-00000000000b"
        path.write_text(json.dumps({
            "version": 1,
            "bots": {identity: {"owner": "xuid:2535400000000001", "ownerName": "Alice", "visibility": "shared"}},
            "groups": {},
        }))
        self.assertEqual(self.status_of(self.doctor(), "ownership"), PASS)
        self.assertIn("records 1 Bot and 0 Groups", self.messages_of(self.doctor(), "ownership")[0])
        corrupt = (
            "{invalid",
            "",
            "[]",
            json.dumps({"version": 2, "bots": {}, "groups": {}}),
            json.dumps({"bots": {}, "groups": {}}),
            json.dumps({"version": 1, "bots": {}, "groups": {}, "extra": True}),
            json.dumps({"version": 1, "bots": [], "groups": {}}),
            json.dumps({"version": 1, "bots": {identity: []}, "groups": {}}),
            json.dumps({"version": 1, "bots": {identity: {"owner": "nobody", "visibility": "shared"}}}),
            json.dumps({"version": 1, "bots": {identity: {"owner": "xuid:1", "visibility": "public"}}}),
            json.dumps({"version": 1, "bots": {identity: {"owner": "xuid:1", "visibility": "shared",
                                                         "ownerName": "x" * 65}}}),
            json.dumps({"version": 1, "bots": {"not-a-uuid": {"owner": "xuid:1", "visibility": "shared"}}}),
            json.dumps({"version": 1, "bots": {
                identity: {"owner": "xuid:1", "visibility": "shared"},
                identity.upper(): {"owner": "xuid:2", "visibility": "private"},
            }}),
        )
        for document in corrupt:
            with self.subTest(document=document[:80]):
                path.write_text(document)
                self.assertEqual(self.status_of(self.doctor(), "ownership"), FAIL)
                self.assertIn("endbotd will not start", self.messages_of(self.doctor(), "ownership")[0])

    def test_corrupt_policy_is_reported_as_failure(self):
        (self.paths.state_controllers.parent / "controller-policy.json").write_text("{invalid")
        self.assertEqual(self.status_of(self.doctor(), "controllers-effective"), FAIL)

    def test_live_report_comes_from_plugin_instead_of_saved_config(self):
        from unittest.mock import patch

        from endbot_cli.runstate import RUNNING, SupervisorState

        with (
            patch("endbot_cli.runstate.inspect_supervisor", return_value=SupervisorState(RUNNING, 123)),
            patch(
                "endbot_cli.endbotd_client.controllers_request",
                return_value={
                    "ok": True,
                    "entries": [{"name": "LiveOwner", "state": "revoked", "reason": "revoked UUID"}],
                },
            ) as request,
        ):
            messages = self.messages_of(self.doctor(live=True), "controllers-effective")
        self.assertEqual(messages, ["LiveOwner: revoked - revoked UUID (live)"])
        request.assert_called_once()


class OperatorTests(DoctorTestCase):
    """The read-only `operators` check (section 7): operators can cheat without losing achievements."""

    PROPERTIES = "online-mode=true\nallow-cheats=false\ntransport=nethernet\nlevel-name=world\n"

    def write_permissions(self, document: object) -> None:
        (self.server / "permissions.json").write_text(json.dumps(document), encoding="utf-8")

    def test_no_permissions_file_passes(self) -> None:
        self.assertEqual(self.status_of(self.doctor(), "operators"), PASS)

    def test_member_entries_pass(self) -> None:
        self.write_permissions([{"permission": "member", "xuid": "1"}])
        self.assertEqual(self.status_of(self.doctor(), "operators"), PASS)

    def test_operator_entry_warns_without_failing(self) -> None:
        self.write_permissions([{"permission": "operator", "xuid": "1"}, {"permission": "member", "xuid": "2"}])
        results = self.doctor()
        self.assertEqual(exit_code(results), 0)
        self.assertEqual(self.status_of(results, "operators"), WARN)
        message = self.messages_of(results, "operators")[0]
        self.assertIn("1 operator entry", message)
        self.assertIn("achievements enabled", message)

    def test_default_operator_permission_level_warns(self) -> None:
        (self.server / "server.properties").write_text(
            self.PROPERTIES + "default-player-permission-level=operator\n", encoding="utf-8"
        )
        results = self.doctor()
        self.assertEqual(self.status_of(results, "operators"), WARN)
        self.assertIn("every player an operator", self.messages_of(results, "operators")[0])

    def test_malformed_permissions_warns(self) -> None:
        (self.server / "permissions.json").write_text("{not json", encoding="utf-8")
        self.assertEqual(self.status_of(self.doctor(), "operators"), WARN)
        self.write_permissions({"permission": "operator"})
        self.assertEqual(self.status_of(self.doctor(), "operators"), WARN)


class RuntimeLiveTests(DoctorTestCase):
    def start_control_server(self, token: str) -> socketserver.ThreadingTCPServer:
        server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), ControlHandler)
        server.token = token
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        return server

    def test_live_ping_reaches_the_runtime(self) -> None:
        control = self.start_control_server("test-control-token-0123456789abcdef0123456789")
        write_endbot_toml(self.root, control_port=control.server_address[1])
        results = self.doctor(live=True)
        self.assertEqual(self.status_of(results, "runtime"), PASS)
        self.assertIn("ping", self.messages_of(results, "runtime")[0])

    def test_live_without_runtime_fails(self) -> None:
        free_socket = socket.socket()
        free_socket.bind(("127.0.0.1", 0))
        port = free_socket.getsockname()[1]
        free_socket.close()
        write_endbot_toml(self.root, control_port=port)
        results = self.doctor(live=True)
        self.assertEqual(self.status_of(results, "runtime"), FAIL)
        self.assertEqual(exit_code(results), 1)

    def test_without_live_is_skipped(self) -> None:
        results = self.doctor()
        self.assertEqual(self.status_of(results, "runtime"), SKIP)


class CapacityTests(DoctorTestCase):
    """The `capacity` check reports the Bot slot budget from server.properties and [limits]."""

    def write_limits(self, text: str) -> None:
        path = self.server / "plugins" / "endbot" / "config.toml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def set_max_players(self, value: str) -> None:
        path = self.server / "server.properties"
        text = path.read_text(encoding="utf-8")
        if re.search(r"(?m)^max-players=", text):
            text = re.sub(r"(?m)^max-players=.*$", f"max-players={value}", text)
        else:
            text = text.rstrip("\n") + f"\nmax-players={value}\n"
        path.write_text(text, encoding="utf-8")

    def test_the_default_budget_reserves_one_slot(self) -> None:
        message = self.messages_of(self.doctor(), "capacity")[0]
        self.assertIn("10 slots", message)
        self.assertIn("1 reserved for players", message)
        self.assertIn("Bots up to 9", message)

    def test_no_limit_warns(self) -> None:
        self.write_limits("[limits]\nmax-bots = 0\nreserved-player-slots = 0\n")
        self.assertEqual(self.status_of(self.doctor(), "capacity"), WARN)

    def test_a_reserve_covering_every_slot_warns(self) -> None:
        self.set_max_players("2")
        self.write_limits("[limits]\nreserved-player-slots = 2\n")
        self.assertEqual(self.status_of(self.doctor(), "capacity"), WARN)

    def test_an_unreachable_bot_cap_warns(self) -> None:
        self.set_max_players("4")
        self.write_limits("[limits]\nmax-bots = 8\nreserved-player-slots = 1\n")
        self.assertEqual(self.status_of(self.doctor(), "capacity"), WARN)

    def test_limits_that_are_not_a_table_warn_instead_of_crashing(self) -> None:
        self.write_limits("limits = 5\n")
        self.assertEqual(self.status_of(self.doctor(), "capacity"), WARN)
        self.assertIn("must be a table", self.messages_of(self.doctor(), "capacity")[0])


if __name__ == "__main__":
    unittest.main()
