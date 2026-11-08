"""Opt-in live check of watches and triggers for raknet_spike --events (BOT-022 steps 4 to 6).

Never installed in the product. Bots stand in for a human (EVENTS.md S9). The
probe changes blocks and drops the plugin's link on request; outcomes are read
from the scripts' own log lines, which pass through endbotd like any run's.
"""

import json
import os
import time
from pathlib import Path

BOT = "RakNetSpike"


def install_probe(plugin_class):
    original_enable = plugin_class.on_enable

    def enable(self):
        original_enable(self)
        base = Path(os.environ["ENDBOT_EVENTS_PROBE"])
        self._events_probe = None

        def sample():
            try:
                request = json.loads((base / "events-request.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return
            if self._events_probe == request["id"]:
                return
            self._events_probe = request["id"]
            player = self.server.get_player(BOT)
            if request["action"] == "fill" and player is not None:
                dimension = player.location.dimension
                (x0, y0, z0), (x1, y1, z1) = request["from"], request["to"]
                for x in range(x0, x1 + 1):
                    for y in range(y0, y1 + 1):
                        for z in range(z0, z1 + 1):
                            dimension.get_block_at(x, y, z).set_type(request["block"], False)
            elif request["action"] == "drop-link":
                live = self._link._live  # evidence only: the same close a lost socket causes
                if live is not None:
                    live.close("events probe dropped the link")
            temporary = base / "events-state.tmp"
            temporary.write_text(json.dumps({"id": request["id"]}), encoding="utf-8")
            temporary.replace(base / "events-state.json")

        self.server.scheduler.run_task(self, sample, delay=1, period=1)

    plugin_class.on_enable = enable


def configure_fixture(root: Path, output: Path, environment: dict):
    import shutil

    source = output / "mining-plugin"
    shutil.copy2(root / "scripts/events_probe.py", source / "_endbot_events_probe.py")
    with (source / "endstone_endbot/plugin.py").open("a", encoding="utf-8") as stream:
        stream.write("\nfrom _endbot_events_probe import install_probe as install_events_probe\n"
                     "install_events_probe(EndbotPlugin)\n")
    environment["ENDBOT_EVENTS_PROBE"] = str(output)


def _request(output: Path, wait, data: dict) -> None:
    temporary = output / "events-request.tmp"
    temporary.write_text(json.dumps(data), encoding="utf-8")
    temporary.replace(output / "events-request.json")

    def done():
        try:
            return json.loads((output / "events-state.json").read_text(encoding="utf-8")).get("id") == data["id"]
        except (OSError, ValueError):
            return False

    wait(f"events probe {data['id']}", done, 15)


WATCH_SOURCE = """params {{}}
local w = watch("block.change", {{area = area.cell({x}, {y}, {z})}})
log("watching")
local r = w:wait{{timeout = 600}}
local d = r.data or {{}}
log("event " .. tostring(r.ok) .. " " .. r.code .. " " .. tostring(d.from) .. ">" .. tostring(d.to) .. " "
    .. tostring(d.cause) .. " " .. tostring(d.bot) .. " missed=" .. r.missed)
log("second " .. w:wait{{timeout = 10}}.code)
"""

MISS_SOURCE = """params {{}}
local w = watch("block.change", {{area = area.box({x0}, {y}, {z0}, {x1}, {y}, {z1})}})
log("watching")
wait(80)
local r = w:wait{{timeout = 10}}
log("miss " .. tostring(r.ok) .. " " .. r.missed)
"""

GAP_SOURCE = """params {{}}
local w = watch("block.change", {{area = area.cell({x}, {y}, {z})}})
log("watching")
local r = w:wait{{timeout = 1200}}
log("gap " .. tostring(r.ok) .. " " .. r.code .. " " .. r.reason)
r = w:wait{{timeout = 1200}}
log("after " .. tostring(r.ok) .. " " .. tostring((r.data or {{}}).to))
"""

TOO_LARGE_SOURCE = """params {{}}
watch("block.change", {{area = area.box({x}, {y}, {z}, {x2}, {y2}, {z2})}})
log("unexpected")
"""


def exercise_events(output: Path, console, control, wait, log, report) -> dict:
    """Watches end to end: a Bot's break, a buffer overflow, a link gap and a refused area."""
    from mining_probe import mining_state, setup_mining_fixture
    from scripting_probe import save_script, start_script_run

    results = report.setdefault("events", {})

    def run(name, source, *expected, timeout=30):
        save_script(output, wait, log, name, source)
        run_id, offset = start_script_run(console, wait, log, name)
        return run_id, offset

    def seen(text, offset, timeout=30):
        wait(f"script log {text!r}", lambda: text in log()[offset:], timeout)

    # 1. A Bot breaks the watched block: delivered with its cause and the Bot's name, then the watch times out.
    setup_mining_fixture(output, console, control, wait, "events-watch", "minecraft:dirt")
    x, y, z = mining_state(output)["target"]
    _, offset = run("eventwatch", WATCH_SOURCE.format(x=x, y=y, z=z))
    seen("watching", offset)
    console(f"bot {BOT} destroy")
    seen(f"event true ok minecraft:dirt>minecraft:air player-break {BOT} missed=0", offset)
    seen("second timeout", offset)
    results["bot_break"] = {"cell": [x, y, z], "cause": "player-break", "bot": BOT}

    # 2. 81 cells change at once while the run sleeps: 64 are kept and 17 counted as missed.
    _, offset = run("eventmiss", MISS_SOURCE.format(x0=x - 4, x1=x + 4, z0=z - 4, z1=z + 4, y=y + 4))
    seen("watching", offset)
    _request(output, wait, {"id": "fill-miss", "action": "fill", "from": [x - 4, y + 4, z - 4],
                            "to": [x + 4, y + 4, z + 4], "block": "minecraft:stone"})
    seen("miss true 17", offset)
    results["missed"] = {"cells": 81, "kept": 64, "missed": 17}

    # 3. The plugin's link drops: the waiting run sees the gap once, then the next change after the relink.
    cell = [x + 2, y + 2, z]
    _, offset = run("eventgap", GAP_SOURCE.format(x=cell[0], y=cell[1], z=cell[2]))
    seen("watching", offset)
    linked = log().count("Endbot linked to endbotd")
    _request(output, wait, {"id": "drop-link", "action": "drop-link"})
    wait("plugin relinked to endbotd", lambda: log().count("Endbot linked to endbotd") > linked, 60)
    seen("gap false unavailable source restarted", offset)
    time.sleep(1)
    _request(output, wait, {"id": "fill-gap", "action": "fill", "from": cell, "to": cell,
                            "block": "minecraft:gold_block"})
    seen("after true minecraft:gold_block", offset)
    results["gap"] = {"reason": "source restarted", "delivered_after_relink": True}

    # 4. An area over 512 blocks is refused with the limit named, before anything is watched.
    _, offset = run("eventlarge", TOO_LARGE_SOURCE.format(x=x, y=y, z=z, x2=x + 8, y2=y + 8, z2=z + 8))
    seen("may hold at most 512 blocks", offset)
    results["too_large"] = {"refused": True}
    if "unexpected" in log()[offset:]:
        raise RuntimeError("a watch over 512 blocks was accepted")
    # Leave the Bot's surroundings as the later smoke stages expect them.
    _request(output, wait, {"id": "clear-miss", "action": "fill", "from": [x - 4, y + 4, z - 4],
                            "to": [x + 4, y + 4, z + 4], "block": "minecraft:air"})
    _request(output, wait, {"id": "clear-gap", "action": "fill", "from": cell, "to": cell, "block": "minecraft:air"})
    return results


TRIGGER_SOURCE = """local a = params { param.event("event") }
local e = a.event
if e == nil then log("manual run") return end
log("triggered " .. tostring(e.to) .. " " .. tostring(e.cause) .. " missed=" .. tostring(e.missed))
"""


def exercise_triggers(output: Path, console, control, wait, log, report, kill_endbotd, restart_endbotd) -> dict:
    """Triggers end to end with console-owned triggers (Bots cannot be controllers)."""
    from mining_probe import mining_state, setup_mining_fixture
    from scripting_probe import save_script

    results = report.setdefault("triggers", {})

    def seen(text, offset, timeout=30):
        wait(f"log {text!r}", lambda: text in log()[offset:], timeout)

    def command(line, expected, timeout=15):
        offset = len(log())
        console(line)
        seen(expected, offset, timeout)
        return offset

    save_script(output, wait, log, "guardtrig", TRIGGER_SOURCE)

    # 1. A Bot breaks the watched block: the trigger starts the script with the occurrence.
    setup_mining_fixture(output, console, control, wait, "events-trigger", "minecraft:dirt")
    x, y, z = mining_state(output)["target"]
    # Logged commands answer the console through the endbotd log line; show and list reply directly.
    command(f'botscript trigger add guardtrig block.change area="{x} {y} {z}" to=air',
            "Trigger guardtrig of console armed for block.change")
    offset = len(log())
    console(f"bot {BOT} destroy")
    seen("triggered minecraft:air player-break missed=0", offset)
    results["start"] = {"cell": [x, y, z], "cause": "player-break"}

    # 2. Restart endbotd: the trigger comes back armed with its stats, and starts again.
    linked = log().count("Endbot linked to endbotd")
    kill_endbotd()
    restart_endbotd()
    wait("plugin relinked to endbotd", lambda: log().count("Endbot linked to endbotd") > linked, 60)
    command("botscript trigger show guardtrig", "armed")
    offset = len(log())
    _request(output, wait, {"id": "trigger-again", "action": "fill", "from": [x, y, z], "to": [x, y, z],
                            "block": "minecraft:stone"})
    time.sleep(1)
    _request(output, wait, {"id": "trigger-again-air", "action": "fill", "from": [x, y, z], "to": [x, y, z],
                            "block": "minecraft:air"})
    seen("triggered minecraft:air poll", offset)
    results["restart"] = {"armed_after_restart": True, "started_again": True}

    # 3. A trigger that fires faster than its start cap is disabled and says why.
    command("botscript trigger set guardtrig cooldown=0 startsPerMinute=2 concurrency=parallel",
            "Trigger guardtrig of console:")
    offset = len(log())
    for index in range(4):
        _request(output, wait, {"id": f"cap-{index}-on", "action": "fill", "from": [x, y, z], "to": [x, y, z],
                                "block": "minecraft:stone"})
        time.sleep(0.4)
        _request(output, wait, {"id": f"cap-{index}-off", "action": "fill", "from": [x, y, z], "to": [x, y, z],
                                "block": "minecraft:air"})
        time.sleep(0.4)
    seen("disabled: rate limit", offset)
    command("botscript trigger show guardtrig", "disabled")
    results["start_cap"] = {"disabled": "rate limit"}

    # 4. Enabling again re-arms it; removing it ends it.
    command("botscript trigger enable guardtrig", "Trigger guardtrig of console armed")
    command("botscript trigger remove guardtrig", "Trigger guardtrig of console removed")
    command("botscript trigger list", "No triggers")
    results["enable_remove"] = True
    return results
