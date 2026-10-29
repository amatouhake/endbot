"""Tests for derived config generation (docs/OPERATIONS.md section 2)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from fakeinstance import build_instance, write_endbot_toml, write_endstone_toml

from endbot_cli.compat import tomllib
from endbot_cli.config import load_config
from endbot_cli.configgen import (
    DEFAULT_SERVER_PORT,
    generate_all,
    generate_endbotd_config,
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
    def test_hotbar_settings_reach_generated_plugin_config(self):
        with self.paths.endbot_toml.open("a", encoding="utf-8") as config:
            config.write("hotbar-sync-interval-ticks=10\nhotbar-sync-resend-ticks=40\n")
        target, warnings = generate_endbotd_config(self.server, self.paths, self.config())
        plugin = tomllib.loads(target.read_text(encoding="utf-8"))["plugin"]
        self.assertEqual(plugin["hotbar-sync-interval-ticks"], 10)
        self.assertEqual(plugin["hotbar-sync-resend-ticks"], 40)
        self.assertEqual(warnings, [])

    def test_ui_language_reaches_generated_plugin_config(self):
        target, _ = generate_endbotd_config(self.server, self.paths, self.config())
        self.assertEqual(tomllib.loads(target.read_text(encoding="utf-8"))["ui"], {"language": "auto"})
        with self.paths.endbot_toml.open("a", encoding="utf-8") as config:
            config.write('\n[ui]\nlanguage = "ja"\n')
        target, _ = generate_endbotd_config(self.server, self.paths, self.config())
        self.assertEqual(tomllib.loads(target.read_text(encoding="utf-8"))["ui"], {"language": "ja"})

    def test_limits_reach_generated_plugin_config(self):
        target, _ = generate_endbotd_config(self.server, self.paths, self.config())
        limits = tomllib.loads(target.read_text(encoding="utf-8"))["limits"]
        self.assertEqual(limits, {"max-bots": 0, "reserved-player-slots": 1})
        with self.paths.endbot_toml.open("a", encoding="utf-8") as config:
            config.write("\n[limits]\nmax-bots = 6\nreserved-player-slots = 2\n")
        target, _ = generate_endbotd_config(self.server, self.paths, self.config())
        limits = tomllib.loads(target.read_text(encoding="utf-8"))["limits"]
        self.assertEqual(limits, {"max-bots": 6, "reserved-player-slots": 2})

    def test_chunk_radius_reaches_generated_runtime_config(self) -> None:
        for radius in (2, 6):
            write_endbot_toml(self.root)
            with self.paths.endbot_toml.open("a", encoding="utf-8") as config:
                config.write(f"chunk-radius = {radius}\n")
            target = generate_runtime_config(self.paths, self.config(), server_port=19132)
            self.assertEqual(json.loads(target.read_text())["chunkRadius"], radius)

    def test_raknet_generation_uses_the_selected_port_without_discovery_identity(self) -> None:
        from unittest.mock import patch

        write_endbot_toml(self.root, transport="raknet")
        (self.server / "server.properties").write_text("transport=raknet\nserver-port=20000\n", encoding="utf-8")
        with patch("endbot_cli.configgen.read_server_identity", side_effect=AssertionError("must not discover")):
            generated = generate_all(self.config(), self.paths, self.server)
        document = json.loads(generated.runtime_config.read_text())
        self.assertEqual(document["transport"], "raknet")
        self.assertEqual(document["serverPort"], 20000)
        self.assertNotIn("serverName", document)
        self.assertNotIn("levelName", document)

    def test_raknet_invalid_port_cannot_silently_dial_the_default_server(self) -> None:
        from endbot_cli.configgen import ConfigGenerationError

        write_endbot_toml(self.root, transport="raknet")
        (self.server / "server.properties").write_text("transport=raknet\nserver-port=oops\n", encoding="utf-8")
        with self.assertRaisesRegex(ConfigGenerationError, "valid server-port"):
            generate_all(self.config(), self.paths, self.server)

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

    def test_server_identity_prefers_level_dat_display_name(self) -> None:
        import fakenbt

        (self.server / "server.properties").write_text("server-name=biiSMP\nlevel-name=local_world\n", encoding="utf-8")
        world = self.server / "worlds" / "local_world"
        world.mkdir(parents=True)
        (world / "level.dat").write_bytes(fakenbt.level_dat({"LevelName": fakenbt.string("マイ ワールド")}))
        self.assertEqual(read_server_identity(self.server), ("biiSMP", "マイ ワールド"))

    def test_server_identity_falls_back_to_level_name_without_level_dat(self) -> None:
        (self.server / "server.properties").write_text("server-name=biiSMP\nlevel-name=local_world\n", encoding="utf-8")
        self.assertEqual(read_server_identity(self.server), ("biiSMP", "local_world"))

    def test_server_identity_ignores_corrupt_level_dat(self) -> None:
        (self.server / "server.properties").write_text("server-name=biiSMP\nlevel-name=local_world\n", encoding="utf-8")
        world = self.server / "worlds" / "local_world"
        world.mkdir(parents=True)
        (world / "level.dat").write_bytes(b"not a level.dat")
        self.assertEqual(read_server_identity(self.server), ("biiSMP", "local_world"))

    def test_server_identity_reads_default_world_when_level_name_is_missing(self) -> None:
        import fakenbt

        (self.server / "server.properties").write_text("server-name=biiSMP\n", encoding="utf-8")
        world = self.server / "worlds" / "Bedrock level"
        world.mkdir(parents=True)
        (world / "level.dat").write_bytes(fakenbt.level_dat({"LevelName": fakenbt.string("マイ ワールド")}))
        self.assertEqual(read_server_identity(self.server), ("biiSMP", "マイ ワールド"))


class PluginConfigTests(ConfigGenTestCase):
    def test_scripting_os_limit_overrides_reach_plugin_and_defaults_return(self):
        with self.paths.endbot_toml.open("a", encoding="utf-8") as config:
            config.write("\n[scripting]\nos-confinement=false\nprocess-memory-bytes=0\ncpu-seconds=3\nbook-export=false\n"
                         "chat-logs=false\nui-pack-editor=true\nreplies=\"all\"\n")
        target, _ = generate_endbotd_config(self.server, self.paths, self.config())
        table = tomllib.loads(target.read_text(encoding="utf-8"))["scripting"]
        self.assertFalse(table["os-confinement"])
        self.assertEqual(table["process-memory-bytes"], 0)
        self.assertEqual(table["cpu-seconds"], 3)
        self.assertFalse(table["book-export"])
        self.assertFalse(table["chat-logs"])
        self.assertTrue(table["ui-pack-editor"])
        self.assertEqual(table["replies"], "all")
        text = self.paths.endbot_toml.read_text(encoding="utf-8")
        self.paths.endbot_toml.write_text(
            text.replace("os-confinement=false", "os-confinement=true")
                .replace("process-memory-bytes=0", "process-memory-bytes=268435456")
                .replace("cpu-seconds=3", "cpu-seconds=60").replace("book-export=false", "book-export=true")
                .replace("chat-logs=false", "chat-logs=true").replace("ui-pack-editor=true", "ui-pack-editor=false")
                .replace('replies="all"', 'replies="failures"'),
            encoding="utf-8",
        )
        target, _ = generate_endbotd_config(self.server, self.paths, self.config())
        table = tomllib.loads(target.read_text(encoding="utf-8"))["scripting"]
        self.assertNotIn("os-confinement", table)
        self.assertNotIn("process-memory-bytes", table)
        self.assertNotIn("cpu-seconds", table)
        self.assertNotIn("book-export", table)
        self.assertNotIn("chat-logs", table)
        self.assertNotIn("ui-pack-editor", table)
        self.assertNotIn("replies", table)

    def test_bundled_runner_is_absolute_and_scripting_stays_disabled(self):
        version = "test-version"
        self.paths.app_current.parent.mkdir(parents=True)
        self.paths.app_current.write_text(version + "\n", encoding="utf-8")
        runner = self.root / "app" / version / "runner" / (
            "endbot-luau-runner.exe" if sys.platform == "win32" else "endbot-luau-runner"
        )
        runner.parent.mkdir(parents=True)
        runner.write_bytes(b"fixture-runner")
        target, _ = generate_endbotd_config(self.server, self.paths, self.config())
        scripting = tomllib.loads(target.read_text(encoding="utf-8"))["scripting"]
        self.assertEqual(scripting, {"enabled": False, "runner": str(runner.resolve())})

    def test_unbundled_runner_keeps_plugin_default_and_explicit_override_wins(self):
        target, _ = generate_endbotd_config(self.server, self.paths, self.config())
        self.assertEqual(tomllib.loads(target.read_text(encoding="utf-8"))["scripting"], {"enabled": False})
        with self.paths.endbot_toml.open("a", encoding="utf-8") as config:
            config.write("\n[scripting]\nenabled=true\nrunner='custom/runner'\n")
        target, _ = generate_endbotd_config(self.server, self.paths, self.config())
        self.assertEqual(
            tomllib.loads(target.read_text(encoding="utf-8"))["scripting"],
            {"enabled": True, "runner": "custom/runner"},
        )

    def test_running_app_runner_takes_precedence_over_current_pointer(self):
        from unittest.mock import patch

        running = self.root / "app" / "running"
        module = running / "python" / "Lib" / "site-packages" / "endbot_cli" / "configgen.py"
        binary = running / "runner" / (
            "endbot-luau-runner.exe" if sys.platform == "win32" else "endbot-luau-runner"
        )
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b"running-version")
        with patch("endbot_cli.configgen.__file__", str(module)):
            target, _ = generate_endbotd_config(self.server, self.paths, self.config())
        self.assertEqual(
            tomllib.loads(target.read_text(encoding="utf-8"))["scripting"]["runner"], str(binary.resolve())
        )

    def test_gamertags_and_paths_are_written_with_literal_paths(self) -> None:
        write_endbot_toml(self.root, gamertags='["First", "Second"]', control_port=19999)
        target, warnings = generate_endbotd_config(self.server, self.paths, self.config())
        self.assertEqual(warnings, [])
        text = target.read_text(encoding="utf-8")
        document = tomllib.loads(text)
        self.assertEqual(document["authorization"]["controller-gamertags"], ["First", "Second"])
        self.assertEqual(document["authorization"]["controllers-file"], str(self.paths.state_controllers.resolve()))
        self.assertEqual(
            document["authorization"]["controller-policy-file"],
            str((self.paths.state_controllers.parent / "controller-policy.json").resolve()),
        )
        self.assertEqual(document["authorization"]["allowed-uuids"], [])
        self.assertEqual(
            document["authorization"]["controller-requests-directory"],
            str((self.paths.state_run / "controller-requests").resolve()),
        )
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
        generated, _ = generate_endbotd_config(self.server, self.paths, self.config())
        document = tomllib.loads(generated.read_text(encoding="utf-8"))
        self.assertEqual(document["authorization"]["allowed-uuids"], ["11111111-2222-3333-4444-555555555555"])
        self.assertEqual(document["authorization"]["allowed-xuids"], ["2535412345678901"])
        # Once the plugin config holds only the link, endbotd.toml carries them forward.
        generate_plugin_config(self.server, self.paths, self.config())
        self.assertNotIn("authorization", tomllib.loads(target.read_text(encoding="utf-8")))
        generated, _ = generate_endbotd_config(self.server, self.paths, self.config())
        document = tomllib.loads(generated.read_text(encoding="utf-8"))
        self.assertEqual(document["authorization"]["allowed-xuids"], ["2535412345678901"])

    def test_unparseable_legacy_file_warns_and_defaults_to_empty(self) -> None:
        target = self.server / "plugins" / "endbot" / "config.toml"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("{not toml", encoding="utf-8")
        generated, warnings = generate_endbotd_config(self.server, self.paths, self.config())
        self.assertEqual(len(warnings), 1)
        self.assertIn("not preserved", warnings[0])
        document = tomllib.loads(generated.read_text(encoding="utf-8"))
        self.assertEqual(document["authorization"]["allowed-uuids"], [])


class EndstoneConfigTests(ConfigGenTestCase):
    def test_other_tables_keys_and_comments_are_preserved(self) -> None:
        write_endstone_toml(self.server)
        target = self.server / "endstone.toml"
        target.write_text(
            "# operator header comment\n"
            "[local-bot-auth]\n"
            "enabled = false # kept comment\n"
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


class EndbotdTokenTests(ConfigGenTestCase):
    def test_plugin_config_holds_only_the_link_and_the_token_is_made_once(self) -> None:
        generated = generate_all(self.config(), self.paths, self.server)
        plugin = tomllib.loads(generated.plugin_config.read_text(encoding="utf-8"))
        self.assertEqual(set(plugin), {"endbotd"})
        self.assertEqual(plugin["endbotd"]["port"], 19143)
        self.assertEqual(Path(plugin["endbotd"]["token-file"]), self.paths.endbotd_token.resolve())
        token = self.paths.endbotd_token.read_text(encoding="utf-8")
        self.assertGreaterEqual(len(token.strip()), 32)
        self.assertNotIn(token.strip(), "\n".join(generated.messages))
        generate_all(self.config(), self.paths, self.server)
        self.assertEqual(self.paths.endbotd_token.read_text(encoding="utf-8"), token)
        endbotd = tomllib.loads(generated.endbotd_config.read_text(encoding="utf-8"))
        self.assertEqual(endbotd["link"]["port"], 19143)
        self.assertEqual(Path(endbotd["plugin"]["data-folder"]), (self.server / "plugins" / "endbot").resolve())
