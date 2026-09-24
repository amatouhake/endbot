"""Tests for the isolated Endstone BDS acquisition helper (section 6).

No network and no real BDS: the exact bootstrap arguments are asserted against
a fake importer, and the ``<python> -m endbot_cli.bds`` subprocess contract is
asserted against a stub ``endstone`` package that records what it received.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import endbot_cli
from endbot_cli.bds import DEFAULT_REMOTE, BdsError, build_bootstrap, install_command, install_locked_bds, run_install

SRC_DIR = Path(endbot_cli.__file__).resolve().parent.parent

STUB_RECORDED_ARGS = ("server_folder", "no_confirm", "remote", "interactive")

STUB_BOOTSTRAP = """\
import json, os


class _RecorderBootstrap:
    def __init__(self, **kwargs):
        self._out = os.environ["ENDBOT_TEST_STUB_OUT"]
        record = {
            "module": "__MODULE__",
            "class": "__CLASS__",
            "kwargs": {key: kwargs[key] for key in __ARGS__},
            "install": 0,
        }
        with open(self._out, "w", encoding="utf-8") as handle:
            json.dump(record, handle)

    def _install(self):
        with open(self._out, encoding="utf-8") as handle:
            record = json.load(handle)
        record["install"] += 1
        with open(self._out, "w", encoding="utf-8") as handle:
            json.dump(record, handle)


class __CLASS__(_RecorderBootstrap):
    pass
"""


class FakeBootstrap:
    created: ClassVar[list] = []

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.installed = False
        FakeBootstrap.created.append(self)

    def _install(self) -> None:
        self.installed = True


class RecordingImporter:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, name: str):
        self.calls.append(name)
        return SimpleNamespace(WindowsBootstrap=FakeBootstrap, LinuxBootstrap=FakeBootstrap)


class RecordingRunner:
    def __init__(self, returncode: int = 0) -> None:
        self.returncode = returncode
        self.commands: list[list[str]] = []

    def __call__(self, command, **kwargs):
        self.commands.append(list(command))
        return SimpleNamespace(returncode=self.returncode)


class BuildBootstrapTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeBootstrap.created.clear()

    def test_windows_bootstrap_gets_the_exact_documented_arguments(self) -> None:
        importer = RecordingImporter()
        bootstrap = build_bootstrap("some/server", system="Windows", importer=importer)
        self.assertEqual(importer.calls, ["endstone.cli.windows"])
        self.assertEqual(
            bootstrap.kwargs,
            {
                "server_folder": str(Path("some/server").absolute()),
                "no_confirm": True,
                "remote": DEFAULT_REMOTE,
                "interactive": False,
            },
        )

    def test_linux_bootstrap_gets_the_exact_documented_arguments(self) -> None:
        importer = RecordingImporter()
        bootstrap = build_bootstrap("/srv/server", system="Linux", importer=importer)
        self.assertEqual(importer.calls, ["endstone.cli.linux"])
        self.assertEqual(
            bootstrap.kwargs,
            {
                "server_folder": str(Path("/srv/server").absolute()),
                "no_confirm": True,
                "remote": DEFAULT_REMOTE,
                "interactive": False,
            },
        )

    def test_unsupported_system_is_refused(self) -> None:
        with self.assertRaises(BdsError):
            build_bootstrap("x", system="Darwin", importer=RecordingImporter())

    def test_missing_platform_class_is_refused(self) -> None:
        with self.assertRaises(BdsError):
            build_bootstrap("x", system="Windows", importer=lambda name: SimpleNamespace())

    def test_install_locked_bds_calls_only_the_install_step(self) -> None:
        install_locked_bds("some/server", system="Windows", importer=RecordingImporter())
        self.assertEqual(len(FakeBootstrap.created), 1)
        self.assertTrue(FakeBootstrap.created[0].installed)

    def test_install_command_is_the_documented_subprocess(self) -> None:
        command = install_command("py", "srv")
        self.assertEqual(command, ["py", "-m", "endbot_cli.bds", "--server-folder", "srv", "--remote", DEFAULT_REMOTE])

    def test_run_install_passes_the_exact_command(self) -> None:
        runner = RecordingRunner(returncode=0)
        run_install("py", "srv", runner=runner)
        self.assertEqual(
            runner.commands,
            [["py", "-m", "endbot_cli.bds", "--server-folder", "srv", "--remote", DEFAULT_REMOTE]],
        )

    def test_run_install_raises_on_failure(self) -> None:
        with self.assertRaises(BdsError):
            run_install("py", "srv", runner=RecordingRunner(returncode=3))


class StubEndstoneSubprocessTests(unittest.TestCase):
    """Drive ``<python> -m endbot_cli.bds`` against a stub endstone package."""

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.stub = self.root / "stub"
        self.out = self.root / "record.json"
        self.server = self.root / "server"
        on_windows = os.name == "nt"
        module = "windows" if on_windows else "linux"
        class_name = "WindowsBootstrap" if on_windows else "LinuxBootstrap"

        def stub_source(name: str) -> str:
            return (
                STUB_BOOTSTRAP.replace("__MODULE__", name)
                .replace("__CLASS__", "WindowsBootstrap" if name == "windows" else "LinuxBootstrap")
                .replace("__ARGS__", repr(STUB_RECORDED_ARGS))
            )

        for relative, text in {
            "endstone/__init__.py": "",
            "endstone/cli/__init__.py": "",
            "endstone/cli/base.py": "class Bootstrap:\n    pass\n",
            "endstone/cli/windows.py": stub_source("windows"),
            "endstone/cli/linux.py": stub_source("linux"),
        }.items():
            path = self.stub / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        self.expected_module = module
        self.expected_class = class_name

    def test_helper_records_exact_arguments_and_runs_install(self) -> None:
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join([str(self.stub), str(SRC_DIR)])
        env["ENDBOT_TEST_STUB_OUT"] = str(self.out)
        completed = subprocess.run(
            [sys.executable, "-m", "endbot_cli.bds", "--server-folder", str(self.server)],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        record = json.loads(self.out.read_text(encoding="utf-8"))
        self.assertEqual(record["module"], self.expected_module)
        self.assertEqual(record["class"], self.expected_class)
        self.assertEqual(
            record["kwargs"],
            {
                "server_folder": str(self.server.absolute()),
                "no_confirm": True,
                "remote": DEFAULT_REMOTE,
                "interactive": False,
            },
        )
        self.assertEqual(record["install"], 1)


if __name__ == "__main__":
    unittest.main()
