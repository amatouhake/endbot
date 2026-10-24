"""No native runner or Endstone required: exercise the real host lifecycle."""

import json
import queue
import sys
import tempfile
import threading
import time
import types
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from endstone_endbot import forms, script_forms
from endstone_endbot.commands import HELP_TOPICS, parse_command
from endstone_endbot.control import RuntimeControlError
from endstone_endbot.dispatch import CommandSource
from endstone_endbot.groups import GroupCommandService, GroupStore
from endstone_endbot.i18n import LocalizedError, localize_error
from endstone_endbot.operations import OPERATIONS, operation_argv
from endstone_endbot.script_config import ScriptConfig
from endstone_endbot.script_inputs import OwnedInputs
from endstone_endbot.script_params import arguments, declarations
from endstone_endbot.script_process import RunnerProcess
from endstone_endbot.script_store import Script, ScriptStore
from endstone_endbot.scripting import ScriptRun, ScriptService


class ConfigStorageParamsTests(unittest.TestCase):
    def test_default_runner_uses_product_name_inside_plugin_package(self):
        executable = ScriptConfig().executable(Path("data"))
        self.assertEqual(executable.name, "endbot-luau-runner.exe" if sys.platform == "win32" else "endbot-luau-runner")
        self.assertEqual(executable.parent.name, "runner")
        self.assertEqual(executable.parent.parent.name, "endstone_endbot")

    def test_startup_defaults_and_strict_settings(self):
        self.assertFalse(ScriptConfig.from_config({}).enabled)
        self.assertEqual(ScriptConfig.from_config({}).wall_seconds, 0)
        self.assertEqual(ScriptConfig.from_config({"wall-seconds": 0}).limits()["wallMs"], 0)
        self.assertEqual(ScriptConfig.from_config({"wall-seconds": 3}).limits()["wallMs"], 3000)
        for setting in (
            {"enabled": "false"},
            {"server-runs": True},
            {"wall-seconds": -1},
            {"wall-seconds": True},
            {"interrupt-budget": -1},
            {"max-script-bytes": 65537},
            {"unknown": 1},
            [],
        ):
            with self.subTest(setting=setting), self.assertRaises(ValueError):
                ScriptConfig.from_config(setting)

    def test_shipped_config_loads_through_tomlkit_like_endstone(self):
        try:
            import tomlkit
        except ImportError:
            self.skipTest("tomlkit is Endstone's config loader; not installed here")
        shipped = Path(__file__).resolve().parents[1] / "src" / "endstone_endbot" / "config.toml"
        document = tomlkit.parse(shipped.read_text(encoding="utf-8"))
        document["scripting"]["enabled"] = True
        config = ScriptConfig.from_config(document["scripting"])
        self.assertTrue(config.enabled)
        self.assertIs(type(config.server_runs), int)
        config = ScriptConfig.from_config({"enabled": True, "server-runs": 16, "runner": "runner/host.exe"})
        self.assertEqual(config.executable(Path("data")), Path("data/runner/host.exe"))
        self.assertEqual(config.limits()["interruptBudget"], 1000000)

    def test_atomic_storage_revisions_names_limits_and_disabled_retention(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scripts.json"
            store = ScriptStore(path, ScriptConfig(max_scripts=1, max_script_bytes=30))
            store.save("test", "params {}\n")
            script = store.get("TEST")
            with self.assertRaises(ValueError):
                store.save("../escape", "params {}")
            with self.assertRaises(ValueError):
                store.save("other", "params {}")
            with self.assertRaises(ValueError):
                store.save("test", "params {}")
            with (
                patch("endstone_endbot.script_store.os.replace", side_effect=OSError("disk full")),
                self.assertRaises(OSError),
            ):
                store.save("test", "params {a=1}", script.revision)
            self.assertEqual(store.get("test"), script)
            self.assertEqual(ScriptStore(path, ScriptConfig(enabled=False)).get("test"), script)
            with self.assertRaises(ValueError):
                store.delete("test", "stale")
            store.delete("test", script.revision)
            self.assertEqual(store.list(), ())
            self.assertEqual(json.loads(path.read_text())["version"], 2)

    def test_all_param_types_relative_positions_and_declaration_order(self):
        groups = type("Groups", (), {"get": lambda self, name: type("G", (), {"name": name})()})()
        schema = [
            {"name": "group", "type": "group"},
            {"name": "name", "type": "bot"},
            {"name": "flag", "type": "boolean", "default": False},
            {"name": "origin", "type": "pos", "default": "~ ~ ~"},
            {"name": "spacing", "type": "number", "default": 2},
            {"name": "text", "type": "string", "default": "x"},
        ]
        self.assertEqual(list(declarations(schema)), [spec["name"] for spec in schema])
        result = arguments(
            schema,
            ["#miners", "alice", "true"],
            groups=groups,
            bots=[{"name": "Alice"}],
            origin={"x": 1, "y": 64, "z": 2},
        )
        self.assertEqual(list(result), [spec["name"] for spec in schema])
        defaulted = arguments(schema, ["#miners", "alice"], groups=groups, bots=[{"name": "Alice"}],
                              origin={"x": 1, "y": 64, "z": 2})
        self.assertFalse(defaulted["flag"])
        self.assertEqual(defaulted["spacing"], 2)
        self.assertEqual(defaulted["text"], "x")
        self.assertEqual(
            result,
            {
                "flag": True,
                "group": "#miners",
                "name": "Alice",
                "origin": {"x": 1, "y": 64, "z": 2},
                "spacing": 2.0,
                "text": "x",
            },
        )
        for kind, value in (
            ("boolean", 1),
            ("number", True),
            ("number", "nan"),
            ("pos", "~ ~ ~"),
            ("bot", "missing"),
            ("group", "miners"),
        ):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                arguments([{"name": "x", "type": kind}], [value], groups=groups, bots=[], origin=None)
        for schema, message in (
            ({}, "Script parameters must be an array"),
            ({"n": {"type": "number"}}, "Script parameters must be an array"),
            ([{"name": "bad key", "type": "number"}], "Invalid script parameter name"),
            ([{"name": "n", "type": "number", "default": True}], "Invalid script parameter default"),
            ([{"name": "n", "type": "boolean", "default": "false"}], "Invalid script parameter default"),
            ([{"name": "n", "type": "command"}], "Unknown script parameter type"),
            ([{"name": "n", "type": []}], "Unknown script parameter type"),
            ([{"name": "n", "type": "number"}] * 2, "Duplicate script parameter name"),
            ([{"type": "number"}], "Invalid script parameter name"),
            ([1], "Invalid script parameter declarations"),
        ):
            with self.subTest(schema=schema), self.assertRaisesRegex(LocalizedError, message) as caught:
                declarations(schema)
            self.assertNotEqual(localize_error(caught.exception, "ja"), message)
        with self.assertRaisesRegex(LocalizedError, "Too many"):
            arguments([], [1], groups=groups, bots=[])
        with self.assertRaisesRegex(LocalizedError, "Unknown script argument"):
            arguments([], {"x": 1}, groups=groups, bots=[])


class OperationParityTests(unittest.TestCase):
    def test_every_named_help_operation_is_in_generated_api(self):
        self.assertTrue(
            {"tp" if name == "teleport" else name for name, topic in HELP_TOPICS.items() if topic.named}
            <= OPERATIONS.keys()
        )

    def test_every_operation_and_overload_produces_equivalent_command(self):
        examples = {
            "spawn": [
                ({}, ["spawn"]),
                (
                    {"x": 1, "y": 64, "z": 2, "yaw": 90, "pitch": 0, "dimension": "overworld"},
                    ["spawn", "at", "1", "64", "2", "facing", "90", "0", "in", "overworld"],
                ),
            ],
            "rename": [({"name": "Other"}, ["rename", "Other"])],
            "tp": [
                ({"target": "Player"}, ["tp", "Player"]),
                (
                    {"x": 1, "y": 64, "z": 2, "dimension": "overworld", "facingX": 3, "facingY": 64, "facingZ": 4},
                    ["tp", "in", "overworld", "1", "64", "2", "facing", "3", "64", "4"],
                ),
            ],
            "move": [({"direction": "forward", "ticks": 20}, ["move", "forward", "for", "20"])],
            "look": [
                ({"yaw": 90, "pitch": 0}, ["look", "90", "0"]),
                ({"x": "~", "y": 64, "z": 1}, ["look", "at", "~", "64", "1"]),
            ],
            "swap": [({"a": 1, "b": 10}, ["swap", "1", "10"])],
            "hold": [({"item": "stone"}, ["hold", "stone"])],
            "hotbar": [({}, ["hotbar"]), ({"slot": 2}, ["hotbar", "2"])],
            "radius": [({}, ["radius"]), ({"value": "default"}, ["radius", "default"])],
            "interact": [
                ({"x": 1, "y": 64, "z": 2, "face": "up"}, ["interact", "1", "64", "2", "up"]),
                ({}, ["interact"]),
                ({"x": 1, "y": 64, "z": 2}, ["interact", "1", "64", "2"]),
                ({"target": "@cow"}, ["interact", "@cow"]),
                ({"target": "chest"}, ["interact", "chest"]),
                ({"target": "@42"}, ["interact", "@42"]),
            ],
            "destroy": [
                ({"mode": "continuous", "count": 64}, ["destroy", "continuous", "count", "64"]),
                ({"x": 1, "y": 64, "z": 2, "face": "up"}, ["destroy", "1", "64", "2", "up"]),
            ],
            "drop": [({}, ["drop"]), ({"selector": "all"}, ["drop", "all"])],
            "block": [({"x": 1, "y": "~-1", "z": "~"}, ["block", "1", "~-1", "~"])],
            "blocks": [
                ({"type": "oak_log"}, ["blocks", "oak_log"]),
                ({"type": "oak_log", "radius": 12, "limit": 3}, ["blocks", "oak_log", "radius", "12", "limit", "3"]),
            ],
            **{
                op: [({"mode": "continuous", "ticks": 20}, [op, "continuous", "for", "20"])]
                for op in ("jump", "attack", "use")
            },
            **{op: [({"value": True}, [op, "on"])] for op in ("sprint", "sneak")},
            "help": [({}, ["help"]), ({"topic": "move"}, ["help", "move"])],
        }
        for op in OPERATIONS:
            for params, suffix in examples.get(op, [({}, [op])]):
                for target in ("Alice", "#miners", "#miners[1]"):
                    with self.subTest(operation=op, params=params, target=target):
                        expected = suffix if op in {"list", "help", "ping"} else [target, *suffix]
                        argv = operation_argv(target, op, params)
                        self.assertEqual(argv, expected)
                        self.assertEqual(parse_command(argv), parse_command(expected))
        for op, params in (
            ("move", {"direction": "forward", "raw": "stop"}),
            ("look", {"yaw": True, "pitch": 0}),
            ("drop", {"selector": "all stop"}),
            ("unknown", {}),
        ):
            with self.assertRaises(ValueError):
                operation_argv("Alice", op, params)


class FakeControl:
    def __init__(self):
        self.calls = []

    def request(self, method, **params):
        self.calls.append((method, params))
        status = {"name": "Alice", "identityId": "11111111-1111-4111-8111-111111111111",
                  "connectionState": "online", "desiredState": "online"}
        if method in {"list", "profiles"}:
            return [status]
        if method == "capabilities":
            return {"identityPinnedRequests": True, "groupInputBatch": True}
        if method == "groupInputBatch":
            return {
                "results": [
                    {"ok": True, "result": {"name": request["params"]["name"], "connectionState": "online"}}
                    for request in params["requests"]
                ]
            }
        if method == "mine":
            status["mining"] = {"loopId": "loop-1"}
        return status


class FakeRunner:
    def __init__(self, events, schema=None):
        self.events = queue.Queue()
        for event in [{"type": "params", "id": 1, "schema": [] if schema is None else schema}, *events]:
            self.events.put(event)
        self.replies = []
        self.closed = False
        self.after_reply = None

    def receive(self, deadline, canceled, check):
        while True:
            check()
            if canceled.is_set() or (deadline is not None and time.monotonic() >= deadline):
                raise ValueError("stopped")
            try:
                return self.events.get(timeout=0.01)
            except queue.Empty:
                pass

    def send(self, reply):
        self.replies.append(reply)
        if self.after_reply:
            self.after_reply(reply)

    def close(self):
        self.closed = True


class RunnerPipeTests(unittest.TestCase):
    def test_zero_wall_time_has_no_watchdog_positive_wall_time_has_one(self):
        for milliseconds in (0, 2000):
            with patch("endstone_endbot.script_process.subprocess.Popen") as popen, \
                 patch("endstone_endbot.script_process.Confinement") as confinement, \
                 patch("endstone_endbot.script_process.threading.Thread"), \
                 patch("endstone_endbot.script_process.threading.Timer") as timer:
                confinement.return_value.popen_kwargs = {}
                confinement.return_value.runner_args = []
                process = RunnerProcess(
                    Mock(is_file=lambda: True), "params {}", "bootstrap", {}, {"wallMs": milliseconds}
                )
                if milliseconds:
                    timer.assert_called_once_with(2, process._terminate)
                    timer.return_value.start.assert_called_once()
                else:
                    timer.assert_not_called()
                process.close()
                popen.return_value.wait.assert_called_once_with(timeout=5)

    def test_receive_without_deadline_rechecks_and_obeys_cancel(self):
        process = RunnerProcess.__new__(RunnerProcess)
        process._events = queue.Queue()
        process._process = Mock()
        process._events.put({"type": "done"})
        canceled, check = threading.Event(), Mock()
        self.assertEqual(process.receive(None, canceled, check), {"type": "done"})
        check.assert_called_once()
        canceled.set()
        with self.assertRaises(ValueError):
            process.receive(None, canceled, check)
        with self.assertRaises(ValueError):
            process.receive(time.monotonic() - 1, threading.Event(), check)
        process._process.poll.return_value = None
        with self.assertRaisesRegex(ValueError, "revoked"):
            process.receive(None, threading.Event(), Mock(side_effect=[None, ValueError("revoked")]))


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.config = ScriptConfig(enabled=True, wall_seconds=2)
        self.raw = FakeControl()
        self.inputs = OwnedInputs(self.raw)
        self.allowed = True
        self.source = CommandSource(player_id="controller", name="Caller", grant_revision=1)
        groups = GroupStore(Path(self.directory.name) / "groups.json")
        self.service = GroupCommandService(self.inputs, None, groups, lambda source: self.allowed)
        self.store = ScriptStore(Path(self.directory.name) / "scripts.json", self.config)
        self.runner = FakeRunner([])
        self.host = ScriptService(
            self.config,
            self.store,
            self.service,
            self.inputs,
            Path(self.directory.name),
            lambda source: self.allowed,
            process_factory=lambda *args: self.runner,
            bootstrap="trusted",
        )
        self.worker = ThreadPoolExecutor(max_workers=1)
        self.addCleanup(self.worker.shutdown)
        self.host.ordered = lambda callback: self.worker.submit(callback).result(timeout=3)
        self.addCleanup(self.host.close)
        self.notifications = []
        self.host.notify = lambda source, result: self.notifications.append(result)

    def start(self, events):
        self.runner = FakeRunner(events)
        identity = self.host.start(Script("test", "params {}", "r1"), [], self.source)
        return identity

    def wait_finished(self):
        deadline = time.monotonic() + 4
        while self.host.active() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(self.host.active(), ())
        self.assertTrue(self.runner.closed)

    @staticmethod
    def op(identity, operation, params):
        return {"type": "operation", "id": identity, "target": "Alice", "operation": operation, "params": params}

    def test_normal_end_cleans_own_inputs_and_uuid_pins_cleanup(self):
        self.start([self.op(2, "move", {"direction": "forward"}), {"type": "done"}])
        self.wait_finished()
        moves = [params for op, params in self.raw.calls if op == "move"]
        self.assertEqual([p["direction"] for p in moves], ["forward", "stop"])
        self.assertIn("expectedIdentityId", moves[-1])
        deadline = time.monotonic() + 2
        while not self.notifications and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(
            any(
                "input cleanup completed" in line
                for result in self.notifications for line in result.log_messages or ()
            )
        )

    def test_unlimited_run_wait_and_inspection_still_has_five_second_cap(self):
        self.host.config = replace(self.config, wall_seconds=0)
        with patch.object(self.host, "_process", return_value=self.runner) as process:
            self.host.inspect(Script("draft", "params {}", "r1"), self.source)
            process.assert_called_once_with("params {}", 5)
        identity = self.start([{"type": "wait", "id": 2, "ticks": 100000}])
        run = self.host.active()[0]
        self.assertIsNone(run.deadline)
        with patch("endstone_endbot.scripting.time.monotonic", return_value=10**12):
            self.host._alive(run)
        self.host.stop(self.source, identity)
        self.wait_finished()

    def test_unlimited_run_ipc_rate_bounds_logs_and_zero_waits(self):
        self.host.config = replace(self.config, wall_seconds=0, operations_per_second=1)
        for kind in ("log", "wait"):
            events = [{"type": kind, "id": i, "ticks": 0, "text": "bounded"} for i in range(2, 8)]
            self.start(events)
            self.wait_finished()
            self.assertLess(len(self.runner.replies), 7)
        logs = [line for result in self.notifications for line in result.log_messages or ()]
        self.assertTrue(any("rate limit" in line for line in logs))

    def logs(self):
        return [line for result in self.notifications for line in result.log_messages or ()]

    def test_script_text_in_logs_and_errors_cannot_forge_lines_or_drive_a_terminal(self):
        self.start([{"type": "log", "text": "ok\r\n[12:00 INFO]: forged \x1b[2J\u00a7cred"},
                    {"type": "error", "message": "boom\nline\x1b]0;title\x07"}])
        self.wait_finished()
        deadline = time.monotonic() + 2
        while not any("ended" in line for line in self.logs()) and time.monotonic() < deadline:
            time.sleep(0.01)
        for line in self.logs():
            self.assertNotRegex(line, r"[\x00-\x1f\x7f]")
            self.assertNotIn("\u00a7", line)
        self.assertTrue(any("forged" in line for line in self.logs()))

    def test_log_budget_drops_logs_past_the_limit_with_one_notice(self):
        self.host.config = replace(self.config, operations_per_second=100000)
        with patch("endstone_endbot.scripting.LOG_LINES", 3):
            self.start([*({"type": "log", "text": f"line {i}"} for i in range(6)), {"type": "done"}])
            self.wait_finished()
        logs = [line for line in self.logs() if "line " in line or "log limit" in line]
        self.assertEqual(len([line for line in logs if "line " in line]), 3)
        self.assertEqual(len([line for line in logs if "log limit" in line]), 1)

    def test_console_lists_and_stops_every_controllers_runs_and_actions_are_audited(self):
        console = CommandSource()
        other = CommandSource(player_id="other", name="Other", grant_revision=1)
        self.store.save("loop", "params {}", None)
        first = self.start([{"type": "wait", "id": 2, "ticks": 100000}])
        self.runner = FakeRunner([{"type": "wait", "id": 2, "ticks": 100000}])
        self.host.start(Script("loop", "params {}", "r1"), [], other)
        self.assertEqual(len(self.host.visible(self.source)), 1)
        listing = self.host.execute(["runs"], console)
        self.assertIn(f"{first} test (Caller)", listing.messages[0])
        self.assertIn("(Other)", listing.messages[0])
        self.assertEqual(self.host.execute(["stop", "all"], self.source).log_messages,
                         ("Stopping 1 script runs (all) by Caller",))
        stopped = self.host.execute(["stop", "all"], console)
        self.assertEqual(stopped.log_messages, ("Stopping 1 script runs (all) by console",))
        deadline = time.monotonic() + 4
        while self.host.active() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(self.host.active(), ())
        self.assertTrue(any("(controller Other)" in line for line in self.logs()))

    def test_wait_online_rejects_an_invalid_target(self):
        self.start([{"type": "waitOnline", "id": 2, "target": "x" * 5000}])
        self.wait_finished()
        self.assertTrue(any("Invalid script target" in line for line in self.logs()))

    def test_inspection_holds_a_run_slot_while_it_runs(self):
        self.host.config = replace(self.config, controller_runs=1)
        started = threading.Event()
        release = threading.Event()

        def slow(*args):
            started.set()
            release.wait(2)
            return self.runner

        with patch.object(self.host, "_process", side_effect=slow):
            worker = threading.Thread(target=self.host.inspect, args=(Script("d", "params {}", "r1"), self.source))
            worker.start()
            started.wait(2)
            with self.assertRaisesRegex(ValueError, "concurrency"):
                self.host.inspect(Script("d", "params {}", "r1"), self.source)
            release.set()
            worker.join(2)
        self.assertEqual(self.host._inspecting, {})

    def test_status_structured_runtime_job_and_group_member_payload(self):
        identity = self.raw.request("status", name="Alice")["identityId"]
        self.service.groups.create("miners")
        self.service.groups.add("miners", identity)
        original = self.raw.request
        for mining, active, mode in (
            (
                {"state": "waiting", "continuous": True, "loopId": "job", "blocksBroken": 1, "blockCount": 2},
                True,
                "continuous",
            ),
            (
                {"state": "completed", "blocksBroken": 2, "blockCount": 2, "reason": "block count reached"},
                False,
                "continuous",
            ),
            ({"state": "failed", "reason": "protected"}, False, "once"),
            ({}, False, None),
        ):
            def request(method, mining=mining, **params):
                status = original(method, **params)
                return {**status, "mining": mining} if method == "status" else status
            with patch.object(self.raw, "request", side_effect=request):
                status = self.service.execute(["Alice", "status"], self.source).data
                group = self.service.execute(["#miners", "status"], self.source).data
            self.assertEqual(status["mining"]["active"], active)
            self.assertEqual(status["mining"]["mode"], mode)
            self.assertEqual(status["mining"]["reason"], None if active else mining.get("reason"))
            self.assertEqual(group["results"][0]["data"], status)

    def test_wait_mining_completion_timeout_offline_failure_and_group(self):
        run = ScriptRun("run", "test", self.source)
        def status(active, reason=None, connection="online", state="completed"):
            return {"name": "Alice", "identityId": "uuid", "connectionState": connection,
                    "mining": {"active": active, "state": state, "reason": reason}}
        event = {"target": "Alice", "timeout": 20}
        for reason in ("block count reached", "duration elapsed", "stopped"):
            payloads = [{"result": {"ok": True, "data": status(True)}},
                        {"result": {"ok": True, "data": status(False, reason)}}]
            with patch.object(self.host, "_operation", side_effect=payloads), patch.object(self.host, "_wait") as pause:
                result = self.host._wait_mining(run, event)
                self.assertTrue(result["ok"])
                self.assertEqual(result["reason"], reason)
                self.assertEqual(len(result["results"]), 1)
                pause.assert_called_once()
        for data, reason in ((status(True), "timeout"), (status(False, connection="offline"), "offline"),
                             (status(False, "protected", state="failed"), "protected")):
            with patch.object(self.host, "_operation", return_value={"result": {"ok": True, "data": data}}):
                result = self.host._wait_mining(run, {**event, "timeout": 0})
                self.assertFalse(result["ok"])
                self.assertEqual(result["reason"], reason)
        rows = [{"ok": True, "name": name, "data": status(False, "block count reached")} for name in ("A", "B")]
        with patch.object(self.host, "_operation", return_value={"result": {"ok": True, "data": {"results": rows}}}):
            result = self.host._wait_mining(run, {"target": "#miners"})
            self.assertTrue(result["ok"])
            self.assertEqual([row["name"] for row in result["results"]], ["A", "B"])
        pending = [rows[0], {"ok": True, "name": "B", "data": status(True)}]
        with patch.object(self.host, "_operation", side_effect=[
            {"result": {"ok": True, "data": {"results": pending}}},
            {"result": {"ok": True, "data": {"results": rows}}},
        ]), patch.object(self.host, "_wait") as pause:
            result = self.host._wait_mining(run, {"target": "#miners"})
            self.assertTrue(result["ok"])
            pause.assert_called_once()
        mixed = [rows[0], {"ok": True, "name": "B", "data": status(False, connection="offline")}]
        with patch.object(self.host, "_operation", return_value={"result": {"ok": True, "data": {"results": mixed}}}):
            result = self.host._wait_mining(run, {"target": "#miners"})
            self.assertFalse(result["ok"])
            self.assertEqual(result["reason"], "offline")
            self.assertEqual([row["reason"] for row in result["results"]], ["block count reached", "offline"])
        for ticks in (-1, True, float("inf"), 1.5):
            with self.assertRaises(ValueError):
                self.host._wait_mining(run, {**event, "timeout": ticks})

    def test_wait_mining_rechecks_authority_stop_and_group_membership(self):
        identity = self.raw.request("status", name="Alice")["identityId"]
        self.service.groups.create("miners")
        self.service.groups.add("miners", identity)
        original = self.raw.request
        def request(method, **params):
            value = original(method, **params)
            return {**value, "mining": {"state": "waiting", "loopId": "job"}} if method == "status" else value
        for change in ("revoke", "stop", "shutdown", "membership"):
            run = ScriptRun("run", "test", self.source)
            self.allowed = True
            self.host._open = True
            def pause(*args, change=change, run=run):
                if change == "revoke":
                    self.allowed = False
                elif change == "stop":
                    run.canceled.set()
                elif change == "shutdown":
                    self.host.cancel()
                else:
                    self.service.groups.remove("miners", identity)
            with (
                patch.object(self.raw, "request", side_effect=request),
                patch.object(self.host, "_wait", side_effect=pause),
                self.assertRaises(ValueError),
            ):
                self.host._wait_mining(run, {"target": "#miners", "timeout": 20})
        self.allowed = True
        self.host._open = True

    def test_wait_mining_pins_literal_bot_identity_between_polls(self):
        status = {**self.raw.request("status", name="Alice"), "mining": {"state": "active"}}
        identity, requests = status["identityId"], []
        def request(method, **params):
            requests.append(params)
            if params.get("expectedIdentityId") and params["expectedIdentityId"] != status["identityId"]:
                raise RuntimeControlError("identity_changed", "Bot identity changed")
            return status
        def replace_bot(*args):
            status["identityId"] = "22222222-2222-4222-8222-222222222222"
        with (
            patch.object(self.raw, "request", side_effect=request),
            patch.object(self.host, "_wait", side_effect=replace_bot),
        ):
            result = self.host._wait_mining(ScriptRun("run", "test", self.source), {"target": "Alice"})
        self.assertFalse(result["ok"])
        self.assertIn("identity changed", result["reason"])
        self.assertEqual(requests[-1]["expectedIdentityId"], identity)

    def test_wait_mining_protocol_and_short_timeout_at_low_operation_rate(self):
        self.start([{"type": "waitMining", "id": 2, "target": "Alice", "timeout": 0}, {"type": "done"}])
        self.wait_finished()
        result = next(reply["result"] for reply in self.runner.replies if reply["id"] == 2)
        self.assertTrue(result["ok"])
        self.assertEqual(result["reason"], "idle")
        self.host.config = replace(self.config, operations_per_second=1)
        run, clock = ScriptRun("wait", "test", self.source), [100.0]
        status = {**self.raw.request("status", name="Alice"), "mining": {"state": "active"}}
        self.host.ordered = lambda callback: callback()
        def pause(run, seconds):
            clock[0] += seconds
        with patch.object(self.raw, "request", return_value=status) as request, \
             patch.object(self.host, "_wait", side_effect=pause), \
             patch("endstone_endbot.scripting.time.monotonic", side_effect=lambda: clock[0]):
            result = self.host._wait_mining(run, {"target": "Alice", "timeout": 1})
        self.assertEqual(result["reason"], "timeout")
        self.assertEqual(request.call_count, 1)
        self.assertAlmostEqual(clock[0], 100.05)

    def test_revocation_is_checked_before_each_operation_and_cleanup_survives_it(self):
        self.runner = FakeRunner(
            [self.op(2, "move", {"direction": "forward"}), self.op(3, "look", {"yaw": 1, "pitch": 0})]
        )
        self.runner.after_reply = lambda reply: setattr(self, "allowed", False) if reply["id"] == 2 else None
        self.host.start(Script("test", "params {}", "r1"), [], self.source)
        self.wait_finished()
        self.assertFalse(any(op == "look" for op, _ in self.raw.calls))
        self.assertTrue(any(op == "move" and params["direction"] == "stop" for op, params in self.raw.calls))

    def test_cleanup_failure_is_reported_without_a_success_diagnostic(self):
        with patch.object(self.inputs, "cleanup", return_value=["unavailable"]):
            self.start([{"type": "done"}])
            self.wait_finished()
            deadline = time.monotonic() + 2
            while not self.notifications and time.monotonic() < deadline:
                time.sleep(0.01)
        output = "\n".join(line for result in self.notifications for line in result.log_messages or ())
        self.assertIn("input cleanup failed", output)
        self.assertNotIn("input cleanup completed", output)

    def test_stop_limits_and_shutdown(self):
        identity = self.start([self.op(2, "move", {"direction": "forward"}), {"type": "wait", "id": 3, "ticks": 100}])
        with self.assertRaises(ValueError):
            other = replace(self.host.config, controller_runs=1)
            with patch.object(self.host, "config", other):
                self.host.start(Script("other", "params {}", "r1"), [], self.source)
        self.assertEqual(self.host.stop(self.source, identity), 1)
        self.wait_finished()
        self.assertEqual(self.host.stop(self.source, "test"), 0)
        self.start([{"type": "wait", "id": 2, "ticks": 100}])
        self.host.ordered(self.host.close)
        self.wait_finished()

    def test_later_controller_input_is_not_cleaned_but_other_run_channels_are(self):
        with self.inputs.scope("run1", lambda: None):
            self.inputs.request("mine", name="Alice", mode="start")
            self.inputs.request("move", name="Alice", direction="forward")
            self.inputs.request("action", name="Alice", action="jump", mode="continuous")
        self.inputs.request("move", name="Alice", direction="left")
        self.raw.calls.clear()
        self.inputs.cleanup("run1")
        self.assertFalse(any(op == "move" for op, _ in self.raw.calls))
        self.assertEqual({op for op, _ in self.raw.calls}, {"action", "mine"})
        self.assertEqual(next(p for op, p in self.raw.calls if op == "mine")["loopId"], "loop-1")

    def test_crash_timeout_and_rate_limit_retire_only_affected_run(self):
        self.start([self.op(2, "move", {"direction": "forward"}), {"type": "error", "message": "crash"}])
        self.wait_finished()
        self.assertEqual([p["direction"] for op, p in self.raw.calls if op == "move"], ["forward", "stop"])
        self.host.config = replace(self.config, operations_per_second=1)
        self.raw.calls.clear()
        self.start([self.op(2, "look", {"yaw": 0, "pitch": 0}), self.op(3, "look", {"yaw": 90, "pitch": 0})])
        self.wait_finished()
        self.assertEqual(sum(op == "look" for op, _ in self.raw.calls), 1)
        self.host.config = replace(self.config, wall_seconds=1)
        self.start([])
        self.wait_finished()

    def test_group_replacement_after_members_enumeration_is_rejected(self):
        group = self.service.groups.create("miners")
        self.service.groups.add("miners", "11111111-1111-4111-8111-111111111111")
        event = {"type": "members", "id": 2, "target": "#miners"}
        self.runner = FakeRunner([event, {**self.op(3, "move", {"direction": "forward"}), "target": "#miners[1]"}])

        def replace_group(reply):
            if reply["id"] == 2:
                self.service.groups.delete("miners")
                self.service.groups.create("miners")
                self.service.groups.add("miners", "11111111-1111-4111-8111-111111111111")

        self.runner.after_reply = replace_group
        self.host.start(Script("test", "params {}", "r1"), [], self.source)
        self.wait_finished()
        self.assertNotEqual(self.service.groups.get("miners").identity, group.identity)
        self.assertFalse(any(op == "move" for op, _ in self.raw.calls))

    def test_batch_ownership_per_request_auth_and_unreachable_cleanup(self):
        checks = []
        batch = [
            {
                "method": "move",
                "params": {
                    "name": "Alice",
                    "direction": "forward",
                    "expectedIdentityId": "11111111-1111-4111-8111-111111111111",
                },
            }
        ]
        with self.inputs.scope("run1", lambda: checks.append(True)):
            self.inputs.request("groupInputBatch", requests=batch)
            self.inputs.request("action", name="Alice", action="jump", mode="continuous")
        self.assertEqual(len(checks), len(self.raw.calls))
        with patch.object(self.raw, "request", side_effect=RuntimeControlError("unavailable", "offline")) as request:
            self.assertEqual(self.inputs.cleanup("run1"), ["offline"])
            self.assertEqual(request.call_count, 1)
        self.assertEqual(self.inputs.cleanup("run1"), [])

    def test_parameter_form_rejects_recreated_group_and_declared_bot_requests_pin_uuid(self):
        old = self.service.groups.create("miners")
        self.service.groups.delete("miners")
        self.service.groups.create("miners")
        self.runner = FakeRunner([{"type": "done"}], [{"name": "g", "type": "group"}])
        self.host.start(
            Script("test", "params {}", "r1"),
            {"g": "#miners"},
            self.source,
            {"g": ("group", "#miners", old.identity, old.members)},
        )
        self.wait_finished()
        self.assertEqual([reply for reply in self.runner.replies if reply.get("args")], [])
        self.raw.calls.clear()
        self.runner = FakeRunner(
            [self.op(2, "move", {"direction": "forward"}), {"type": "done"}], [{"name": "name", "type": "bot"}]
        )
        self.host.start(Script("test", "params {}", "r1"), {"name": "Alice"}, self.source)
        self.wait_finished()
        first = next(params for method, params in self.raw.calls if method == "move")
        self.assertEqual(first["expectedIdentityId"], "11111111-1111-4111-8111-111111111111")

    def test_stale_mining_poll_cannot_disown_a_newer_runs_loop(self):
        status = self.raw.request("status", name="Alice")
        first = {**status, "mining": {"loopId": "first"}}
        second = {**status, "mining": {"loopId": "second"}}
        with patch.object(self.raw, "request", side_effect=[status, first, status, second, second]):
            with self.inputs.scope("run1", lambda: None):
                self.inputs.request("mine", name="Alice", mode="start")
            with self.inputs.scope("run2", lambda: None):
                self.inputs.request("mine", name="Alice", mode="start")
            self.inputs.request("mine", name="Alice", mode="stop", loopId="first")
        self.raw.calls.clear()
        self.assertEqual(self.inputs.cleanup("run1"), [])
        self.inputs.cleanup("run2")
        self.assertEqual(self.raw.calls[0][1]["loopId"], "second")

    def test_lost_ack_retains_pinned_cleanup_and_later_inputs_replace_it(self):
        original = self.raw.request

        def lost_move(method, **params):
            if method == "move":
                raise RuntimeControlError("unavailable", "lost acknowledgement")
            return original(method, **params)

        with (
            self.inputs.scope("run1", lambda: None),
            patch.object(self.raw, "request", side_effect=lost_move),
            self.assertRaises(RuntimeControlError),
        ):
            self.inputs.request("move", name="Alice", direction="forward")
        self.raw.calls.clear()
        self.inputs.cleanup("run1")
        self.assertEqual(self.raw.calls[-1][1]["direction"], "stop")
        self.assertIn("expectedIdentityId", self.raw.calls[-1][1])
        with (
            self.inputs.scope("run2", lambda: None),
            patch.object(self.raw, "request", side_effect=lost_move),
            self.assertRaises(RuntimeControlError),
        ):
            self.inputs.request("move", name="Alice", direction="forward")
        self.inputs.request("move", name="Alice", direction="left")
        self.raw.calls.clear()
        self.inputs.cleanup("run2")
        self.assertEqual(self.raw.calls, [])

    def test_disabled_service_rejects_every_entry_without_constructing_runner(self):
        with patch.object(self.host, "config", ScriptConfig()), patch.object(self.host, "_process") as process:
            for argv in ([], ["list"], ["runs"], ["help"], ["menu"], ["show", "test"], ["run", "test"],
                         ["stop", "test"], ["delete", "test"], ["save", "test", "params {}"]):
                for source in (self.source, CommandSource(sender=object())):
                    with self.subTest(argv=argv, console=source.player_id is None):
                        result = self.host.execute(argv, source)
                        self.assertFalse(result.accepted)
                        self.assertIn("Scripting is disabled", " ".join(result.messages))
            process.assert_not_called()

    def test_runner_declaration_line_errors_are_localized_and_invalid_schema_is_not_saved(self):
        for message in ("Script parameters must be an array", "Duplicate script parameter name",
                        "Invalid script parameter name", "Unknown script parameter type"):
            self.runner = FakeRunner([])
            self.runner.events = queue.Queue()
            self.runner.events.put({"type": "error", "message": f"script:4: {message}"})
            source = replace(self.source, language="ja", group_guard=script_forms.ScriptGuard(edit=True))
            result = self.host.execute(["save", "bad", "params {bad=1}"], source)
            self.assertFalse(result.accepted)
            self.assertIn("script:4", " ".join(result.messages))
            self.assertNotIn(message, " ".join(result.messages))
            self.assertEqual(self.store.list(), ())
        for schema in ({}, [{"name": "x", "type": "number"}] * 2):
            self.runner = FakeRunner([], schema)
            result = self.host.execute(["save", "bad", "params {}"],
                                       replace(self.source, group_guard=script_forms.ScriptGuard(edit=True)))
            self.assertFalse(result.accepted)
            self.assertEqual(self.store.list(), ())

    def test_raw_save_is_rejected_and_form_save_inspects_without_body_acceptance(self):
        self.assertFalse(self.host.execute(["save", "test", "params {}"], self.source).accepted)
        source = replace(self.source, group_guard=script_forms.ScriptGuard(edit=True))
        result = self.host.execute(["save", "test", "params {}"], source)
        self.assertTrue(result.accepted)
        self.assertEqual(self.store.get("test").source, "params {}")
        self.assertEqual(self.runner.replies, [])


class OwnershipTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "scripts.json"
        self.config = ScriptConfig(enabled=True)
        self.store = ScriptStore(self.path, self.config)
        self.host = ScriptService(self.config, self.store, None, None, Path(directory.name), lambda source: True,
                                  process_factory=lambda *args: FakeRunner([]), bootstrap="trusted")
        self.alice = CommandSource(player_id="a-uuid", xuid="111", name="Alice", grant_revision=0)
        self.bob = CommandSource(player_id="b-uuid", xuid="222", name="Bob", grant_revision=0)
        self.console = CommandSource()

    def save(self, source, name, text, revision=None):
        guard = script_forms.ScriptGuard(revision, None if revision is None else name, edit=True)
        return self.host.execute(["save", name, text], replace(source, group_guard=guard))

    def text(self, result):
        return " ".join(result.messages)

    def test_owner_visibility_copy_history_and_restore(self):
        self.assertTrue(self.save(self.alice, "mine", "params {}\n-- v1").accepted)
        script = self.store.get("mine")
        self.assertEqual((script.owner, script.owner_name, script.visibility), ("xuid:111", "Alice", "private"))
        # Private: hidden from Bob, who cannot run, show, edit or delete it.
        self.assertNotIn("mine", self.text(self.host.execute(["list"], self.bob)))
        for argv in (["show", "mine"], ["run", "mine"], ["delete", "mine"], ["copy", "mine", "x"]):
            self.assertIn("does not exist", self.text(self.host.execute(argv, self.bob)), argv)
        self.assertIn("mine (yours, private)", self.text(self.host.execute(["list"], self.alice)))
        self.assertTrue(self.host.execute(["share", "mine"], self.alice).accepted)
        # Shared: Bob may run, show and copy, never change.
        self.assertIn("mine (Alice, shared)", self.text(self.host.execute(["list"], self.bob)))
        self.assertTrue(self.host.execute(["show", "mine"], self.bob).accepted)
        for argv in (["delete", "mine"], ["private", "mine"], ["restore", "mine", "1"]):
            self.assertIn("Only the owner Alice", self.text(self.host.execute(argv, self.bob)), argv)
        stale = self.store.get("mine").revision
        self.assertIn("Only the owner", self.text(self.save(self.bob, "mine", "params {}\n-- bob", stale)))
        self.assertTrue(self.host.execute(["copy", "mine", "bobs"], self.bob).accepted)
        copy = self.store.get("bobs")
        self.assertEqual((copy.owner, copy.visibility, copy.source), ("xuid:222", "private", "params {}\n-- v1"))
        self.assertIn("already exists", self.text(self.host.execute(["copy", "mine", "bobs"], self.bob)))
        # History: each save keeps the previous source; restore brings it back as a new revision.
        self.assertTrue(self.save(self.alice, "mine", "params {}\n-- v2", stale).accepted)
        self.assertEqual([r.source for r in self.store.get("mine").history], ["params {}\n-- v1"])
        self.assertIn("1: ", self.text(self.host.execute(["history", "mine"], self.alice)))
        self.assertTrue(self.host.execute(["restore", "mine", "1"], self.alice).accepted)
        restored = self.store.get("mine")
        self.assertEqual(restored.source, "params {}\n-- v1")
        self.assertEqual([r.source for r in restored.history], ["params {}\n-- v2", "params {}\n-- v1"])
        self.assertIn("out of range", self.text(self.host.execute(["restore", "mine", "9"], self.alice)))
        # The console is the operator: it sees and changes every script.
        self.assertTrue(self.host.execute(["private", "bobs"], self.console).accepted)
        self.assertIn("bobs (Bob, private)", self.text(self.host.execute(["list"], self.console)))
        reloaded = ScriptStore(self.path, self.config).get("mine")
        self.assertEqual(reloaded, restored)

    def test_history_is_bounded_and_legacy_scripts_are_claimed_by_their_next_editor(self):
        self.path.write_text(json.dumps({"version": 1, "scripts": [
            {"name": "old", "source": "params {}", "revision": "r0"}]}), encoding="utf-8")
        store = ScriptStore(self.path, self.config)
        self.host.store = store
        legacy = store.get("old")
        self.assertEqual((legacy.owner, legacy.visibility), (None, "shared"))
        self.assertIn("old (unowned, shared)", self.text(self.host.execute(["list"], self.bob)))
        revision = "r0"
        for index in range(12):
            self.assertTrue(self.save(self.bob, "old", f"params {{}}\n-- {index}", revision).accepted)
            revision = store.get("old").revision
        claimed = store.get("old")
        self.assertEqual((claimed.owner, claimed.owner_name, claimed.visibility), ("xuid:222", "Bob", "shared"))
        self.assertEqual(len(claimed.history), 10)
        self.assertIn("Only the owner Bob", self.text(self.save(self.alice, "old", "params {}", revision)))

    def test_menus_offer_owner_actions_only_to_the_owner(self):
        self.save(self.alice, "mine", "params {}\n")
        script = self.store.get("mine")
        own = [button.text for button in script_forms.script_screen(script, "en", True).items]
        other = [button.text for button in script_forms.script_screen(script, "en", False).items]
        self.assertIn("Edit source...", own)
        self.assertIn("Share with controllers", own)
        self.assertNotIn("Edit source...", other)
        self.assertNotIn("Delete", other)
        self.assertIn("Copy and edit...", other)
        copy = next(button for button in script_forms.script_screen(script, "en", False).items
                    if button.text == "Copy and edit...").action.screen
        self.assertEqual(copy.controls[0].default_value, "mine_copy")
        self.assertIsNone(copy.guard.revision)


class ScriptFormTests(unittest.TestCase):
    def test_paste_field_starts_empty_and_guards_stale_edit(self):
        script = Script("sample", "params {}\n", "r1")
        screen = script_forms.edit_screen(script)
        self.assertEqual(screen.controls[-1].default_value, "")
        self.assertEqual(screen.controls[0].text, "Current source:\n1: params {}")
        # A client that sends null for the label, and one that leaves it out of the response.
        self.assertEqual(forms.modal_commands(screen, json.dumps([None, 0, "", "", "x"])), (("save", "sample", "x\n"),))
        self.assertEqual(
            forms.modal_commands(screen, json.dumps([0, "", "", "\r\nparams {}\r\n"])),
            (("save", "sample", "\nparams {}\n"),),
        )
        # An empty paste never wipes the source.
        with self.assertRaisesRegex(forms.FormResponseError, "nothing was changed"):
            forms.modal_commands(screen, json.dumps([0, "", "", ""]))
        self.assertEqual(screen.guard.revision, "r1")
        self.assertTrue(screen.guard.edit)

    def test_line_edits(self):
        source = "a\nb\nc\n"
        edit = script_forms.apply_edit
        self.assertEqual(edit(source, "Append at end", "d\n", "", ""), "a\nb\nc\nd\n")
        self.assertEqual(edit(source, "Replace lines", "B1\nB2", "2", ""), "a\nB1\nB2\nc\n")
        self.assertEqual(edit(source, "Replace lines", "X", "1", "2"), "X\nc\n")
        self.assertEqual(edit(source, "Insert after line", "top", "0", ""), "top\na\nb\nc\n")
        self.assertEqual(edit(source, "Insert after line", "end", "3", ""), "a\nb\nc\nend\n")
        self.assertEqual(edit(source, "Delete lines", "", "2", "3"), "a\n")
        for mode, first, last in (("Delete lines", "4", ""), ("Delete lines", "2", "1"), ("Replace lines", "", ""),
                                  ("Insert after line", "x", ""), ("Replace lines", "1", "")):
            with self.subTest(mode=mode, first=first, last=last), self.assertRaises(forms.FormResponseError):
                edit(source, mode, "", first, last)

    def test_new_script_keeps_template_when_paste_is_empty(self):
        screen = script_forms.edit_screen(None, "en", "params {}\n", "row")
        self.assertEqual(forms.modal_commands(screen, json.dumps(["row", None, 0, "", "", ""])),
                         (("save", "row", "params {}\n"),))
        self.assertEqual(forms.modal_commands(screen, json.dumps(["row", None, 0, "", "", "params {}\nlog'x'"])),
                         (("save", "row", "params {}\nlog'x'\n"),))

    def test_param_controls_and_generated_values(self):
        script = Script("sample", "params {}", "r1")
        schema = [
            {"name": "g", "type": "group"},
            {"name": "b", "type": "bot"},
            {"name": "p", "type": "pos", "default": "~ ~ ~"},
            {"name": "n", "type": "number", "default": 2},
            {"name": "f", "type": "boolean", "default": False},
            {"name": "s", "type": "string", "default": "x"},
        ]
        group = type("G", (), {"name": "miners", "identity": "group-id", "members": ("bot-id",)})()
        screen = script_forms.run_screen(
            script, schema, [group], [{"name": "Alice", "identityId": "bot-id"}], {"x": 1, "y": 64, "z": 2}
        )
        self.assertIsInstance(screen.controls[0], forms.Dropdown)  # g
        self.assertIsInstance(screen.controls[1], forms.Dropdown)  # b
        self.assertIsInstance(screen.controls[4], forms.Toggle)  # f
        self.assertEqual(screen.controls[2].default_value, "1 64 2")
        self.assertEqual([control.label for control in screen.controls], [f"{s['name']} ({s['type']})" for s in schema])
        self.assertEqual(
            forms.modal_commands(screen, json.dumps([0, 0, "1 64 2", "3", True, "hello world"])), (("run", "sample"),)
        )
        self.assertEqual(screen.guard.values["g"], "#miners")
        self.assertEqual(screen.guard.values["s"], "hello world")
        self.assertEqual(screen.guard.targets["g"], ("group", "#miners", "group-id", ("bot-id",)))
        self.assertEqual(screen.guard.targets["b"], ("bot", "Alice", "bot-id"))

    def test_templates_paged_source_and_hidden_menu(self):
        self.assertEqual(set(script_forms.TEMPLATES), {"Group row", "Simple patrol", "Mine at spots"})
        source = "x" * 4000
        pages = script_forms.source_pages(Script("sample", source, "r1"))
        self.assertGreater(len(pages), 1)
        self.assertEqual(sum(page.count("x") for page in pages), 4000)
        for enabled in (True, False):
            screen = forms.bot_list_screen(forms.BotListView((), scripting=enabled))
            self.assertEqual(any(button.text == "Scripts..." for button in screen.items), enabled)


if __name__ == "__main__":
    unittest.main()


class FakeTag:
    def __init__(self, value):
        self.value = value


class FakeItem:
    def __init__(self, kind, pages=None):
        self.type = type("T", (), {"id": kind})()
        self.nbt = None if pages is None else {"pages": [{"text": FakeTag(page)} for page in pages]}


class BookWorld:
    """Stands in for the server-thread world: one player's main-hand item."""

    def __init__(self, item):
        self.item, self.written = item, None

    def held_book_pages(self, player_id):
        from endstone_endbot.script_books import book_pages

        return book_pages(self.item)

    def fill_held_book(self, player_id, pages):
        from endstone_endbot.script_books import WRITABLE_BOOK

        if self.item is None or self.item.type.id != WRITABLE_BOOK or self.held_book_pages(player_id):
            raise LocalizedError("The book and quill is not empty; hold an empty one")
        self.written = pages


class BookTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.config = ScriptConfig(enabled=True, book_export=True)
        self.store = ScriptStore(Path(directory.name) / "scripts.json", self.config)
        self.world = BookWorld(FakeItem("minecraft:writable_book", ["params {}", "log('a')\n", "", ""]))
        self.host = ScriptService(self.config, self.store, Mock(world=self.world), None, Path(directory.name),
                                  lambda source: True, process_factory=lambda *args: FakeRunner([]),
                                  bootstrap="trusted")
        self.alice = CommandSource(player_id="a-uuid", xuid="111", name="Alice", grant_revision=0)
        self.bob = CommandSource(player_id="b-uuid", xuid="222", name="Bob", grant_revision=0)

    def text(self, result):
        return " ".join(result.messages)

    def test_pages_round_trip_by_whole_lines(self):
        from endstone_endbot.script_books import BOOK_PAGE_CHARS, pages_to_source, source_to_pages

        self.assertEqual(pages_to_source(["a\nb", "c\n", "d", "  "]), "a\nb\nc\nd\n")
        source = "".join(f"line {index} " + "x" * 40 + "\n" for index in range(30))
        pages = source_to_pages(source)
        self.assertTrue(all(len(page) <= BOOK_PAGE_CHARS for page in pages))
        self.assertEqual(pages_to_source(pages), source)
        with self.assertRaisesRegex(LocalizedError, "Line 2 is longer"):
            source_to_pages("ok\n" + "y" * (BOOK_PAGE_CHARS + 1))
        with self.assertRaisesRegex(LocalizedError, "does not fit in one book"):
            source_to_pages("z" * BOOK_PAGE_CHARS + "\n" + ("z" * BOOK_PAGE_CHARS + "\n") * 50)
        with self.assertRaisesRegex(LocalizedError, "empty"):
            pages_to_source(["", " "])

    def test_import_creates_then_revises_and_respects_ownership(self):
        result = self.host.execute(["import", "fromBook"], self.alice)
        self.assertIn("imported from the book (2 lines)", self.text(result))
        script = self.store.get("frombook")
        self.assertEqual((script.name, script.source, script.owner), ("fromBook", "params {}\nlog('a')\n", "xuid:111"))
        self.world.item = FakeItem("minecraft:written_book", ["params {}\n-- v2"])
        self.assertTrue(self.host.execute(["import", "FROMBOOK"], self.alice).accepted)
        script = self.store.get("frombook")
        self.assertEqual((script.name, script.source), ("fromBook", "params {}\n-- v2\n"))
        self.assertEqual(len(script.history), 1)
        self.host.execute(["share", "fromBook"], self.alice)
        self.assertIn("Only the owner Alice", self.text(self.host.execute(["import", "fromBook"], self.bob)))
        self.world.item = FakeItem("minecraft:stick")
        self.assertIn("Hold a book", self.text(self.host.execute(["import", "other"], self.alice)))
        self.assertIn("only to players", self.text(self.host.execute(["import", "other"], CommandSource())))

    def test_export_fills_only_an_empty_book_and_can_be_disabled(self):
        self.host.execute(["import", "mine"], self.alice)
        self.assertIn("not empty", self.text(self.host.execute(["export", "mine"], self.alice)))
        self.world.item = FakeItem("minecraft:writable_book")
        self.assertIn("written into the book (1 pages)", self.text(self.host.execute(["export", "mine"], self.alice)))
        self.assertEqual(self.world.written, ["params {}\nlog('a')"])
        # Others may export only scripts they can see.
        self.assertIn("does not exist", self.text(self.host.execute(["export", "mine"], self.bob)))
        self.host.config = replace(self.config, book_export=False)
        self.assertIn("disabled", self.text(self.host.execute(["export", "mine"], self.alice)))
        self.assertNotIn("Write into held book",
                         [b.text for b in script_forms.script_screen(self.store.get("mine"), "en", True).items])

    def test_fill_book_writes_pages_into_the_item_tag(self):
        from endstone_endbot.script_books import fill_book

        nbt = types.ModuleType("endstone.nbt")
        nbt.CompoundTag, nbt.ListTag, nbt.StringTag = dict, list, FakeTag
        with patch.dict(sys.modules, {"endstone": types.ModuleType("endstone"), "endstone.nbt": nbt}):
            item = fill_book(FakeItem("minecraft:writable_book", []), ["a", "b"])
        self.assertEqual([page["text"].value for page in item.nbt["pages"]], ["a", "b"])
        with self.assertRaisesRegex(LocalizedError, "empty book and quill"):
            fill_book(FakeItem("minecraft:written_book", []), ["a"])
