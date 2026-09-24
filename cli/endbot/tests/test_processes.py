"""Tests for toolchain resolution and process liveness (docs/OPERATIONS.md sections 1, 5a)."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from fakeinstance import build_instance

from endbot_cli.instance import InstancePaths
from endbot_cli.lock import load_lock
from endbot_cli.processes import (
    ENV_NODE,
    ENV_PYTHON,
    ENV_RUNTIME_DIR,
    ToolchainError,
    kill_process_tree,
    process_alive,
    resolve_toolchain,
)


class ResolveToolchainTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.paths: InstancePaths = build_instance(self.root, load_lock())

    def make_app(self) -> Path:
        app = self.root / "app" / "v1"
        (app / "python").mkdir(parents=True)
        (app / "node").mkdir()
        (app / "runtime" / "src").mkdir(parents=True)
        (app / "runtime" / "src" / "cli.js").write_text("// fake", encoding="utf-8")
        (self.root / "app" / "current").write_text("v1\n", encoding="utf-8")
        return app

    def test_app_layout_windows(self) -> None:
        app = self.make_app()
        (app / "python" / "python.exe").touch()
        (app / "node" / "node.exe").touch()
        toolchain = resolve_toolchain(self.paths, {}, windows=True)
        self.assertEqual(toolchain.python, app / "python" / "python.exe")
        self.assertEqual(toolchain.node, app / "node" / "node.exe")
        self.assertEqual(toolchain.runtime_dir, app / "runtime")

    def test_app_layout_posix(self) -> None:
        app = self.make_app()
        (app / "python" / "bin").mkdir()
        (app / "python" / "bin" / "python3").touch()
        (app / "node" / "bin").mkdir()
        (app / "node" / "bin" / "node").touch()
        toolchain = resolve_toolchain(self.paths, {}, windows=False)
        self.assertEqual(toolchain.python, app / "python" / "bin" / "python3")
        self.assertEqual(toolchain.node, app / "node" / "bin" / "node")
        self.assertEqual(toolchain.runtime_dir, app / "runtime")

    def test_environment_overrides_win_over_the_app(self) -> None:
        app = self.make_app()
        (app / "python" / "python.exe").touch()
        (app / "node" / "node.exe").touch()
        overrides = self.root / "dev"
        (overrides / "runtime" / "src").mkdir(parents=True)
        (overrides / "runtime" / "src" / "cli.js").touch()
        environ = {
            ENV_PYTHON: str(overrides / "python"),
            ENV_NODE: str(overrides / "node"),
            ENV_RUNTIME_DIR: str(overrides / "runtime"),
        }
        (overrides / "python").touch()
        (overrides / "node").touch()
        toolchain = resolve_toolchain(self.paths, environ, windows=True)
        self.assertEqual(toolchain.python, overrides / "python")
        self.assertEqual(toolchain.node, overrides / "node")
        self.assertEqual(toolchain.runtime_dir, overrides / "runtime")

    def test_missing_app_and_environment_is_a_clear_failure(self) -> None:
        with self.assertRaisesRegex(ToolchainError, ENV_PYTHON):
            resolve_toolchain(self.paths, {}, windows=True)

    def test_missing_candidate_names_the_source(self) -> None:
        with self.assertRaisesRegex(ToolchainError, ENV_NODE):
            resolve_toolchain(self.paths, {ENV_PYTHON: sys.executable, ENV_NODE: "missing-node"}, windows=True)

    def test_runtime_dir_without_the_runtime_is_rejected(self) -> None:
        empty = self.root / "empty"
        empty.mkdir()
        environ = {
            ENV_PYTHON: sys.executable,
            ENV_NODE: sys.executable,
            ENV_RUNTIME_DIR: str(empty),
        }
        with self.assertRaisesRegex(ToolchainError, "src/cli.js"):
            resolve_toolchain(self.paths, environ, windows=True)


class ProcessAliveTests(unittest.TestCase):
    def test_own_process_is_alive(self) -> None:
        self.assertTrue(process_alive(os.getpid()))

    def test_reaped_child_is_not_alive(self) -> None:
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        child.wait(timeout=30)
        self.assertFalse(process_alive(child.pid))

    def test_invalid_pids_are_not_alive(self) -> None:
        self.assertFalse(process_alive(0))
        self.assertFalse(process_alive(-5))

    def test_killing_an_exited_process_is_a_no_op(self) -> None:
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        child.wait(timeout=30)
        kill_process_tree(child)  # must not raise


if __name__ == "__main__":
    unittest.main()


class ProcessAliveZombieTests(unittest.TestCase):
    def test_current_process_is_alive(self) -> None:
        from endbot_cli.processes import process_alive

        self.assertTrue(process_alive(os.getpid()))

    @unittest.skipUnless(sys.platform.startswith("linux"), "zombie state is read from /proc")
    def test_unreaped_exited_child_is_not_alive(self) -> None:
        import time

        from endbot_cli.processes import process_alive

        child = subprocess.Popen([sys.executable, "-c", "pass"])
        self.addCleanup(child.wait)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and process_alive(child.pid):
            time.sleep(0.05)
        self.assertFalse(process_alive(child.pid))
