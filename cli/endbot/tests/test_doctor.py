"""End-to-end tests for `endbot doctor` against synthetic instances (§7)."""

from __future__ import annotations

import json
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
            "controllers": WARN,
            "runtime": SKIP,
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
                        {"gamertag": "exampletag", "xuid": "1", "uuid": "u", "boundAt": "2026-09-19T00:00:00Z"},
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
        (plugins / "config.toml").write_text("[runtime]\nport = 19142\n", encoding="utf-8")
        results = self.doctor()
        self.assertEqual(self.status_of(results, "plugin-port"), PASS)
        (plugins / "config.toml").write_text("[runtime]\nport = 25565\n", encoding="utf-8")
        results = self.doctor()
        self.assertEqual(self.status_of(results, "plugin-port"), FAIL)
        self.assertEqual(exit_code(results), 1)


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


if __name__ == "__main__":
    unittest.main()
