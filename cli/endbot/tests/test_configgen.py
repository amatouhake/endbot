"""Tests for derived config generation (docs/OPERATIONS.md section 2)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from fakeinstance import build_instance, write_endbot_toml, write_endstone_toml

from endbot_cli.compat import tomllib
from endbot_cli.config import load_config
from endbot_cli.configgen import (
    DEFAULT_SERVER_PORT,
    generate_all,
    generate_endstone_config,
    generate_plugin_config,
    generate_runtime_config,
    read_server_identity,
    read_server_port,
)
from endbot_cli.instance import InstancePaths
from endbot_cli.lock import load_lock


class ConfigGenTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.paths: InstancePaths = build_instance(self.root, load_lock())
        self.server = self.root / "server"

    def config(self):
        return load_config(self.paths.endbot_toml)


class RuntimeConfigTests(ConfigGenTestCase):
    def test_paths_are_absolute_and_values_come_from_the_instance(self) -> None:
        write_endbot_toml(self.root, control_port=19999)
        (self.server / "server.properties").write_text(
            "online-mode=true\nallow-cheats=false\nserver-port=20000\n", encoding="utf-8"
        )
        target = generate_runtime_config(self.paths, self.config(), server_port=20000)
        document = json.loads(target.read_text(encoding="utf-8"))
        state = self.root / "state"
        self.assertEqual(document["dataDirectory"], str(state.resolve()))
        self.assertEqual(document["controlTokenPath"], str((state / "secrets" / "control.token").resolve()))
        self.assertEqual(document["ownerPrivateKeyPath"], str((state / "secrets" / "owner-private.pem").resolve()))
        self.assertEqual(document["ownerPublicKeyPath"], str((state / "secrets" / "owner-public.pem").resolve()))
        self.assertEqual(document["controlHost"], "127.0.0.1")
        self.assertEqual(document["controlPort"], 19999)
        self.assertEqual(document["serverHost"], "127.0.0.1")
        self.assertEqual(document["serverPort"], 20000)
        for key in ("dataDirectory", "controlTokenPath", "ownerPrivateKeyPath", "ownerPublicKeyPath"):
            self.assertTrue(Path(document[key]).is_absolute(), key)

    def test_server_port_falls_back_to_the_bds_default(self) -> None:
        port, warnings = read_server_port(self.server)
        self.assertEqual(port, DEFAULT_SERVER_PORT)
        self.assertEqual(warnings, [])
        (self.server / "server.properties").write_text("online-mode=true\nserver-port=not-a-port\n", encoding="utf-8")
        port, warnings = read_server_port(self.server)
        self.assertEqual(port, DEFAULT_SERVER_PORT)
        self.assertEqual(len(warnings), 1)
        self.assertIn("server-port", warnings[0])


    def test_server_identity_comes_from_server_properties_with_bds_defaults(self) -> None:
        (self.server / "server.properties").unlink(missing_ok=True)
        self.assertEqual(read_server_identity(self.server), ("Dedicated Server", "Bedrock level"))
        (self.server / "server.properties").write_text(
            "server-name=Endstone Server\nlevel-name=My World\n", encoding="utf-8"
        )
        self.assertEqual(read_server_identity(self.server), ("Endstone Server", "My World"))
        write_endbot_toml(self.root)
        target = generate_runtime_config(
            self.paths, self.config(), server_port=19132, server_identity=read_server_identity(self.server)
        )
        document = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual((document["serverName"], document["levelName"]), ("Endstone Server", "My World"))


class PluginConfigTests(ConfigGenTestCase):
    def test_gamertags_and_paths_are_written_with_literal_paths(self) -> None:
        write_endbot_toml(self.root, gamertags='["First", "Second"]', control_port=19999)
        target, warnings = generate_plugin_config(self.server, self.paths, self.config())
        self.assertEqual(warnings, [])
        text = target.read_text(encoding="utf-8")
        document = tomllib.loads(text)
        self.assertEqual(document["authorization"]["controller-gamertags"], ["First", "Second"])
        self.assertEqual(document["authorization"]["controllers-file"], str(self.paths.state_controllers.resolve()))
        self.assertEqual(document["authorization"]["allowed-uuids"], [])
        self.assertEqual(document["authorization"]["allowed-xuids"], [])
        self.assertEqual(document["runtime"]["token-file"], str(self.paths.control_token.resolve()))
        self.assertEqual(document["runtime"]["host"], "127.0.0.1")
        self.assertEqual(document["runtime"]["port"], 19999)
        self.assertEqual(document["runtime"]["timeout-seconds"], 10.0)
        # Paths are TOML literal strings so Windows backslashes need no escaping.
        self.assertIn("= '", text)

    def test_legacy_pre_bound_entries_are_preserved(self) -> None:
        target = self.server / "plugins" / "endbot" / "config.toml"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            "[authorization]\n"
            'allowed-uuids = ["11111111-2222-3333-4444-555555555555"]\n'
            'allowed-xuids = ["2535412345678901", 7]\n',
            encoding="utf-8",
        )
        generate_plugin_config(self.server, self.paths, self.config())
        document = tomllib.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(document["authorization"]["allowed-uuids"], ["11111111-2222-3333-4444-555555555555"])
        self.assertEqual(document["authorization"]["allowed-xuids"], ["2535412345678901"])

    def test_unparseable_legacy_file_warns_and_defaults_to_empty(self) -> None:
        target = self.server / "plugins" / "endbot" / "config.toml"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("{not toml", encoding="utf-8")
        _target, warnings = generate_plugin_config(self.server, self.paths, self.config())
        self.assertEqual(len(warnings), 1)
        self.assertIn("not preserved", warnings[0])
        document = tomllib.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(document["authorization"]["allowed-uuids"], [])


class EndstoneConfigTests(ConfigGenTestCase):
    def test_other_tables_keys_and_comments_are_preserved(self) -> None:
        write_endstone_toml(self.server)
        target = self.server / "endstone.toml"
        target.write_text(
            "# operator header comment\n"
            "[local-bot-auth]\n"
            'enabled = false # kept comment\n'
            'public-key-file = "old.pem"\n'
            'operator-note = "keep me"\n'
            "\n"
            "[other-table]\n"
            'value = "untouched"\n',
            encoding="utf-8",
        )
        generate_endstone_config(self.server, self.paths)
        text = target.read_text(encoding="utf-8")
        self.assertIn("# operator header comment", text)
        self.assertIn("# kept comment", text)
        self.assertIn('operator-note = "keep me"', text)
        self.assertIn('value = "untouched"', text)
        document = tomllib.loads(text)
        section = document["local-bot-auth"]
        self.assertIs(section["enabled"], True)
        self.assertEqual(section["issuer"], "endbot://local-bot")
        self.assertEqual(section["audience"], "endstone://local-bot")
        self.assertEqual(section["clock-skew-seconds"], 5)
        self.assertEqual(section["max-token-lifetime-seconds"], 120)
        self.assertEqual(section["public-key-file"], str(self.paths.owner_public_key.resolve()))
        self.assertTrue(Path(section["public-key-file"]).is_absolute())
        self.assertIn("= '", text)

    def test_missing_endstone_toml_is_created_with_only_local_bot_auth(self) -> None:
        target = self.server / "endstone.toml"
        target.unlink()
        generate_endstone_config(self.server, self.paths)
        document = tomllib.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(list(document), ["local-bot-auth"])
        self.assertIs(document["local-bot-auth"]["enabled"], True)

    def test_unparseable_endstone_toml_refuses_generation(self) -> None:
        target = self.server / "endstone.toml"
        target.write_text("{not toml", encoding="utf-8")
        with self.assertRaisesRegex(Exception, "endstone.toml"):
            generate_endstone_config(self.server, self.paths)


class GenerateAllTests(ConfigGenTestCase):
    def test_generate_all_reports_every_generated_file(self) -> None:
        generated = generate_all(self.config(), self.paths, self.server)
        self.assertTrue(generated.runtime_config.is_file())
        self.assertTrue(generated.plugin_config.is_file())
        self.assertTrue(generated.endstone_config.is_file())
        self.assertTrue(any(message.startswith("PASS config-gen:") for message in generated.messages))


if __name__ == "__main__":
    unittest.main()
