"""Opt-in scripting fixture and console smoke for raknet_spike's disposable world.

Only configure_fixture installs this module, into the output's plugin copy.
SDK writes prepare a flat Survival arena; all script saves use the paste-form
path (including compilation/declaration validation), and runs use real commands.
"""

import json
import math
import os
import re
import shutil
import time
from pathlib import Path

BOT = "RakNetSpike"
BUDDY = "ScriptBuddy"
GROUP = "#script_probe"


def built_runner(root):
    from e2e_consumer import ConsumerError

    binary = root / "runner/.build/native/bin" / ("endbot-luau-runner.exe" if os.name == "nt" else "endbot-luau-runner")
    sidecar = binary.with_name("api.luau")
    if not binary.is_file() or not sidecar.is_file():
        raise ConsumerError("scripting runner not built; run 'node runner/build.mjs'")
    if sidecar.read_bytes() != (root / "runner/api.luau").read_bytes() or binary.stat().st_mtime < (root / "runner/main.cpp").stat().st_mtime:
        raise ConsumerError("scripting runner build is stale; run 'node runner/build.mjs'")
    return binary


def enable_config(configfile, runner):
    import tomlkit

    document = tomlkit.parse(configfile.read_text(encoding="utf-8"))
    document["scripting"] = {"enabled": True, "runner": str(runner), "interrupt-budget": 10000}
    configfile.write_text(tomlkit.dumps(document), encoding="utf-8")


def configure_fixture(root: Path, output: Path, environment: dict):
    # mining_probe.configure_fixture has already created this isolated source tree.
    source = output / "mining-plugin"
    shutil.copy2(root / "scripts/scripting_probe.py", source / "_endbot_scripting_probe.py")
    with (source / "endstone_endbot/plugin.py").open("a", encoding="utf-8") as stream:
        stream.write(
            "\nfrom _endbot_scripting_probe import install_probe as install_scripting_probe\n"
            "install_scripting_probe(EndbotPlugin)\n"
        )
    environment["ENDBOT_SCRIPTING_PROBE"] = str(output)


def submit_save(plugin, name, source):
    from endbot_core.script_forms import ScriptGuard

    # Exactly the capability the paste form supplies, sent over the endbotd link like a form
    # reply; endbotd's ordered worker runs ScriptService's inspect/check/save branch.
    plugin._submit_bot(plugin.server.command_sender, ["save", name, source], command="botscript",
                       group_guard=ScriptGuard(edit=True))
    return True


def scripting_state(output: Path) -> dict:
    # The plugin replaces this file atomically every tick; on Windows a read that
    # meets the replace fails or sees nothing, so retry briefly before giving up.
    for _ in range(20):
        try:
            return json.loads((output / "scripting-state.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            time.sleep(0.05)
    return {}


def submit_fixture(output: Path, wait, data: dict) -> None:
    temporary = output / "scripting-request.tmp"
    temporary.write_text(json.dumps(data), encoding="utf-8")
    temporary.replace(output / "scripting-request.json")
    wait("scripting fixture " + data["id"], lambda: scripting_state(output).get("id") == data["id"], 15)


def save_script(output: Path, wait, log, name: str, source: str) -> None:
    offset = len(log())
    submit_fixture(output, wait, {"id": "save-" + name, "action": "save", "name": name, "source": source})
    wait("validated script save", lambda: f"Script {name} saved" in log()[offset:], 15)


def start_script_run(console, wait, log, name: str, suffix: str = ""):
    offset = len(log())
    console(f"botscript run {name}{suffix}")
    pattern = re.compile(rf"Script {name} started: ([0-9a-f-]+)")
    wait("script start " + name, lambda: pattern.search(log()[offset:]), 15)
    return pattern.search(log()[offset:])[1], offset


def install_probe(plugin_class):
    from endbot_core.script_forms import TEMPLATES
    from endstone.level import Location

    original_enable = plugin_class.on_enable

    def enable(self):
        original_enable(self)
        base = Path(os.environ["ENDBOT_SCRIPTING_PROBE"])
        seen, sequence, arena = None, 0, None
        mining_cells, mining_dimension = [], None

        def sample():
            nonlocal seen, sequence, arena, mining_cells, mining_dimension
            try:
                data = json.loads((base / "scripting-request.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return
            if data["id"] != seen:
                if data["action"] == "setup":
                    players = [self.server.get_player(name) for name in (BOT, BUDDY)]
                    if any(player is None for player in players):
                        return
                    location = players[0].location
                    dimension = location.dimension
                    x, z = math.floor(location.x), math.floor(location.z)
                    for dx in range(-8, 25):
                        for dz in range(-8, 41):
                            dimension.get_block_at(x + dx, 199, z + dz).set_type("minecraft:stone")
                            for y in range(200, 204):
                                dimension.get_block_at(x + dx, y, z + dz).set_type("minecraft:air")
                    arena = [x + 0.5, 200, z + 0.5]
                    for index, player in enumerate(players):
                        # Start away from the requested row so a no-op script cannot pass.
                        player.teleport(Location(dimension, arena[0] + 6 + index * 3, 200, arena[2] + 4, 0, 0))
                elif data["action"] == "mining":
                    player = self.server.get_player(BOT)
                    if player is None or arena is None:
                        return
                    mining_dimension = player.location.dimension
                    mining_cells = [[math.floor(arena[0]) + spot * 4, 201, math.floor(arena[2]) + 2 + block]
                                    for spot in range(2) for block in range(2)]
                    for cell in mining_cells:
                        mining_dimension.get_block_at(*cell).set_type("minecraft:dirt")
                    player.inventory.clear()
                    player.inventory.held_item_slot = 0
                elif data["action"] == "save":
                    if not submit_save(self, data["name"], data["source"]):
                        raise RuntimeError("scripting probe save submission rejected")
                else:
                    raise ValueError("unknown scripting fixture action")
                seen = data["id"]
            sequence += 1
            positions = {}
            for name in (BOT, BUDDY):
                player = self.server.get_player(name)
                if player is not None:
                    location = player.location
                    positions[name] = [location.x, location.y, location.z]
            state = {"id": seen, "sequence": sequence, "origin": arena, "positions": positions,
                     "row_template": TEMPLATES["Group row"], "mine_template": TEMPLATES["Mine at spots"],
                     "mining_blocks": [{"position": cell, "type": mining_dimension.get_block_at(*cell).type}
                                       for cell in mining_cells]}
            temporary = base / "scripting-state.tmp"
            temporary.write_text(json.dumps(state), encoding="utf-8")
            try:
                temporary.replace(base / "scripting-state.json")
            except PermissionError:
                pass

        self.server.scheduler.run_task(self, sample, delay=1, period=1)

    plugin_class.on_enable = enable


WAITS_SCRIPT = """params {}
local b = bot("BOT")
local function show(label, r)
    log(label .. ": " .. tostring(r.ok) .. " " .. tostring(r.code) .. " " .. tostring(r.reason))
end
show("online", b:waitOnline{timeout = 20})
show("move", b:move("forward", {ticks = 20}).job:wait{timeout = 200})
show("jump", b:jump({interval = 5, ticks = 20}).job:wait{timeout = 200})
local held = b:jump({mode = "continuous"}).job
b:jump({mode = "stop"})
show("stop", held:wait{timeout = 40})
local first = b:move("left").job
b:move("right")
show("replaced", first:wait{timeout = 40})
b:move("stop")
local here = b:status().data.position
show("tp", b:tp(here.x, here.y, here.z + 2).job:wait{timeout = 200})
group("GROUP"):waitOnline{timeout = 400}
show("group", group("GROUP"):move("backward", {ticks = 10}).job:wait{timeout = 200})
show("timeout", b:move("forward", {ticks = 400}).job:wait{timeout = 5})
b:stop()
""".replace("BOT", BOT).replace("GROUP", GROUP)
WAITS_EXPECTED = (
    "online: true ok online", "move: true ok duration elapsed", "jump: true ok duration elapsed",
    "stop: true ok stopped", "replaced: true ok replaced", "tp: true ok arrived",
    "group: true ok duration elapsed", "timeout: false timeout timeout",
)


def distance(a, b):
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def exercise_scripting(output, console, request, wait, log, report):
    from e2e_consumer import ConsumerError

    results = report["scripting"] = {
        "status": "running", "position_source": "BDS SDK player.location",
        "disabled_default": "unit test: default ScriptConfig rejects every botscript entry without starting a runner",
    }

    def state():
        return scripting_state(output)

    def fixture(data):
        submit_fixture(output, wait, data)

    def command(line, *expected):
        offset = len(log())
        console(line)
        wait(line, lambda: all(text in log()[offset:] for text in expected), 15)
        return log()[offset:]

    def save(name, source):
        save_script(output, wait, log, name, source)

    def start(name, suffix):
        return start_script_run(console, wait, log, name, suffix)

    def ended(name, run_id, offset, reason):
        wait("script end " + name, lambda: (
            f"{name} ended: {reason}" in log()[offset:]
            and f"Endbot script {run_id}: input cleanup completed" in log()[offset:]
        ), 15)
        return log()[offset:]

    def positions():
        current = state()
        return current.get("positions", {})

    def fresh_samples(seconds):
        # Poll the per-tick state file directly: the generic wait helper sleeps a
        # whole second between checks, too coarse for a ~1 second window.
        samples, last = [], state().get("sequence", 0)
        start = time.monotonic()
        while time.monotonic() - start < seconds or len(samples) < 3:
            if time.monotonic() - start > seconds + 10:
                break
            current = state()
            if current.get("sequence", 0) > last:
                samples.append(current)
                last = current["sequence"]
                time.sleep(0.15)
            else:
                time.sleep(0.02)
        if len(samples) < 3 or any(set(sample["positions"]) != {BOT, BUDDY} for sample in samples):
            raise ConsumerError("missing authoritative scripting position samples")
        return samples

    try:
        command(f"bot {BUDDY} spawn", f"{BUDDY}: created and connecting")
        wait("ScriptBuddy online", lambda: request("status", name=BUDDY)["connectionState"] == "online", 60)
        command(f"botgroup create {GROUP}", f"{GROUP}: group created")
        for name in (BOT, BUDDY):
            command(f"botgroup add {GROUP} {name}", f"{GROUP}: add: {name}")
        fixture({"id": "arena", "action": "setup"})
        origin = state()["origin"]
        save("line", state()["row_template"])
        save("patrol", '''local args = params {param.group "group"}
while true do
    local r = group(args.group):move("forward")
    if not r.ok then error(r.reason or "patrol move rejected") end
    log("patrol moving")
    wait(200)
end
''')
        save("runaway", "params {}\nwhile true do end\n")
        # BOT-024 2a: the basic set may be declared and changes nothing; a capability that does not exist yet is an
        # inspection error naming it, so the save is refused.
        save("declares", 'requires { "act.bot", "wait" }\nparams {}\nwait(1)\nlog("declared")\n')
        offset = len(log())
        submit_fixture(output, wait, {"id": "save-observes", "action": "save", "name": "observes",
                                      "source": 'requires { "observe.chat" }\nparams {}\n'})
        wait("unknown capability refused",
             lambda: "Unknown capability in requires: observe.chat" in log()[offset:], 15)
        if "Script observes saved" in log()[offset:]:
            raise ConsumerError("a script declaring an unknown capability was saved")
        declared_run, declared_offset = start("declares", "")
        results["requires"] = {"run_id": declared_run,
                               "output": ended("declares", declared_run, declared_offset, "completed")}
        results["list_output"] = command("botscript list", "line", "patrol", "runaway")
        results["idle_runs_output"] = command("botscript runs", "No active script runs")
        suffix = f' {GROUP} "{origin[0]} {origin[1]} {origin[2]}" 3'
        before_row = positions()
        run_id, offset = start("line", suffix)
        results["row"] = {"run_id": run_id, "command": "botscript run line" + suffix, "before": before_row}
        results["row"]["output"] = ended("line", run_id, offset, "completed")
        expected = {BOT: origin, BUDDY: [origin[0] + 3, origin[1], origin[2]]}
        wait("BDS group row offsets", lambda: all(
            name in positions() and distance(positions()[name], target) <= 0.35 for name, target in expected.items()
        ), 15)
        results["row"].update(expected=expected, observed=positions(), tolerance=0.35, outcome="passed")

        # Run twice: first prove both inputs cease; then replace one channel while
        # the run is waiting and prove cleanup preserves that console input.
        for overlap in (False, True):
            label = "overlap_cleanup" if overlap else "stop_patrol"
            command(f"bot {GROUP} look 0 0", "accepted 2, failed 0")
            baseline = positions()
            run_id, offset = start("patrol", f" {GROUP}")
            results[label] = {"run_id": run_id, "stable_tolerance": 0.15, "sample_seconds": 1.2,
                              "console_minimum_distance": 0.5}
            wait("patrol first wait", lambda offset=offset: "patrol moving" in log()[offset:], 15)
            wait("both BDS Bots moving", lambda baseline=baseline: all(
                name in positions() and distance(positions()[name], baseline[name]) > 0.5 for name in (BOT, BUDDY)
            ), 10)
            results[label]["runs_output"] = command("botscript runs", run_id, "patrol")
            if overlap:
                command(f"bot {BOT} move right", "movement set to right")
            # Exercise both supported selectors with independent runs.
            command(f"botscript stop {run_id if overlap else 'patrol'}", "Stopping 1 script runs")
            results[label]["output"] = ended("patrol", run_id, offset, "Script stopped or time limit reached")
            time.sleep(0.4)  # Allow normal release/physics to settle before the bounded sample window.
            samples = fresh_samples(1.2)
            results[label]["samples"] = samples
            stable_names = (BUDDY,) if overlap else (BOT, BUDDY)
            if any(max(distance(sample["positions"][name], samples[0]["positions"][name]) for sample in samples) > 0.15
                   for name in stable_names):
                raise ConsumerError("script continuing input survived cleanup")
            if overlap and distance(samples[-1]["positions"][BOT], samples[0]["positions"][BOT]) < 0.5:
                raise ConsumerError("script cleanup cancelled the later console movement")
            # An operation after termination must also continue normally.
            command(f"bot {BOT} move right", "movement set to right")
            later = fresh_samples(0.8)
            results[label]["post_stop_console_samples"] = later
            if distance(later[-1]["positions"][BOT], later[0]["positions"][BOT]) < 0.5:
                raise ConsumerError("post-stop console movement was cancelled")
            command(f"bot {GROUP} stop", "accepted 2, failed 0")
            results[label]["outcome"] = "passed"

        fixture({"id": "mining-arena", "action": "mining"})
        before = state()["mining_blocks"]
        if len(before) != 4 or any(block["type"] != "minecraft:dirt" for block in before):
            raise ConsumerError("mining fixture did not place four BDS dirt blocks")
        save("mine", state()["mine_template"])
        suffix = f' {BOT} "{origin[0]} {origin[1]} {origin[2]}" 2 4 2'
        run_id, offset = start("mine", suffix)
        results["mine"] = {"run_id": run_id, "before": before, "command": "botscript run mine" + suffix}
        results["mine"]["output"] = ended("mine", run_id, offset, "completed")
        for spot in (1, 2):
            if f"Endbot script {run_id}: spot {spot}: block count reached" not in results["mine"]["output"]:
                raise ConsumerError("mine template did not log block count reached for each spot")
        expected_cells = [block["position"] for block in before]
        wait("BDS blocks broken at both spots", lambda: (
            [block["position"] for block in state().get("mining_blocks", [])] == expected_cells
            and all(block["type"] == "minecraft:air" for block in state()["mining_blocks"])
        ), 15)
        results["mine"].update(after=state()["mining_blocks"], outcome="passed")

        # BOT-016: every continuing operation returns a job; its wait ends with the runtime's reason.
        save("waits", WAITS_SCRIPT)
        run_id, offset = start("waits", "")
        results["waits"] = {"run_id": run_id, "expected": list(WAITS_EXPECTED)}
        results["waits"]["output"] = ended("waits", run_id, offset, "completed")
        missing = [line for line in WAITS_EXPECTED
                   if f"Endbot script {run_id}: {line}" not in results["waits"]["output"]]
        if missing:
            raise ConsumerError(f"job waits did not report: {missing}")
        results["waits"]["outcome"] = "passed"

        run_id, offset = start("runaway", "")
        results["runaway"] = {"run_id": run_id}
        results["runaway"]["output"] = ended("runaway", run_id, offset, "Script runner failed:")
        if "execution budget exceeded" not in results["runaway"]["output"]:
            raise ConsumerError("runaway did not terminate by VM execution budget")
        results["runaway"]["outcome"] = "passed"
        results["final_runs_output"] = command("botscript runs", "No active script runs")
        command(f"bot {BUDDY} despawn", f"{BUDDY}: despawn requested")
        command(f"botgroup delete {GROUP}", "group deleted; Bots retained")
        results["status"] = "passed"
    except Exception as error:
        results.update(status="failed", error=str(error))
        raise
    finally:
        # The base smoke later restarts BDS for its auth-disabled check. Do not
        # replay the last save when this disposable fixture is enabled again.
        (output / "scripting-request.json").unlink(missing_ok=True)
        (output / "scripting-results.json").write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
