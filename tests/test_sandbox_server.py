import os
import tempfile
import unittest
from pathlib import Path

from scripts.sandbox_server import SandboxError, reusable_server


class SandboxServerTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.server = Path(directory.name) / "server"
        self.installs = []
        self.version = "1.26.52.3"

    def install(self, server: Path) -> None:
        self.installs.append(self.version)
        server.mkdir(parents=True, exist_ok=True)
        (server / "bedrock_server.exe").write_text(self.version, encoding="utf-8")
        (server / "server.properties").write_text("online-mode=true\n", encoding="utf-8")
        (server / "behavior_packs").mkdir(exist_ok=True)

    def use(self):
        return reusable_server(self.server, install=self.install, version=lambda: self.version)

    def test_installs_once_and_resets_run_state(self) -> None:
        with self.use() as server:
            (server / "server.properties").write_text("level-name=run-1\n", encoding="utf-8")
            (server / "worlds" / "run-1").mkdir(parents=True)
            (server / "plugins").mkdir()
            (server / "endstone.toml").write_text("", encoding="utf-8")
            (server / "allowlist.json").write_text("[]\n", encoding="utf-8")
        with self.use() as server:
            self.assertEqual(self.installs, [self.version])
            self.assertEqual((server / "server.properties").read_text(encoding="utf-8"), "online-mode=true\n")
            for leftover in ("worlds", "plugins", "endstone.toml", "allowlist.json"):
                self.assertFalse((server / leftover).exists(), leftover)
            self.assertTrue((server / "bedrock_server.exe").is_file())
            self.assertTrue((server / "behavior_packs").is_dir())

    def test_a_new_locked_version_reinstalls_at_the_same_path(self) -> None:
        with self.use():
            pass
        self.version = "1.26.60.1"
        with self.use() as server:
            self.assertEqual(self.installs, ["1.26.52.3", "1.26.60.1"])
            self.assertEqual((server / "bedrock_server.exe").read_text(encoding="utf-8"), "1.26.60.1")

    def test_an_interrupted_install_is_repeated(self) -> None:
        def failing(server: Path) -> None:
            self.install(server)
            raise RuntimeError("download interrupted")

        with self.assertRaises(RuntimeError), reusable_server(self.server, install=failing, version=lambda: self.version):
            pass
        with self.use():
            self.assertEqual(len(self.installs), 2)

    def test_refuses_a_directory_it_did_not_create(self) -> None:
        self.server.mkdir()
        (self.server / "worlds").mkdir()
        with self.assertRaises(SandboxError), self.use():
            pass
        self.assertTrue((self.server / "worlds").is_dir())

    def test_refuses_a_sandbox_held_by_a_live_run(self) -> None:
        with self.use():
            pass
        # The parent of this test process stands in for another live run.
        (self.server / ".endbot-sandbox" / "run.lock").write_text(str(os.getppid()), encoding="utf-8")
        with self.assertRaises(SandboxError), self.use():
            pass

    def test_a_stale_lock_is_taken_over_and_released(self) -> None:
        with self.use():
            pass
        lock = self.server / ".endbot-sandbox" / "run.lock"
        lock.write_text("999999999", encoding="utf-8")
        with self.use():
            self.assertEqual(lock.read_text(encoding="utf-8"), str(os.getpid()))
        self.assertFalse(lock.exists())


if __name__ == "__main__":
    unittest.main()
