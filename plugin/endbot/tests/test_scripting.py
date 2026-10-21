"""No native runner or Endstone required: exercise the real host lifecycle."""

import json
import queue
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from endstone_endbot import forms, script_forms
from endstone_endbot.commands import HELP_TOPICS, parse_command
from endstone_endbot.control import RuntimeControlError
from endstone_endbot.dispatch import CommandSource
from endstone_endbot.groups import GroupCommandService, GroupStore
from endstone_endbot.operations import OPERATIONS, operation_argv
from endstone_endbot.script_config import ScriptConfig
from endstone_endbot.script_inputs import OwnedInputs
from endstone_endbot.script_params import arguments, declarations
from endstone_endbot.script_store import Script, ScriptStore
from endstone_endbot.scripting import ScriptService


class ConfigStorageParamsTests(unittest.TestCase):
    def test_startup_defaults_and_strict_settings(self):
        self.assertFalse(ScriptConfig.from_config({}).enabled)
        for setting in (
            {"enabled": "false"},
            {"server-runs": True},
            {"wall-seconds": 0},
            {"interrupt-budget": -1},
            {"max-script-bytes": 65537},
            {"unknown": 1},
            [],
        ):
            with self.subTest(setting=setting), self.assertRaises(ValueError):
                ScriptConfig.from_config(setting)
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
            self.assertEqual(json.loads(path.read_text())["version"], 1)

    def test_all_param_types_relative_positions_and_alphabetical_order(self):
        groups = type("Groups", (), {"get": lambda self, name: type("G", (), {"name": name})()})()
        schema = {
            "text": {"type": "string", "default": "x"},
            "origin": {"type": "pos", "default": "~ ~ ~"},
            "group": {"type": "group"},
            "flag": {"type": "boolean", "default": False},
            "name": {"type": "bot"},
            "spacing": {"type": "number", "default": 2},
        }
        result = arguments(
            schema,
            ["true", "#miners", "alice"],
            groups=groups,
            bots=[{"name": "Alice"}],
            origin={"x": 1, "y": 64, "z": 2},
        )
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
                arguments({"x": {"type": kind}}, [value], groups=groups, bots=[], origin=None)
        for schema in (
            {"bad key": {"type": "number"}},
            {"n": {"type": "number", "default": True}},
            {"n": {"type": "boolean", "default": "false"}},
            {"n": {"type": "command"}},
        ):
            with self.assertRaises(ValueError):
                declarations(schema)


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
            "interact": [({"x": 1, "y": 64, "z": 2, "face": "up"}, ["interact", "1", "64", "2", "up"])],
            "destroy": [
                ({"mode": "continuous", "count": 64}, ["destroy", "continuous", "count", "64"]),
                ({"x": 1, "y": 64, "z": 2, "face": "up"}, ["destroy", "1", "64", "2", "up"]),
            ],
            "drop": [({}, ["drop"]), ({"selector": "all"}, ["drop", "all"])],
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
        status = {"name": "Alice", "identityId": "11111111-1111-4111-8111-111111111111", "connectionState": "online"}
        if method == "list":
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
        for event in [{"type": "params", "id": 1, "schema": schema or {}}, *events]:
            self.events.put(event)
        self.replies = []
        self.closed = False
        self.after_reply = None

    def receive(self, deadline, canceled, check):
        while True:
            check()
            if canceled.is_set() or time.monotonic() >= deadline:
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

    def test_revocation_is_checked_before_each_operation_and_cleanup_survives_it(self):
        self.runner = FakeRunner(
            [self.op(2, "move", {"direction": "forward"}), self.op(3, "look", {"yaw": 1, "pitch": 0})]
        )
        self.runner.after_reply = lambda reply: setattr(self, "allowed", False) if reply["id"] == 2 else None
        self.host.start(Script("test", "params {}", "r1"), [], self.source)
        self.wait_finished()
        self.assertFalse(any(op == "look" for op, _ in self.raw.calls))
        self.assertTrue(any(op == "move" and params["direction"] == "stop" for op, params in self.raw.calls))

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
        self.runner = FakeRunner([{"type": "done"}], {"g": {"type": "group"}})
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
            [self.op(2, "move", {"direction": "forward"}), {"type": "done"}], {"name": {"type": "bot"}}
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
            self.assertFalse(self.host.execute(["list"], self.source).accepted)
            self.assertFalse(self.host.execute(["run", "test"], self.source).accepted)
            self.assertFalse(self.host.execute(["save", "test", "params {}"], self.source).accepted)
            process.assert_not_called()

    def test_raw_save_is_rejected_and_form_save_inspects_without_body_acceptance(self):
        self.assertFalse(self.host.execute(["save", "test", "params {}"], self.source).accepted)
        source = replace(self.source, group_guard=script_forms.ScriptGuard(edit=True))
        result = self.host.execute(["save", "test", "params {}"], source)
        self.assertTrue(result.accepted)
        self.assertEqual(self.store.get("test").source, "params {}")
        self.assertEqual(self.runner.replies, [])


class ScriptFormTests(unittest.TestCase):
    def test_paste_preserves_source_and_guards_stale_edit(self):
        script = Script("sample", "params {}\n", "r1")
        screen = script_forms.edit_screen(script)
        self.assertEqual(screen.controls[0].default_value, script.source)
        self.assertEqual(
            forms.modal_commands(screen, json.dumps(["\nparams {}\n"])), (("save", "sample", "\nparams {}\n"),)
        )
        self.assertEqual(screen.guard.revision, "r1")
        self.assertTrue(screen.guard.edit)

    def test_param_controls_and_generated_values(self):
        script = Script("sample", "params {}", "r1")
        schema = {
            "g": {"type": "group"},
            "b": {"type": "bot"},
            "p": {"type": "pos", "default": "~ ~ ~"},
            "n": {"type": "number", "default": 2},
            "f": {"type": "boolean", "default": False},
            "s": {"type": "string", "default": "x"},
        }
        group = type("G", (), {"name": "miners", "identity": "group-id", "members": ("bot-id",)})()
        screen = script_forms.run_screen(
            script, schema, [group], [{"name": "Alice", "identityId": "bot-id"}], {"x": 1, "y": 64, "z": 2}
        )
        self.assertIsInstance(screen.controls[0], forms.Dropdown)  # b
        self.assertIsInstance(screen.controls[1], forms.Toggle)  # f
        self.assertEqual(screen.controls[4].default_value, "1 64 2")
        self.assertEqual(
            forms.modal_commands(screen, json.dumps([0, True, 0, "3", "1 64 2", "hello world"])), (("run", "sample"),)
        )
        self.assertEqual(screen.guard.values["g"], "#miners")
        self.assertEqual(screen.guard.values["s"], "hello world")
        self.assertEqual(screen.guard.targets["g"], ("group", "#miners", "group-id", ("bot-id",)))
        self.assertEqual(screen.guard.targets["b"], ("bot", "Alice", "bot-id"))

    def test_templates_paged_source_and_hidden_menu(self):
        self.assertEqual(len(script_forms.TEMPLATES), 2)
        source = "x" * 4000
        pages = script_forms.source_pages(Script("sample", source, "r1"))
        self.assertGreater(len(pages), 1)
        self.assertEqual(sum(page.count("x") for page in pages), 4000)
        for enabled in (True, False):
            screen = forms.bot_list_screen(forms.BotListView((), scripting=enabled))
            self.assertEqual(any(button.text == "Scripts..." for button in screen.items), enabled)


if __name__ == "__main__":
    unittest.main()
