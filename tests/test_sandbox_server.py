import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.sandbox_server import SandboxError, reusable_server

ROOT = Path(__file__).resolve().parents[1]


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

    def test_a_held_sandbox_is_refused_before_any_reset(self) -> None:
        with self.use() as server:
            (server / "worlds").mkdir()
            with self.assertRaises(SandboxError), self.use():
                pass
            self.assertTrue((server / "worlds").is_dir())
        with self.use():
            self.assertEqual(self.installs, [self.version])

    def test_a_concurrent_process_cannot_take_a_held_sandbox(self) -> None:
        with self.use():
            pass
        script = (
            "import sys; from pathlib import Path; from scripts.sandbox_server import SandboxError, reusable_server\n"
            "try:\n"
            "    with reusable_server(Path(sys.argv[1]), install=lambda s: None, version=lambda: sys.argv[2]): pass\n"
            "except SandboxError: sys.exit(3)\n"
        )
        command = [sys.executable, "-c", script, str(self.server), self.version]
        with self.use():
            held = subprocess.run(command, cwd=ROOT, check=False)
        self.assertEqual(held.returncode, 3)
        # The OS released the lock with its holder; nothing stale remains.
        self.assertEqual(subprocess.run(command, cwd=ROOT, check=False).returncode, 0)


if __name__ == "__main__":
    unittest.main()
