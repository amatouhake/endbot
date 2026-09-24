"""Tests for instance directory resolution and §1 layout paths."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from endbot_cli.instance import INSTANCE_ENV_VAR, InstanceError, InstancePaths, resolve_instance_root


class InstancePathsTests(unittest.TestCase):
    def test_for_root_covers_the_section_1_layout(self) -> None:
        paths = InstancePaths.for_root(Path("X"))
        self.assertEqual(paths.endbot_toml, Path("X/endbot.toml"))
        self.assertEqual(paths.app_current, Path("X/app/current"))
        self.assertEqual(paths.state_secrets, Path("X/state/secrets"))
        self.assertEqual(paths.state_profiles, Path("X/state/profiles"))
        self.assertEqual(paths.state_controllers, Path("X/state/controllers.json"))
        self.assertEqual(paths.state_generated, Path("X/state/generated"))
        self.assertEqual(paths.state_run, Path("X/state/run"))
        self.assertEqual(paths.backups, Path("X/backups"))
        self.assertEqual(paths.control_token, Path("X/state/secrets/control.token"))
        self.assertEqual(paths.owner_private_key, Path("X/state/secrets/owner-private.pem"))
        self.assertEqual(paths.owner_public_key, Path("X/state/secrets/owner-public.pem"))


class ResolveInstanceRootTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def test_explicit_argument_wins(self) -> None:
        other = self.root / "other"
        other.mkdir()
        root, source = resolve_instance_root(
            self.root, environ={INSTANCE_ENV_VAR: str(other)}, argv0=str(other / "endbot.cmd"), cwd=other
        )
        self.assertEqual(root, self.root.resolve())
        self.assertEqual(source, "--instance")

    def test_environment_beats_launcher_and_cwd(self) -> None:
        env_instance = self.root / "from-env"
        env_instance.mkdir()
        other = self.root / "other"
        other.mkdir()
        (other / "endbot.cmd").write_text("", encoding="utf-8")
        root, source = resolve_instance_root(
            None, environ={INSTANCE_ENV_VAR: str(env_instance)}, argv0=str(other / "endbot.cmd"), cwd=other
        )
        self.assertEqual(root, env_instance.resolve())
        self.assertEqual(source, f"${INSTANCE_ENV_VAR}")

    def test_launcher_directory_beats_cwd(self) -> None:
        launcher_dir = self.root / "with-launcher"
        launcher_dir.mkdir()
        (launcher_dir / "endbot").write_text("", encoding="utf-8")
        (launcher_dir / "endbot.toml").write_text("", encoding="utf-8")
        other = self.root / "other"
        other.mkdir()
        root, source = resolve_instance_root(None, environ={}, argv0=str(launcher_dir / "endbot"), cwd=other)
        self.assertEqual(root, launcher_dir.resolve())
        self.assertEqual(source, "launcher directory")

    def test_console_script_named_like_the_launcher_falls_back_to_cwd(self) -> None:
        scripts_dir = self.root / "venv" / "Scripts"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "endbot").write_text("", encoding="utf-8")
        root, source = resolve_instance_root(None, environ={}, argv0=str(scripts_dir / "endbot"), cwd=self.root)
        self.assertEqual(root, self.root.resolve())
        self.assertEqual(source, "current working directory")

    def test_falls_back_to_cwd(self) -> None:
        root, source = resolve_instance_root(None, environ={}, argv0="python", cwd=self.root)
        self.assertEqual(root, self.root.resolve())
        self.assertEqual(source, "current working directory")

    def test_missing_directory_names_the_source(self) -> None:
        missing = self.root / "missing"
        with self.assertRaises(InstanceError) as caught:
            resolve_instance_root(missing, environ={})
        self.assertIn("--instance", str(caught.exception))
        self.assertIn(str(missing), str(caught.exception))


if __name__ == "__main__":
    unittest.main()
