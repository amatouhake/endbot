"""Tests for endbot.toml loading and validation (docs/OPERATIONS.md section 2)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from endbot_cli.config import DEFAULT_CONTROL_PORT, ConfigError, load_config


class ConfigTests(unittest.TestCase):
    def test_hotbar_sync_settings_defaults_and_bounds(self):
        base = "[server]\npath='server'\n[controllers]\ngamertags=[]\n"
        self.write(base)
        self.assertEqual(load_config(self.path).runtime.hotbar_sync_interval_ticks, 4)
        self.assertEqual(load_config(self.path).runtime.hotbar_sync_resend_ticks, 20)
        for key, attribute, maximum in (
            ("hotbar-sync-interval-ticks", "hotbar_sync_interval_ticks", 1200),
            ("hotbar-sync-resend-ticks", "hotbar_sync_resend_ticks", 72000),
        ):
            for value in (1, 10, maximum):
                self.write(base + f"[runtime]\n{key}={value}\n")
                self.assertEqual(getattr(load_config(self.path).runtime, attribute), value)
            for value in ("0", str(maximum + 1), "true", '"4"', "4.5"):
                self.write(base + f"[runtime]\n{key}={value}\n")
                with self.subTest(key=key, value=value), self.assertRaisesRegex(ConfigError, key):
                    load_config(self.path)

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.path = self.root / "endbot.toml"

    def write(self, text: str) -> None:
        self.path.write_text(text, encoding="utf-8")

    def test_valid_config_loads(self) -> None:
        self.write("[server]\npath = 'server'\n\n[controllers]\ngamertags = [\"ExampleTag\"]\n")
        config = load_config(self.path)
        self.assertEqual(config.server.path, "server")
        self.assertEqual(config.server.transport, "nethernet")
        self.assertEqual(config.controllers.gamertags, ("ExampleTag",))
        self.assertEqual(config.runtime.control_port, DEFAULT_CONTROL_PORT)

    def test_transport_is_explicit_and_validated(self) -> None:
        for transport in ('"raknet"', '"nethernet"'):
            self.write(f"[server]\npath='server'\ntransport={transport}\n[controllers]\ngamertags=[]\n")
            self.assertEqual(load_config(self.path).server.transport, transport.strip('"'))
        for transport in ('"auto"', '"RakNet"', '""', "1", "true", "[]"):
            with self.subTest(transport=transport):
                self.write(f"[server]\npath='server'\ntransport={transport}\n[controllers]\ngamertags=[]\n")
                with self.assertRaisesRegex(ConfigError, "server.transport"):
                    load_config(self.path)

    def test_control_port_is_configurable(self) -> None:
        self.write("[server]\npath = 'server'\n[controllers]\ngamertags = []\n[runtime]\ncontrol-port = 1\n")
        config = load_config(self.path)
        self.assertEqual(config.runtime.control_port, 1)

    def test_chunk_radius_defaults_and_bounds(self) -> None:
        base = "[server]\npath='server'\n[controllers]\ngamertags=[]\n"
        self.write(base)
        self.assertEqual(load_config(self.path).runtime.chunk_radius, 5)
        for value in (2, 10, 32):
            self.write(base + f"[runtime]\nchunk-radius={value}\n")
            self.assertEqual(load_config(self.path).runtime.chunk_radius, value)
        for value in ("1", "33", "2.5", '"2"', "true"):
            with self.subTest(value=value):
                self.write(base + f"[runtime]\nchunk-radius={value}\n")
                with self.assertRaisesRegex(ConfigError, "runtime.chunk-radius"):
                    load_config(self.path)

    def test_ui_language_defaults_to_auto_and_is_validated(self) -> None:
        base = "[server]\npath='server'\n[controllers]\ngamertags=[]\n"
        self.write(base)
        self.assertEqual(load_config(self.path).ui.language, "auto")
        for value in ("auto", "en", "ja"):
            self.write(base + f'[ui]\nlanguage="{value}"\n')
            self.assertEqual(load_config(self.path).ui.language, value)
        for value in ('"fr"', '"JA"', '""', "1", "true"):
            with self.subTest(value=value):
                self.write(base + f"[ui]\nlanguage={value}\n")
                with self.assertRaisesRegex(ConfigError, "ui.language"):
                    load_config(self.path)
        self.write(base + '[ui]\nlocale="ja"\n')
        with self.assertRaisesRegex(ConfigError, "ui.locale"):
            load_config(self.path)

    def test_absolute_server_path_is_kept(self) -> None:
        self.write(f"[server]\npath = '{self.root.as_posix()}'\n[controllers]\ngamertags = []\n")
        config = load_config(self.path)
        resolved = config.server.resolve(self.root)
        self.assertTrue(resolved.is_absolute())
        self.assertEqual(resolved, self.root)

    def test_relative_server_path_resolves_against_the_instance(self) -> None:
        self.write("[server]\npath = 'server'\n[controllers]\ngamertags = []\n")
        config = load_config(self.path)
        self.assertEqual(config.server.resolve(self.root), self.root / "server")

    def test_missing_file_names_the_fix(self) -> None:
        with self.assertRaises(ConfigError) as caught:
            load_config(self.path)
        message = str(caught.exception)
        self.assertIn("endbot.toml", message)
        self.assertIn("missing", message)
        self.assertIn("endbot setup", message)

    def test_invalid_toml_names_the_file(self) -> None:
        self.write("[server\npath = 'server'\n")
        with self.assertRaises(ConfigError) as caught:
            load_config(self.path)
        self.assertIn("invalid TOML", str(caught.exception))

    def test_numeric_gamertag_is_rejected_with_quoting_hint(self) -> None:
        self.write("[server]\npath = 'server'\n[controllers]\ngamertags = [1234]\n")
        with self.assertRaises(ConfigError) as caught:
            load_config(self.path)
        message = str(caught.exception)
        self.assertIn("controllers.gamertags[0]", message)
        self.assertIn("quote", message)
        self.assertIn("1234", message)

    def test_float_gamertag_is_rejected(self) -> None:
        self.write("[server]\npath = 'server'\n[controllers]\ngamertags = [1.5]\n")
        with self.assertRaises(ConfigError) as caught:
            load_config(self.path)
        self.assertIn("quote", str(caught.exception))

    def test_empty_gamertag_is_rejected(self) -> None:
        self.write("[server]\npath = 'server'\n[controllers]\ngamertags = [\"\"]\n")
        with self.assertRaises(ConfigError) as caught:
            load_config(self.path)
        self.assertIn("empty", str(caught.exception))

    def test_non_array_gamertags_is_rejected(self) -> None:
        self.write("[server]\npath = 'server'\n[controllers]\ngamertags = \"ExampleTag\"\n")
        with self.assertRaises(ConfigError) as caught:
            load_config(self.path)
        self.assertIn("controllers.gamertags", str(caught.exception))

    def test_unknown_top_level_key_is_named(self) -> None:
        self.write("[server]\npath = 'server'\n[controllers]\ngamertags = []\n[contro]\nport = 1\n")
        with self.assertRaises(ConfigError) as caught:
            load_config(self.path)
        self.assertIn("contro", str(caught.exception))

    def test_unknown_section_key_is_named(self) -> None:
        self.write("[server]\npath = 'server'\npaht = 'x'\n[controllers]\ngamertags = []\n")
        with self.assertRaises(ConfigError) as caught:
            load_config(self.path)
        self.assertIn("server.paht", str(caught.exception))

    def test_missing_server_path_is_named(self) -> None:
        self.write("[server]\n[controllers]\ngamertags = []\n")
        with self.assertRaises(ConfigError) as caught:
            load_config(self.path)
        self.assertIn("server.path", str(caught.exception))

    def test_missing_controllers_section_is_named(self) -> None:
        self.write("[server]\npath = 'server'\n")
        with self.assertRaises(ConfigError) as caught:
            load_config(self.path)
        self.assertIn("controllers", str(caught.exception))

    def test_control_port_bounds(self) -> None:
        for value in ("0", "65536", '"19142"', "true"):
            with self.subTest(value=value):
                self.write(
                    f"[server]\npath = 'server'\n[controllers]\ngamertags = []\n[runtime]\ncontrol-port = {value}\n"
                )
                with self.assertRaises(ConfigError) as caught:
                    load_config(self.path)
                self.assertIn("runtime.control-port", str(caught.exception))
        self.write("[server]\npath = 'server'\n[controllers]\ngamertags = []\n[runtime]\ncontrol-port = 65535\n")
        self.assertEqual(load_config(self.path).runtime.control_port, 65535)


if __name__ == "__main__":
    unittest.main()
