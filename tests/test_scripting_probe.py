"""Offline probe plumbing/evaluator tests; these cannot establish live BDS results."""

import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "plugin/endbot/src"))

from scripts.scripting_probe import (
    BOT,
    BUDDY,
    GROUP,
    built_runner,
    configure_fixture,
    enable_config,
    exercise_scripting,
    install_probe,
    submit_save,
)


class ScriptingProbeTests(unittest.TestCase):
    def test_cli_forwards_opt_in_and_sandbox_without_enabling_scripting_by_default(self):
        from scripts import raknet_spike

        for enabled in (False, True):
            with self.subTest(enabled=enabled), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / "fresh-output"
                server = Path(directory) / "sandbox"
                argv = ["raknet_spike.py", "--output", str(output), "--node", "node"]
                if enabled:
                    argv.extend(["--scripting", "--server-dir", str(server)])

                def exercise(output, node, report, **kwargs):
                    report["ok"] = True

                with patch.object(sys, "argv", argv), \
                     patch.object(raknet_spike.subprocess, "run", return_value=SimpleNamespace(returncode=0)), \
                     patch.object(raknet_spike, "exercise", side_effect=exercise) as run, \
                     patch.object(raknet_spike, "check_locked_endstone"), \
                     patch.object(raknet_spike, "reusable_server") as sandbox, patch("builtins.print"):
                    sandbox.return_value.__enter__.return_value = server
                    self.assertEqual(raknet_spike.main(), 0)
                    self.assertEqual(run.call_args.kwargs["scripting"], enabled)
                    if enabled:
                        self.assertEqual(run.call_args.kwargs["server"], server)
                    else:
                        sandbox.assert_not_called()

    def test_scripting_prerequisite_failure_precedes_bds_acquisition(self):
        from scripts import raknet_spike

        with tempfile.TemporaryDirectory() as directory, \
             patch.object(raknet_spike, "ROOT", Path(directory)), \
             patch.object(raknet_spike, "check_locked_endstone") as locked:
            with self.assertRaisesRegex(RuntimeError, "node runner/build.mjs"):
                raknet_spike.exercise(Path(directory) / "output", "node", {}, scripting=True)
            locked.assert_not_called()
            self.assertFalse((Path(directory) / "output").exists())

    def test_requires_built_binary_and_matching_sidecar(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(RuntimeError, "node runner/build.mjs"):
                built_runner(root)
            binary = root / "runner/.build/native/bin" / ("endbot-luau-spike.exe" if os.name == "nt" else "endbot-luau-spike")
            binary.parent.mkdir(parents=True)
            api = root / "runner/api.luau"
            api.write_text("new bootstrap", encoding="utf-8")
            (root / "runner/main.cpp").write_text("source", encoding="utf-8")
            binary.write_bytes(b"test placeholder; never executed")
            sidecar = binary.with_name("api.luau")
            sidecar.write_text("old bootstrap", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "stale"):
                built_runner(root)
            sidecar.write_bytes(api.read_bytes())
            self.assertEqual(built_runner(root), binary)
            os.utime(binary, (1, 1))
            with self.assertRaisesRegex(RuntimeError, "stale"):
                built_runner(root)

    def test_config_enables_only_scripting_in_generated_config_and_fixture_changes_only_copy(self):
        import tomllib

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            config = output / "config.toml"
            config.write_text('[runtime]\nport=12345\n[ui]\nlanguage="ja"\n', encoding="utf-8")
            enable_config(config, output / "runner.exe")
            document = tomllib.loads(config.read_text(encoding="utf-8"))
            self.assertEqual(document["runtime"], {"port": 12345})
            self.assertEqual(document["ui"], {"language": "ja"})
            self.assertEqual(document["scripting"], {"enabled": True, "runner": str(output / "runner.exe"), "interrupt-budget": 10000})
            source = output / "mining-plugin/endstone_endbot/plugin.py"
            source.parent.mkdir(parents=True)
            source.write_text("# isolated copy\n", encoding="utf-8")
            original = (ROOT / "plugin/endbot/src/endstone_endbot/plugin.py").read_bytes()
            environment = {}
            configure_fixture(ROOT, output, environment)
            self.assertIn("install_scripting_probe(EndbotPlugin)", source.read_text(encoding="utf-8"))
            self.assertEqual(environment["ENDBOT_SCRIPTING_PROBE"], str(output))
            self.assertEqual((output / "mining-plugin/_endbot_scripting_probe.py").read_bytes(),
                             (ROOT / "scripts/scripting_probe.py").read_bytes())
            self.assertEqual((ROOT / "plugin/endbot/src/endstone_endbot/plugin.py").read_bytes(), original)

    def test_saves_submit_same_guarded_action_as_paste_form_without_touching_store(self):
        from endstone_endbot.script_forms import ScriptGuard

        plugin = SimpleNamespace(_command_runner=SimpleNamespace(submit=Mock(return_value=True)),
                                 server=SimpleNamespace(command_sender=object()))
        self.assertTrue(submit_save(plugin, "line", "params {}"))
        plugin._command_runner.submit.assert_called_once_with(
            ["save", "line", "params {}"], plugin.server.command_sender,
            command="botscript", group_guard=ScriptGuard(edit=True),
        )

    def test_installed_fixture_samples_sdk_locations_and_submits_each_save_once(self):
        callbacks = []
        writes = []
        blocks = {}
        def block_at(*cell):
            if cell not in blocks:
                block = SimpleNamespace(type="minecraft:air")
                def set_type(kind):
                    block.type = kind
                    writes.append(kind)
                block.set_type = set_type
                blocks[cell] = block
            return blocks[cell]
        dimension = SimpleNamespace(get_block_at=block_at)

        def location(dimension, x, y, z, yaw, pitch):
            return SimpleNamespace(dimension=dimension, x=x, y=y, z=z, yaw=yaw, pitch=pitch)

        players = {}
        for name in (BOT, BUDDY):
            player = SimpleNamespace(location=location(dimension, 10, 64, 20, 0, 0),
                                     inventory=SimpleNamespace(clear=Mock(), held_item_slot=4))
            player.teleport = lambda target, player=player: setattr(player, "location", target)
            players[name] = player

        class Plugin:
            def on_enable(self):
                self.original_enabled = True

        plugin = Plugin()
        plugin.server = SimpleNamespace(
            get_player=players.get,
            scheduler=SimpleNamespace(run_task=lambda owner, callback, **kwargs: callbacks.append(callback)),
        )
        with tempfile.TemporaryDirectory() as directory, \
             patch.dict(os.environ, {"ENDBOT_SCRIPTING_PROBE": directory}), \
             patch.dict(sys.modules, {"endstone.level": SimpleNamespace(Location=location)}), \
             patch("scripts.scripting_probe.submit_save", return_value=True) as save:
            output = Path(directory)
            install_probe(Plugin)
            plugin.on_enable()
            self.assertTrue(plugin.original_enabled)
            request = output / "scripting-request.json"
            request.write_text(json.dumps({"id": "setup", "action": "setup"}), encoding="utf-8")
            callbacks[0]()
            initial = json.loads((output / "scripting-state.json").read_text(encoding="utf-8"))
            self.assertEqual(initial["origin"], [10.5, 200, 20.5])
            self.assertEqual(initial["positions"][BOT], [16.5, 200, 24.5])
            self.assertIn("minecraft:stone", writes)
            self.assertIn("minecraft:air", writes)
            players[BOT].location.x = 123
            request.write_text(json.dumps({"id": "save", "action": "save", "name": "x", "source": "params {}"}), encoding="utf-8")
            callbacks[0]()
            callbacks[0]()
            save.assert_called_once_with(plugin, "x", "params {}")
            observed = json.loads((output / "scripting-state.json").read_text(encoding="utf-8"))
            self.assertEqual(observed["positions"][BOT][0], 123)
            self.assertGreater(observed["sequence"], initial["sequence"])
            request.write_text(json.dumps({"id": "mining", "action": "mining"}), encoding="utf-8")
            callbacks[0]()
            prepared = json.loads((output / "scripting-state.json").read_text(encoding="utf-8"))
            self.assertEqual([row["position"] for row in prepared["mining_blocks"]],
                             [[10, 201, 22], [10, 201, 23], [14, 201, 22], [14, 201, 23]])
            self.assertTrue(all(row["type"] == "minecraft:dirt" for row in prepared["mining_blocks"]))
            self.assertIn(":waitMining", prepared["mine_template"])
            players[BOT].inventory.clear.assert_called_once()
            self.assertEqual(players[BOT].inventory.held_item_slot, 0)
            blocks[(14, 201, 23)].set_type("minecraft:air")
            callbacks[0]()
            sampled = json.loads((output / "scripting-state.json").read_text(encoding="utf-8"))
            self.assertEqual(sampled["mining_blocks"][-1]["type"], "minecraft:air")

    def run_probe(self, failure=None):
        with tempfile.TemporaryDirectory() as directory:
            output, report = Path(directory), {}
            driver = FakeBds(output, failure)
            with patch("scripts.scripting_probe.time.sleep", side_effect=driver.advance), \
                 patch("scripts.scripting_probe.time.monotonic", side_effect=lambda: driver.clock):
                if failure:
                    with self.assertRaises(RuntimeError):
                        exercise_scripting(output, driver.console, driver.request, driver.wait, lambda: driver.log, report)
                else:
                    exercise_scripting(output, driver.console, driver.request, driver.wait, lambda: driver.log, report)
            self.assertEqual(json.loads((output / "scripting-results.json").read_text(encoding="utf-8")), report["scripting"])
            self.assertFalse((output / "scripting-request.json").exists())
            return report["scripting"], driver

    def test_console_smoke_records_authoritative_samples_commands_and_both_stop_selectors(self):
        result, driver = self.run_probe()
        self.assertEqual(result["status"], "passed")
        for phase in ("row", "stop_patrol", "overlap_cleanup", "mine", "runaway"):
            self.assertEqual(result[phase]["outcome"], "passed")
        self.assertIn("botscript stop patrol", driver.commands)
        self.assertIn("botscript stop 0003", driver.commands)
        self.assertIn('botscript run line #script_probe "0.5 200 0.5" 3', driver.commands)
        self.assertEqual(set(driver.saved), {"line", "patrol", "mine", "runaway"})
        self.assertTrue(all(block["type"] == "minecraft:air" for block in result["mine"]["after"]))
        self.assertIn("spot 2: block count reached", result["mine"]["output"])
        self.assertIn('param.group "group"', driver.saved["line"])
        self.assertIn("while true do end", driver.saved["runaway"])
        self.assertGreater(len(result["stop_patrol"]["samples"]), 3)
        self.assertIn("unit test", result["disabled_default"])

    def test_probe_fails_and_keeps_evidence_for_wrong_row_drift_cancelled_console_and_missing_budget(self):
        for failure in ("row", "drift", "overlap", "budget", "cleanup", "mining_blocks", "mining_reason", "mining_end"):
            with self.subTest(failure=failure):
                result, _ = self.run_probe(failure)
                self.assertEqual(result["status"], "failed")
                self.assertIn("error", result)


class FakeBds:
    """Independent position samples and console replies for testing the driver only."""

    def __init__(self, output, failure):
        self.output, self.failure = output, failure
        self.clock, self.sequence, self.serial = 0, 0, 0
        self.log, self.commands, self.saved = "", [], {}
        self.positions = {BOT: [6.5, 200, 4.5], BUDDY: [9.5, 200, 4.5]}
        self.movement, self.console_owned = {}, set()
        self.fixture_id, self.active, self.run_id = None, None, None
        self.mining_blocks = []

    def emit(self, text):
        self.log += text + "\n"

    def advance(self, seconds):
        self.clock += seconds
        for name, direction in self.movement.items():
            axis = 0 if direction == "right" else 2
            self.positions[name][axis] += seconds * 3
        self.sequence += 1
        self.write_state()

    def write_state(self):
        from endstone_endbot.script_forms import TEMPLATES

        (self.output / "scripting-state.json").write_text(json.dumps({
            "id": self.fixture_id, "sequence": self.sequence, "origin": [0.5, 200, 0.5],
            "positions": copy.deepcopy(self.positions),
            "row_template": TEMPLATES["Group row"],
            "mine_template": TEMPLATES["Mine at spots"], "mining_blocks": self.mining_blocks,
        }), encoding="utf-8")

    def request(self, method, **params):
        assert method == "status"
        return {"connectionState": "online"}

    def wait(self, label, predicate, timeout):
        if label.startswith("scripting fixture"):
            data = json.loads((self.output / "scripting-request.json").read_text(encoding="utf-8"))
            self.fixture_id = data["id"]
            if data["action"] == "save":
                self.saved[data["name"]] = data["source"]
                self.emit(f"Script {data['name']} saved")
            elif data["action"] == "mining":
                self.mining_blocks = [{"position": [spot * 4, 201, 2 + block], "type": "minecraft:dirt"}
                                      for spot in range(2) for block in range(2)]
            self.advance(0.05)
        for _ in range(int(timeout * 20)):
            if predicate():
                return
            self.advance(0.05)
        raise RuntimeError("timed out: " + label)

    def finish(self, name, run_id, reason):
        self.emit(f"Endbot script {run_id} {name} ended: {reason}")
        if self.failure != "cleanup":
            self.emit(f"Endbot script {run_id}: input cleanup completed")
        self.active = None

    def console(self, line):
        self.commands.append(line)
        if line == f"bot {BUDDY} spawn":
            self.emit(f"{BUDDY}: created and connecting")
        elif line == f"botgroup create {GROUP}":
            self.emit(f"{GROUP}: group created")
        elif line.startswith(f"botgroup add {GROUP} "):
            self.emit(f"{GROUP}: add: {line.split()[-1]}")
        elif line == "botscript list":
            self.emit("line, patrol, runaway")
        elif line == "botscript runs":
            self.emit(f"{self.run_id} {self.active}" if self.active else "No active script runs")
        elif line.startswith("botscript run "):
            self.serial += 1
            self.run_id, self.active = f"{self.serial:04d}", line.split()[2]
            self.emit(f"Script {self.active} started: {self.run_id}")
            if self.active == "line":
                if self.failure != "row":
                    self.positions = {BOT: [0.5, 200, 0.5], BUDDY: [3.5, 200, 0.5]}
                self.finish("line", self.run_id, "completed")
            elif self.active == "patrol":
                self.movement = {BOT: "forward", BUDDY: "forward"}
                self.console_owned.clear()
                self.emit("patrol moving")
            elif self.active == "mine":
                if self.failure != "mining_blocks":
                    for block in self.mining_blocks:
                        block["type"] = "minecraft:air"
                for spot in (1, 2):
                    reason = "timeout" if self.failure == "mining_reason" else "block count reached"
                    self.emit(f"Endbot script {self.run_id}: spot {spot}: {reason}")
                self.finish("mine", self.run_id, "failed" if self.failure == "mining_end" else "completed")
            else:
                reason = "Script runner failed: script:2: execution budget exceeded" if self.failure != "budget" else "completed"
                self.finish("runaway", self.run_id, reason)
        elif line.startswith("botscript stop "):
            self.emit("Stopping 1 script runs")
            if self.failure != "drift":
                self.movement = {name: value for name, value in self.movement.items()
                                 if name in self.console_owned and self.failure != "overlap"}
            self.finish("patrol", self.run_id, "Script stopped or time limit reached")
        elif line == f"bot {BOT} move right":
            self.movement[BOT] = "right"
            self.console_owned.add(BOT)
            self.emit("movement set to right")
        elif line == f"bot {GROUP} stop":
            self.movement.clear()
            self.emit("accepted 2, failed 0")
        elif line == f"bot {GROUP} look 0 0":
            self.emit("accepted 2, failed 0")
        elif line == f"bot {BUDDY} despawn":
            self.emit(f"{BUDDY}: despawn requested")
        elif line == f"botgroup delete {GROUP}":
            self.emit("group deleted; Bots retained")
        else:
            raise AssertionError("unexpected command: " + line)


if __name__ == "__main__":
    unittest.main()
