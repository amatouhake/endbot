"""Tests for endbot.toml loading and validation (docs/OPERATIONS.md §2)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from endbot_cli.config import DEFAULT_CONTROL_PORT, ConfigError, load_config


class ConfigTests(unittest.TestCase):
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
        self.assertEqual(config.controllers.gamertags, ("ExampleTag",))
        self.assertEqual(config.runtime.control_port, DEFAULT_CONTROL_PORT)

    def test_control_port_is_configurable(self) -> None:
        self.write("[server]\npath = 'server'\n[controllers]\ngamertags = []\n[runtime]\ncontrol-port = 1\n")
        config = load_config(self.path)
        self.assertEqual(config.runtime.control_port, 1)

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
                    "[server]\npath = 'server'\n[controllers]\ngamertags = []\n"
                    f"[runtime]\ncontrol-port = {value}\n"
                )
                with self.assertRaises(ConfigError) as caught:
                    load_config(self.path)
                self.assertIn("runtime.control-port", str(caught.exception))
        self.write("[server]\npath = 'server'\n[controllers]\ngamertags = []\n[runtime]\ncontrol-port = 65535\n")
        self.assertEqual(load_config(self.path).runtime.control_port, 65535)


if __name__ == "__main__":
    unittest.main()
