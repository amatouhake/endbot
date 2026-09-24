"""Tests for `endbot start` / `stop` / `console` / `controllers reset` (sections 3, 5a).

The end-to-end cases drive ``fakedriver.py`` as a real process so PID files,
the request-file protocol, and process liveness behave exactly as in
production; everything else runs in-process with fake children. No
POSIX-only signals are used.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from fakeinstance import (
    FAKES_DIR,
    build_instance,
    fake_runtime_command,
    fake_server_command,
    free_port,
    write_endbot_toml,
    write_runtime_config,
)

from endbot_cli.commands import run_console, run_controllers_reset, run_start, run_stop
from endbot_cli.controllers import CONTROLLERS_VERSION, load_controllers
from endbot_cli.instance import InstancePaths
from endbot_cli.lock import load_lock
from endbot_cli.runstate import request_stop

FAKE_CONTROLLERS = {
    "version": CONTROLLERS_VERSION,
    "bindings": [
        {
            "gamertag": "ExampleTag",
            "xuid": "2535412345678901",
            "uuid": "11111111-2222-3333-4444-555555555555",
            "boundAt": "2026-09-19T00:00:00Z",
        }
    ],
}


def dead_pid() -> int:
    """Return the PID of a process that has already exited."""

    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait(timeout=30)
    return child.pid


class CommandTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.paths: InstancePaths = build_instance(self.root, load_lock())
        self.run_dir = self.paths.state_run
        self.port = free_port()
        # The generated runtime config derives its control port from
        # endbot.toml; keep it consistent with the fake runtime's port.
        write_endbot_toml(self.root, control_port=self.port)
        self.token_path = self.paths.control_token
        self.runtime_config = write_runtime_config(
            self.root / "state" / "generated" / "endbot-runtime.json",
            data_dir=self.root / "state",
            token_path=self.token_path,
            private_path=self.paths.owner_private_key,
            public_path=self.paths.owner_public_key,
            control_port=self.port,
        )
        self.echo_lines: list[str] = []
        self.result: dict = {}
        self.start_stdout = io.StringIO()
        self.thread: threading.Thread | None = None

    def start_kwargs(self, **overrides) -> dict:
        values = {
            "environ": {},
            "stdin": None,
            "echo": self.echo_lines.append,
            "install_signal_handlers": False,
            "runtime_command": fake_runtime_command(self.runtime_config),
            "server_command": fake_server_command(),
            "installed_endstone_version": lambda: load_lock().endstone_package_version,
            "ready_timeout": 15.0,
            "stop_timeout": 8.0,
            "runtime_stop_timeout": 4.0,
            "poll_interval": 0.05,
            "restart_initial_backoff": 0.05,
            "restart_max_backoff": 0.2,
        }
        values.update(overrides)
        return values

    def start_in_thread(self, **overrides) -> None:
        kwargs = self.start_kwargs(**overrides)

        def target() -> None:
            with contextlib.redirect_stdout(self.start_stdout):
                self.result["code"] = run_start(self.paths, **kwargs)

        self.thread = threading.Thread(target=target, daemon=True, name="run-start-under-test")
        self.thread.start()
        self.addCleanup(self.join, 30.0)

    def join(self, timeout: float = 30.0) -> int:
        if self.thread is None:
            return self.result.get("code")
        self.thread.join(timeout)
        if self.thread.is_alive():
            request_stop(self.run_dir)
            self.thread.join(5)
            self.fail("run_start did not return")
        self.thread = None
        return self.result["code"]

    def run_captured(self, function, *args, **kwargs) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = function(*args, **kwargs)
        return code, stdout.getvalue(), stderr.getvalue()

    def wait_for_echo(self, text: str, timeout: float = 20.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if any(text in line for line in self.echo_lines):
                return
            time.sleep(0.05)
        self.fail(f"did not see {text!r}; echo so far: {self.echo_lines[-20:]}")


class StartRefusalTests(CommandTestCase):
    def marker_command(self) -> tuple[list[str], Path]:
        marker = self.root / "spawned.marker"
        command = [sys.executable, "-c", f"open({str(marker)!r}, 'w').close()"]
        return command, marker

    def test_start_refuses_on_doctor_fail_before_spawning(self) -> None:
        (self.root / "server" / "server.properties").write_text(
            "online-mode=false\nallow-cheats=false\n", encoding="utf-8"
        )
        command, marker = self.marker_command()
        code, stdout, stderr = self.run_captured(
            run_start,
            self.paths,
            **self.start_kwargs(runtime_command=command, server_command=command),
        )
        self.assertEqual(code, 1)
        self.assertIn("FAIL server-properties", stdout)
        self.assertIn("refusing to start", stderr)
        self.assertFalse(marker.exists())

    def test_start_refuses_on_version_mismatch_and_points_at_update(self) -> None:
        (self.root / "server" / "version.txt").write_text("99.99", encoding="utf-8")
        command, marker = self.marker_command()
        code, stdout, stderr = self.run_captured(
            run_start,
            self.paths,
            **self.start_kwargs(runtime_command=command, server_command=command),
        )
        self.assertEqual(code, 1)
        self.assertIn("FAIL bds-version", stdout)
        self.assertIn("endbot update", stdout)
        self.assertIn("refusing to start", stderr)
        self.assertFalse(marker.exists())

    def test_start_refuses_while_another_supervisor_is_live(self) -> None:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        (self.run_dir / "supervisor.pid").write_text(f"{os.getpid()}\n", encoding="utf-8")
        code, _stdout, stderr = self.run_captured(run_start, self.paths, **self.start_kwargs())
        self.assertEqual(code, 1)
        self.assertIn("already running", stderr)

    def test_start_cleans_a_stale_supervisor_pid_and_runs(self) -> None:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        (self.run_dir / "supervisor.pid").write_text(f"{dead_pid()}\n", encoding="utf-8")
        self.start_in_thread()
        self.wait_for_echo("fake BDS ready")
        request_stop(self.run_dir)
        self.assertEqual(self.join(), 0)
        self.assertIn("stale", self.start_stdout.getvalue())
        self.assertFalse((self.run_dir / "supervisor.pid").exists())


class StartHappyPathTests(CommandTestCase):
    def test_first_start_creates_secrets_and_stops_via_stop_request(self) -> None:
        # The runtime creates the token and owner keys on the very first start,
        # after config generation: preflight tolerates their absence and the
        # strict [local-bot-auth] re-check passes once the runtime is ready.
        self.paths.owner_private_key.unlink()
        self.paths.owner_public_key.unlink()
        self.paths.control_token.unlink()
        self.start_in_thread()
        self.wait_for_echo("fake BDS ready")
        self.assertTrue(self.paths.owner_public_key.exists())
        self.assertTrue(self.paths.control_token.exists())
        self.assertTrue((self.root / "state" / "generated" / "endbot-runtime.json").is_file())
        self.assertTrue((self.root / "server" / "plugins" / "endbot" / "config.toml").is_file())
        self.assertIn("local-bot-auth", (self.root / "server" / "endstone.toml").read_text(encoding="utf-8"))
        # The supervisor runs in this process's thread here, so its PID is not
        # waitable from `endbot stop`; the end-to-end test covers that path.
        request_stop(self.run_dir)
        self.assertEqual(self.join(), 0)
        self.assertTrue(any("stopped cleanly" in line for line in self.echo_lines))


class StopAndConsoleTests(CommandTestCase):
    def test_stop_and_console_report_stale_pid_files(self) -> None:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        (self.run_dir / "supervisor.pid").write_text(f"{dead_pid()}\n", encoding="utf-8")
        code, _stdout, stderr = self.run_captured(run_stop, self.paths, timeout=1.0)
        self.assertEqual(code, 1)
        self.assertIn("stale", stderr)
        self.assertFalse((self.run_dir / "supervisor.pid").exists())
        (self.run_dir / "supervisor.pid").write_text(f"{dead_pid()}\n", encoding="utf-8")
        code, _stdout, stderr = self.run_captured(run_console, self.paths, "say hi")
        self.assertEqual(code, 1)
        self.assertIn("stale", stderr)
        self.assertFalse((self.run_dir / "supervisor.pid").exists())

    def test_stop_without_a_supervisor_refuses(self) -> None:
        code, _stdout, stderr = self.run_captured(run_stop, self.paths, timeout=1.0)
        self.assertEqual(code, 1)
        self.assertIn("no supervisor", stderr)

    def test_stop_waits_for_a_live_supervisor_to_exit(self) -> None:
        supervisor = subprocess.Popen(
            [sys.executable, str(FAKES_DIR / "fakesupervisor.py"), str(self.run_dir)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.addCleanup(supervisor.kill)
        self.addCleanup(supervisor.stdout.close)
        self.addCleanup(supervisor.stderr.close)
        line = supervisor.stdout.readline().decode("utf-8")
        self.assertIn("ready", line)
        code, stdout, stderr = self.run_captured(run_stop, self.paths, timeout=20.0, poll_interval=0.05)
        self.assertEqual(code, 0)
        self.assertIn("supervisor stopped", stdout)
        self.assertEqual(stderr, "")
        supervisor.wait(timeout=30)
        self.assertEqual(supervisor.returncode, 0)

    def test_stop_times_out_when_the_supervisor_ignores_the_request(self) -> None:
        supervisor = subprocess.Popen(
            [sys.executable, str(FAKES_DIR / "fakesupervisor.py"), str(self.run_dir), "--ignore-stop"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.addCleanup(supervisor.kill)
        self.addCleanup(supervisor.stdout.close)
        self.addCleanup(supervisor.stderr.close)
        line = supervisor.stdout.readline().decode("utf-8")
        self.assertIn("ready", line)
        code, _stdout, stderr = self.run_captured(run_stop, self.paths, timeout=0.5, poll_interval=0.05)
        self.assertEqual(code, 1)
        self.assertIn("still running", stderr)

    def test_console_queues_one_line_for_a_live_supervisor(self) -> None:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        (self.run_dir / "supervisor.pid").write_text(f"{os.getpid()}\n", encoding="utf-8")
        code, stdout, stderr = self.run_captured(run_console, self.paths, "say hi")
        self.assertEqual(code, 0)
        self.assertIn("queued", stdout)
        self.assertEqual(stderr, "")
        self.assertEqual((self.run_dir / "console.request").read_text(encoding="utf-8"), "say hi\n")
        code, _stdout, stderr = self.run_captured(run_console, self.paths, "two\nlines")
        self.assertEqual(code, 1)
        self.assertIn("single line", stderr)


class ControllersResetTests(CommandTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.paths.state_controllers.write_text(json.dumps(FAKE_CONTROLLERS), encoding="utf-8")

    def test_reset_refuses_while_a_supervisor_is_live(self) -> None:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        (self.run_dir / "supervisor.pid").write_text(f"{os.getpid()}\n", encoding="utf-8")
        code, _stdout, stderr = self.run_captured(run_controllers_reset, self.paths, "ExampleTag")
        self.assertEqual(code, 1)
        self.assertIn("is running", stderr)
        self.assertEqual(load_controllers(self.paths.state_controllers).find("ExampleTag").xuid, "2535412345678901")

    def test_reset_removes_one_binding(self) -> None:
        code, stdout, stderr = self.run_captured(run_controllers_reset, self.paths, "exampletag")
        self.assertEqual(code, 0)
        self.assertIn("pending again", stdout)
        self.assertEqual(stderr, "")
        self.assertIsNone(load_controllers(self.paths.state_controllers).find("ExampleTag"))
        code, stdout, _stderr = self.run_captured(run_controllers_reset, self.paths, "ExampleTag")
        self.assertEqual(code, 0)
        self.assertIn("no binding", stdout)

    def test_reset_with_a_stale_pid_file_cleans_it_and_proceeds(self) -> None:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        (self.run_dir / "supervisor.pid").write_text(f"{dead_pid()}\n", encoding="utf-8")
        code, stdout, _stderr = self.run_captured(run_controllers_reset, self.paths, "ExampleTag")
        self.assertEqual(code, 0)
        self.assertIn("stale", stdout)
        self.assertFalse((self.run_dir / "supervisor.pid").exists())
        self.assertIsNone(load_controllers(self.paths.state_controllers).find("ExampleTag"))


class EndToEndTests(CommandTestCase):
    """`endbot start` as a real process: console.request and stop.request round trip."""

    def test_start_console_and_stop_through_the_request_files(self) -> None:
        options = {
            "runtime_command": fake_runtime_command(self.runtime_config),
            "server_command": fake_server_command(),
            "installed_endstone_version": load_lock().endstone_package_version,
            "ready_timeout": 15.0,
            "stop_timeout": 8.0,
            "runtime_stop_timeout": 4.0,
            "poll_interval": 0.05,
        }
        environment = dict(os.environ)
        environment["ENDBOT_TEST_START_OPTIONS"] = json.dumps(options)
        driver = subprocess.Popen(
            [sys.executable, str(FAKES_DIR / "fakedriver.py"), str(self.root)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
        )
        self.addCleanup(driver.kill)
        self.addCleanup(driver.stdout.close)
        self.addCleanup(driver.stderr.close)
        seen: list[str] = []

        def read_output() -> None:
            for line in driver.stdout:
                seen.append(line.decode("utf-8", "replace").rstrip())

        reader = threading.Thread(target=read_output, daemon=True)
        reader.start()

        def wait_for(text: str) -> None:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if any(text in line for line in seen):
                    return
                time.sleep(0.05)
            self.fail(f"driver never showed {text!r}; output: {seen[-20:]}")

        wait_for("fake BDS ready")
        self.assertEqual(run_console(self.paths, "say hello"), 0)
        wait_for("[console] say hello")
        self.assertEqual(run_stop(self.paths, timeout=30.0, poll_interval=0.05), 0)
        driver.wait(timeout=30)
        self.assertEqual(driver.returncode, 0)
        wait_for("DRIVER EXIT 0")


if __name__ == "__main__":
    unittest.main()
