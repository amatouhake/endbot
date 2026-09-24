"""Supervision tests with fake children (docs/OPERATIONS.md section 5a).

Every child is a small Python script run with ``sys.executable``; no
POSIX-only signals are used so the suite runs on Windows and Linux.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path

from fakeinstance import (
    LineFeed,
    fake_runtime_command,
    fake_server_command,
    free_port,
    write_runtime_config,
)

from endbot_cli.processes import process_alive
from endbot_cli.runstate import append_console_line, request_stop
from endbot_cli.supervisor import Supervisor, SupervisorOptions


class SupervisorTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.run_dir = self.root / "state" / "run"
        secrets = self.root / "state" / "secrets"
        self.port = free_port()
        self.token_path = secrets / "control.token"
        self.starts_file = self.root / "runtime-starts.log"
        self.runtime_config = write_runtime_config(
            self.root / "state" / "generated" / "endbot-runtime.json",
            data_dir=self.root / "state",
            token_path=self.token_path,
            private_path=secrets / "owner-private.pem",
            public_path=secrets / "owner-public.pem",
            control_port=self.port,
        )
        self.echo_lines: list[str] = []
        self.result: dict = {}
        self.supervisor: Supervisor | None = None
        self.thread: threading.Thread | None = None
        self.set_env(FAKE_RUNTIME_STARTS_FILE=str(self.starts_file))

    def set_env(self, **values: str) -> None:
        for key, value in values.items():
            os.environ[key] = value
            self.addCleanup(os.environ.pop, key, None)

    def options(self, **overrides) -> SupervisorOptions:
        values = {
            "run_dir": self.run_dir,
            "runtime_command": tuple(fake_runtime_command(self.runtime_config)),
            "server_command": tuple(fake_server_command()),
            "control_token_file": self.token_path,
            "control_host": "127.0.0.1",
            "control_port": self.port,
            "ready_timeout": 15.0,
            "stop_timeout": 8.0,
            "runtime_stop_timeout": 4.0,
            "poll_interval": 0.05,
            "restart_initial_backoff": 0.05,
            "restart_max_backoff": 0.2,
            "restart_max_failures": 5,
            "restart_failure_window": 300.0,
            "runtime_control_timeout": 3.0,
        }
        values.update(overrides)
        return SupervisorOptions(**values)

    def start_supervisor(self, options: SupervisorOptions | None = None, stdin=None) -> Supervisor:
        self.supervisor = Supervisor(
            options or self.options(),
            stdin=stdin,
            echo=self.echo_lines.append,
        )

        def target() -> None:
            self.result["code"] = self.supervisor.run()

        self.thread = threading.Thread(target=target, daemon=True, name="supervisor-under-test")
        self.thread.start()
        self.addCleanup(self.join, 30.0)
        return self.supervisor

    def wait_for(self, text: str, timeout: float = 20.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if any(text in line for line in self.echo_lines):
                return
            time.sleep(0.05)
        self.fail(f"did not see {text!r} within {timeout:g}s; echo so far: {self.echo_lines[-20:]}")

    def join(self, timeout: float = 30.0) -> int:
        if self.thread is None:
            return self.result.get("code")
        self.thread.join(timeout)
        if self.thread.is_alive():
            request_stop(self.run_dir)
            self.thread.join(5)
            self.fail(f"supervisor did not exit; echo so far: {self.echo_lines[-20:]}")
        self.thread = None
        return self.result["code"]

    def last_exit(self) -> dict:
        return json.loads((self.run_dir / "last-exit.json").read_text(encoding="utf-8"))

    def starts(self) -> int:
        return len(self.starts_file.read_text(encoding="utf-8").splitlines())


class HappyPathTests(SupervisorTestCase):
    def test_happy_start_then_stop_request_stops_cleanly(self) -> None:
        self.start_supervisor()
        self.wait_for("fake BDS ready")
        request_stop(self.run_dir)
        self.assertEqual(self.join(), 0)
        self.assertTrue(any("stopped cleanly" in line for line in self.echo_lines))
        record = self.last_exit()
        self.assertEqual(record["serverExit"], 0)
        self.assertEqual(record["runtimeExit"], 0)
        self.assertFalse(record["unclean"])
        self.assertFalse((self.run_dir / "supervisor.pid").exists())

    def test_console_request_lines_reach_the_bds_console(self) -> None:
        self.start_supervisor()
        self.wait_for("fake BDS ready")
        append_console_line(self.run_dir, "say hello")
        append_console_line(self.run_dir, "list")
        self.wait_for("[console] say hello")
        self.wait_for("[console] list")
        request_stop(self.run_dir)
        self.assertEqual(self.join(), 0)

    def test_supervisor_stdin_lines_reach_the_bds_console(self) -> None:
        feed = LineFeed()
        self.start_supervisor(stdin=feed)
        self.wait_for("fake BDS ready")
        feed.feed("from-stdin")
        self.wait_for("[console] from-stdin")
        request_stop(self.run_dir)
        self.assertEqual(self.join(), 0)

    def test_per_start_logs_are_written(self) -> None:
        self.start_supervisor()
        self.wait_for("fake BDS ready")
        request_stop(self.run_dir)
        self.assertEqual(self.join(), 0)
        record = self.last_exit()
        log_dir = Path(record["logDir"])
        self.assertTrue((log_dir / "runtime.log").is_file())
        self.assertTrue((log_dir / "server.log").is_file())
        self.assertIn("fake BDS ready", (log_dir / "server.log").read_text(encoding="utf-8"))


class SupervisionPolicyTests(SupervisorTestCase):
    def test_bds_self_exit_stops_the_runtime_and_keeps_its_code(self) -> None:
        self.set_env(FAKE_BDS_EXIT_AFTER="0.3", FAKE_BDS_EXIT_CODE="7")
        self.start_supervisor()
        self.assertEqual(self.join(), 7)
        self.assertTrue(any("BDS exited with code 7" in line for line in self.echo_lines))
        self.assertTrue(any("graceful shutdown" in line for line in self.echo_lines))
        record = self.last_exit()
        self.assertEqual(record["serverExit"], 7)
        self.assertFalse(record["unclean"])

    def test_runtime_crash_restarts_with_backoff(self) -> None:
        self.set_env(FAKE_RUNTIME_CRASH_ON_START="1", FAKE_RUNTIME_CRASH_DELAY="0.1")
        self.start_supervisor()
        self.wait_for("runtime is back")
        self.assertTrue(any("restarting the runtime" in line for line in self.echo_lines))
        request_stop(self.run_dir)
        self.assertEqual(self.join(), 0)
        self.assertEqual(self.starts(), 2)

    def test_runtime_crash_gives_up_after_five_failures_and_stops_bds(self) -> None:
        self.set_env(FAKE_RUNTIME_CRASH_ON_START="all", FAKE_RUNTIME_CRASH_DELAY="0.02")
        self.start_supervisor(self.options(restart_initial_backoff=0.02, restart_max_backoff=0.05))
        self.assertEqual(self.join(), 1)
        self.assertTrue(any("giving up" in line for line in self.echo_lines))
        self.assertTrue(any("fake BDS: stopping" in line for line in self.echo_lines))
        self.assertEqual(self.starts(), 5)
        self.assertTrue(self.last_exit()["unclean"])

    def test_runtime_that_never_reports_ready_fails_the_start(self) -> None:
        self.set_env(FAKE_RUNTIME_CRASH_IMMEDIATE="1")
        self.start_supervisor(self.options(ready_timeout=2.0))
        self.assertEqual(self.join(), 1)
        self.assertTrue(any("did not report endbot_runtime_ready" in line for line in self.echo_lines))


class StopEscalationTests(SupervisorTestCase):
    def test_stop_escalates_to_killing_a_bds_that_ignores_stop(self) -> None:
        pid_file = self.root / "bds.pid"
        self.set_env(FAKE_BDS_IGNORE_STOP="1", FAKE_BDS_PID_FILE=str(pid_file))
        self.start_supervisor(self.options(stop_timeout=0.6))
        self.wait_for("fake BDS ready")
        bds_pid = None
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and bds_pid is None:
            if pid_file.exists():
                bds_pid = int(pid_file.read_text(encoding="utf-8"))
            time.sleep(0.05)
        self.assertIsNotNone(bds_pid)
        request_stop(self.run_dir)
        self.assertEqual(self.join(), 1)
        self.assertTrue(any("UNCLEAN" in line for line in self.echo_lines))
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and process_alive(bds_pid):
            time.sleep(0.05)
        self.assertFalse(process_alive(bds_pid), "the ignored-stop BDS survived the escalation")


if __name__ == "__main__":
    unittest.main()
