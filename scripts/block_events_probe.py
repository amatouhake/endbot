"""Opt-in block change coverage spike for raknet_spike --block-events (BOT-022 step 1).

Never installed in the product. Measures, on the locked Endstone/BDS pair, which
block changes raise an Endstone event, what a per-tick read of a watched area
sees, how much such a read costs, what reading an unloaded chunk returns, and
which presence and chat events exist. Bots stand in for a human (EVENTS.md S9).
Everything happens in the disposable smoke world; the evidence is written to
``block-events.jsonl`` and summarised in the spike report.
"""

import itertools
import json
import math
import os
import statistics
import time
from functools import partial
from pathlib import Path

BOT = "RakNetSpike"
END_TICK = 1500
SHORT_TICKS = (1, 2, 4, 8)
# Scene offsets (dx, dz) from the scene base, which sits beside the mining platform.
SHORT_CELLS = {1: (1, 1), 2: (1, 3), 4: (1, 5), 8: (1, 7)}
PISTON = (4, 1)
DOOR = (4, 5)
WATER_BASIN = ((7, 0), (10, 4))
LAVA_BASIN = ((7, 6), (10, 10))
SAND = (1, 10)
FIRE = (4, 10)
LEAVES = (4, 15)
CROP = (1, 15)
TNT = (8, 18)
SCENE = ((0, 0), (10, 20))
EXPERIMENTS = {
    "short": [(dx, 200, dz) for dx, dz in SHORT_CELLS.values()],
    "piston": [(PISTON[0] + i, 200, PISTON[1]) for i in range(-1, 3)],
    "door": [(DOOR[0], 200, DOOR[1]), (DOOR[0], 201, DOOR[1]), (DOOR[0] - 1, 200, DOOR[1])],
    "water": [(dx, 200, dz) for dx in range(8, 10) for dz in range(1, 4)],
    "water-near": [(dx, 200, dz) for dx in range(-10, -7) for dz in range(11, 14)],
    "lava": [(dx, 200, dz) for dx in range(8, 10) for dz in range(7, 10)],
    "sand": [(SAND[0], y, SAND[1]) for y in range(200, 204)],
    "fire": [(FIRE[0], y, FIRE[1] + dz) for y in (200, 201) for dz in (0, 1)],
    "leaves": [(LEAVES[0], 201, LEAVES[1])],
    "crop": [(CROP[0], 200, CROP[1])],
    "tnt": [(TNT[0] + dx, 200, TNT[1] + dz) for dx in (-1, 0, 1) for dz in (-1, 0, 1)],
}


def install_probe(plugin_class):
    from endstone.event import (
        ActorExplodeEvent,
        BlockBreakEvent,
        BlockExplodeEvent,
        BlockFormEvent,
        BlockFromToEvent,
        BlockGrowEvent,
        BlockPistonExtendEvent,
        BlockPistonRetractEvent,
        BlockPlaceEvent,
        EventPriority,
        LeavesDecayEvent,
        PlayerChatEvent,
        PlayerCommandEvent,
        PlayerDeathEvent,
        PlayerJoinEvent,
        PlayerQuitEvent,
        event_handler,
    )

    original_enable = plugin_class.on_enable

    def state(self):
        return getattr(self, "_block_probe", None)

    def record(self, entry):
        probe = state(self)
        if probe is not None:
            entry["tick"] = probe["tick"]
            probe["log"].append(entry)

    def block_entry(self, name, event, block, **extra):
        entry = {"event": name, "at": [block.x, block.y, block.z], "type": block.type,
                 "cancelled": bool(getattr(event, "is_cancelled", False)), **extra}
        record(self, entry)
        probe = state(self)
        if probe is not None:
            # Next-tick confirm (EVENTS.md E3): what the world holds once the action settled.
            probe["confirm"].append((probe["tick"] + 1, name, (block.x, block.y, block.z), block.dimension.name))

    def handler(event_class, name, extract):
        @event_handler(priority=EventPriority.MONITOR)
        def on_event(self, event):
            if state(self) is None:
                return
            try:
                extract(self, event)
            except Exception as error:  # noqa: BLE001 - evidence only; never break the server thread
                record(self, {"event": name, "error": repr(error)})
        on_event.__annotations__ = {"event": event_class}
        on_event.__name__ = f"on_block_probe_{name}"
        return on_event

    def player_name(event):
        player = getattr(event, "player", None)
        return getattr(player, "name", None)

    handlers = {
        "break": (BlockBreakEvent, lambda self, e: block_entry(self, "break", e, e.block, player=player_name(e))),
        "place": (BlockPlaceEvent, lambda self, e: block_entry(
            self, "place", e, e.block, player=player_name(e), placed=e.block_placed_state.type)),
        "piston_extend": (BlockPistonExtendEvent, lambda self, e: block_entry(
            self, "piston_extend", e, e.block, direction=str(e.direction))),
        "piston_retract": (BlockPistonRetractEvent, lambda self, e: block_entry(
            self, "piston_retract", e, e.block, direction=str(e.direction))),
        "from_to": (BlockFromToEvent, lambda self, e: block_entry(
            self, "from_to", e, e.to_block, source=[e.block.x, e.block.y, e.block.z])),
        "grow": (BlockGrowEvent, lambda self, e: block_entry(self, "grow", e, e.block, new=e.new_state.type)),
        "form": (BlockFormEvent, lambda self, e: block_entry(self, "form", e, e.block, new=e.new_state.type)),
        "leaves_decay": (LeavesDecayEvent, lambda self, e: block_entry(self, "leaves_decay", e, e.block)),
        "block_explode": (BlockExplodeEvent, lambda self, e: [
            block_entry(self, "block_explode", e, b) for b in e.block_list]),
        "actor_explode": (ActorExplodeEvent, lambda self, e: [
            block_entry(self, "actor_explode", e, b, actor=e.actor.type) for b in e.block_list]),
        "chat": (PlayerChatEvent, lambda self, e: record(self, {"event": "chat", "player": player_name(e)})),
        "command": (PlayerCommandEvent, lambda self, e: record(
            self, {"event": "command", "player": player_name(e), "command": e.command.split(" ")[0]})),
        "death": (PlayerDeathEvent, lambda self, e: record(
            self, {"event": "death", "player": player_name(e), "message": getattr(e.death_message, "text", str(e.death_message))})),
        "join": (PlayerJoinEvent, lambda self, e: record(self, {"event": "join", "player": player_name(e)})),
        "quit": (PlayerQuitEvent, lambda self, e: record(self, {"event": "quit", "player": player_name(e)})),
    }
    skipped = set(filter(None, os.environ.get("ENDBOT_BLOCK_EVENTS_SKIP", "").split(",")))
    for name, (event_class, extract) in handlers.items():
        if name in skipped:
            continue  # Diagnostic: does listening itself change what the world does?
        setattr(plugin_class, f"on_block_probe_{name}", handler(event_class, name, extract))

    def enable(self):
        original_enable(self)
        base = Path(os.environ["ENDBOT_BLOCK_EVENTS_PROBE"])
        self._block_probe = None
        self._block_probe_request = None

        def flush(probe, final=False):
            if probe["log"]:
                with (base / "block-events.jsonl").open("a", encoding="utf-8") as stream:
                    for entry in probe["log"]:
                        stream.write(json.dumps(entry) + "\n")
                probe["log"].clear()
            temporary = base / "block-events-state.tmp"
            temporary.write_text(json.dumps({
                "id": probe["id"], "tick": probe["tick"], "done": final or probe["done"],
                "base": probe["base"], "measurements": probe["measurements"],
            }), encoding="utf-8")
            temporary.replace(base / "block-events-state.json")

        def read(dimension, cell):
            block = dimension.get_block_at(*cell)
            return block.data.runtime_id, block

        def watched_cells(origin, scene_base):
            x, z = origin
            bx, bz = scene_base
            cells = []
            (x0, z0), (x1, z1) = SCENE
            for dx in range(x0, x1 + 1):
                for dz in range(z0, z1 + 1):
                    for y in range(199, 205):
                        cells.append((bx + dx, y, bz + dz))
            for dx in range(-3, 4):  # the mining platform, where the Bot breaks and places
                for dz in range(-3, 8):
                    for y in range(200, 204):
                        cells.append((x + dx, y, z + dz))
            return cells

        def build_scene(dimension, bx, bz):
            (x0, z0), (x1, z1) = SCENE
            for dx in range(x0, x1 + 1):
                for dz in range(z0, z1 + 1):
                    dimension.get_block_at(bx + dx, 198, bz + dz).set_type("minecraft:stone", False)
                    dimension.get_block_at(bx + dx, 199, bz + dz).set_type("minecraft:stone", False)
                    for y in range(200, 207):
                        dimension.get_block_at(bx + dx, y, bz + dz).set_type("minecraft:air", False)
            for (ax, az), (cx, cz) in (WATER_BASIN, LAVA_BASIN):
                for dx in range(ax, cx + 1):
                    for dz in range(az, cz + 1):
                        if dx in (ax, cx) or dz in (az, cz):
                            dimension.get_block_at(bx + dx, 200, bz + dz).set_type("minecraft:stone", False)

        def put(dimension, cell, block_type, states=None, physics=True):
            block = dimension.get_block_at(*cell)
            data = self.server.create_block_data(block_type, states) if states else None
            if data is not None:
                block.set_data(data, physics)
            else:
                block.set_type(block_type, physics)

        def setblock(cell, block_type, states=None):
            suffix = ""
            if states:
                suffix = " [" + ",".join(f'"{key}"={json.dumps(value)}' for key, value in states.items()) + "]"
            line = f"setblock {cell[0]} {cell[1]} {cell[2]} {block_type}{suffix}"
            if not self.server.dispatch_command(self.server.command_sender, line):
                raise RuntimeError(f"command failed: {line}")

        def timeline(dimension, bx, bz):
            def at(dx, y, dz):
                return (bx + dx, y, bz + dz)

            steps = []
            if os.environ.get("ENDBOT_BLOCK_EVENTS_TICKINGAREA"):
                # Diagnostic: force the scene to tick, to tell missing scheduled ticks from missing events.
                steps.append((1, "tickingarea", lambda: self.server.dispatch_command(
                    self.server.command_sender, f"tickingarea add circle {bx} 200 {bz} 2 endbot_spike")))
            for ticks, (dx, dz) in SHORT_CELLS.items():
                for start in (20, 40 + ticks):  # two phases so a changed-and-restored cell is seen twice
                    cell = at(dx, 200, dz)
                    steps.append((start, "short", partial(put, dimension, cell, "minecraft:gold_block",
                                                          physics=False)))
                    steps.append((start + ticks, "short", partial(put, dimension, cell, "minecraft:air",
                                                                  physics=False)))
            px, pz = PISTON
            steps += [
                (60, "piston", lambda: setblock(at(px, 200, pz), "minecraft:sticky_piston", {"facing_direction": 5})),
                (61, "piston", lambda: setblock(at(px + 1, 200, pz), "minecraft:stone")),
                (70, "piston", lambda: setblock(at(px - 1, 200, pz), "minecraft:redstone_block")),
                (110, "piston", lambda: setblock(at(px - 1, 200, pz), "minecraft:air")),
            ]
            dx_, dz_ = DOOR
            steps += [
                (140, "door", lambda: put(dimension, at(dx_, 200, dz_), "minecraft:wooden_door",
                                          {"upper_block_bit": False, "direction": 0}, physics=False)),
                (140, "door", lambda: put(dimension, at(dx_, 201, dz_), "minecraft:wooden_door",
                                          {"upper_block_bit": True, "direction": 0}, physics=False)),
                (150, "door", lambda: setblock(at(dx_ - 1, 200, dz_), "minecraft:redstone_block")),
                (190, "door", lambda: setblock(at(dx_ - 1, 200, dz_), "minecraft:air")),
            ]
            steps += [
                (220, "water", lambda: setblock(at(8, 200, 1), "minecraft:water")),
                (300, "lava", lambda: setblock(at(8, 200, 7), "minecraft:lava")),
                # Diagnostic: water on the Bot's own platform, to tell an untouched area from no liquid ticks.
                (230, "water-near", lambda: setblock((bx - 9, 200, bz + 12), "minecraft:water")),
                (420, "sand", lambda: setblock(at(SAND[0], 203, SAND[1]), "minecraft:sand")),
            ]
            fx, fz = FIRE
            steps += [
                (460, "fire", lambda: put(dimension, at(fx, 200, fz), "minecraft:oak_planks", physics=False)),
                (460, "fire", lambda: put(dimension, at(fx, 200, fz + 1), "minecraft:oak_planks", physics=False)),
                (461, "fire", lambda: setblock(at(fx, 201, fz), "minecraft:fire")),
            ]
            lx, lz = LEAVES
            cx, cz = CROP
            steps += [
                (500, "crop", lambda: put(dimension, at(cx, 199, cz), "minecraft:farmland", physics=False)),
                (500, "crop", lambda: put(dimension, at(cx + 1, 199, cz), "minecraft:water", physics=False)),
                (501, "crop", lambda: put(dimension, at(cx, 200, cz), "minecraft:wheat", {"growth": 0},
                                          physics=False)),
                (502, "leaves", lambda: put(dimension, at(lx, 201, lz), "minecraft:oak_leaves",
                                            {"persistent_bit": False, "update_bit": True}, physics=False)),
                (505, "random", lambda: self.server.dispatch_command(self.server.command_sender,
                                                                     "gamerule randomtickspeed 300")),
            ]
            tx, tz = TNT
            for ddx in (-1, 0, 1):
                for ddz in (-1, 0, 1):
                    if ddx or ddz:
                        steps.append((540, "tnt", partial(put, dimension, at(tx + ddx, 200, tz + ddz),
                                                          "minecraft:dirt", physics=False)))
            steps += [
                (541, "tnt", lambda: put(dimension, at(tx, 200, tz), "minecraft:tnt", physics=False)),
                (545, "tnt", lambda: setblock(at(tx, 201, tz), "minecraft:redstone_block")),
                (END_TICK - 20, "random", lambda: self.server.dispatch_command(self.server.command_sender,
                                                                               "gamerule randomtickspeed 1")),
            ]
            return sorted(steps, key=lambda step: step[0])

        def measure(dimension, cells, origin):
            results = {}
            for count in (1000, 4096):
                sample = [cells[index % len(cells)] for index in range(count)]
                durations = []
                for _ in range(20):
                    started = time.perf_counter()
                    for cell in sample:
                        dimension.get_block_at(*cell).data.runtime_id  # noqa: B018 - the read is the cost
                    durations.append((time.perf_counter() - started) * 1000)
                results[f"read_{count}_ms"] = {"median": round(statistics.median(durations), 3),
                                               "max": round(max(durations), 3)}
            far = (origin[0] + 5000, 64, origin[1] + 5000)
            loaded = {(chunk.x, chunk.z) for chunk in dimension.loaded_chunks}
            entry = {"at": list(far), "chunk_loaded": (math.floor(far[0] / 16), math.floor(far[2] / 16)) in loaded}
            try:
                block = dimension.get_block_at(*far)
                entry.update(type=block.type, runtime_id=block.data.runtime_id)
            except Exception as error:  # noqa: BLE001 - the question is exactly what happens here
                entry["error"] = repr(error)
            results["unloaded"] = entry
            results["loaded_chunks"] = len(loaded)
            return results

        def tick():
            player = self.server.get_player(BOT)
            try:
                request = json.loads((base / "block-events-request.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                request = None
            probe = self._block_probe
            if request and (probe is None or probe["id"] != request["id"]) and player is not None:
                dimension = player.location.dimension
                x, z = math.floor(player.location.x), math.floor(player.location.z)
                origin = request.get("origin") or [x, z]
                bx, bz = origin[0] + 6, origin[1] - 6
                build_scene(dimension, bx, bz)
                cells = watched_cells(origin, (bx, bz))
                probe = self._block_probe = {
                    "id": request["id"], "tick": 0, "done": False, "base": [bx, 200, bz], "origin": origin,
                    "dimension": dimension, "cells": cells, "baseline": {}, "log": [], "confirm": [],
                    "steps": timeline(dimension, bx, bz) if request.get("scene", True) else [],
                    "measurements": measure(dimension, cells, origin) if request.get("scene", True) else {},
                    "end": END_TICK if request.get("scene", True) else None,
                }
                for cell in cells:
                    probe["baseline"][cell] = read(dimension, cell)[0]
                record(self, {"event": "start", "base": probe["base"], "origin": origin, "cells": len(cells)})
            if probe is None or probe["done"]:
                return
            probe["tick"] += 1
            dimension = probe["dimension"]
            while probe["steps"] and probe["steps"][0][0] <= probe["tick"]:
                _, label, action = probe["steps"].pop(0)
                try:
                    action()
                    record(self, {"event": "step", "label": label})
                except Exception as error:  # noqa: BLE001 - a failed step is evidence
                    record(self, {"event": "step", "label": label, "error": repr(error)})
            due = [item for item in probe["confirm"] if item[0] <= probe["tick"]]
            probe["confirm"] = [item for item in probe["confirm"] if item[0] > probe["tick"]]
            for _, name, cell, _dimension in due:
                block = dimension.get_block_at(*cell)
                record(self, {"event": "confirm", "of": name, "at": list(cell), "type": block.type})
            started = time.perf_counter()
            changed = []
            for cell in probe["cells"]:
                runtime_id, block = read(dimension, cell)
                if runtime_id != probe["baseline"][cell]:
                    probe["baseline"][cell] = runtime_id
                    changed.append({"at": list(cell), "type": block.type,
                                    "states": block.data.block_states})
            elapsed = (time.perf_counter() - started) * 1000
            probe.setdefault("poll_ms", []).append(elapsed)
            for change in changed:
                record(self, {"event": "poll", **change})
            if probe["end"] is not None and probe["tick"] >= probe["end"]:
                durations = probe.pop("poll_ms")
                probe["measurements"]["poll_pass_ms"] = {
                    "cells": len(probe["cells"]), "median": round(statistics.median(durations), 3),
                    "p99": round(sorted(durations)[int(len(durations) * 0.99) - 1], 3),
                    "max": round(max(durations), 3)}
                probe["done"] = True
                flush(probe, final=True)
            elif probe["tick"] % 20 == 0:
                flush(probe)

        def presence():
            # Presence and chat checks run after the scene: the probe keeps logging events only.
            try:
                request = json.loads((base / "block-events-presence.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return
            probe = self._block_probe
            if probe is None or probe.get("presence") == request["id"]:
                return
            probe["presence"] = request["id"]
            probe["done"] = False
            probe["end"] = None
            player = self.server.get_player(BOT)
            if player is not None and request.get("commands"):
                for line in request["commands"]:
                    record(self, {"event": "bot_command", "ok": bool(player.perform_command(line)),
                                  "command": line.split(" ")[0]})

        self.server.scheduler.run_task(self, tick, delay=1, period=1)
        self.server.scheduler.run_task(self, presence, delay=1, period=5)

    plugin_class.on_enable = enable


def configure_fixture(root: Path, output: Path, environment: dict):
    import shutil

    source = output / "mining-plugin"
    shutil.copy2(root / "scripts/block_events_probe.py", source / "_endbot_block_events_probe.py")
    with (source / "endstone_endbot/plugin.py").open("a", encoding="utf-8") as stream:
        stream.write(
            "\nfrom _endbot_block_events_probe import install_probe as install_block_events_probe\n"
            "install_block_events_probe(EndbotPlugin)\n"
        )
    environment["ENDBOT_BLOCK_EVENTS_PROBE"] = str(output)


def _entries(output: Path) -> list[dict]:
    try:
        lines = (output / "block-events.jsonl").read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return [json.loads(line) for line in lines if line.strip()]


def _state(output: Path) -> dict:
    for _ in range(20):
        try:
            return json.loads((output / "block-events-state.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            time.sleep(0.01)
    return {}


def _write(output: Path, name: str, data: dict) -> None:
    temporary = output / f"{name}.tmp"
    temporary.write_text(json.dumps(data), encoding="utf-8")
    temporary.replace(output / name)


def summarise(entries: list[dict], base: list[int], origin: list[int]) -> dict:
    """Per experiment: which events touched its cells, what the poll saw, and which poll changes no event explained."""
    bx, _, bz = base
    block_events = {"break", "place", "piston_extend", "piston_retract", "from_to", "grow", "form", "leaves_decay",
                    "block_explode", "actor_explode"}
    summary = {}
    for name, offsets in EXPERIMENTS.items():
        cells = {(bx + dx, y, bz + dz) for dx, y, dz in offsets}
        events = [entry for entry in entries if entry["event"] in block_events and tuple(entry["at"]) in cells]
        polls = [entry for entry in entries if entry["event"] == "poll" and tuple(entry["at"]) in cells]
        explained = 0
        for change in polls:
            if any(tuple(event["at"]) == tuple(change["at"]) and abs(event["tick"] - change["tick"]) <= 1
                   for event in events):
                explained += 1
        summary[name] = {
            "events": sorted({entry["event"] for entry in events}),
            "event_count": len(events),
            "poll_changes": len(polls),
            "poll_changes_with_event": explained,
            "types_seen": sorted({entry["type"] for entry in polls}),
        }
    short = {}
    for ticks, (dx, dz) in SHORT_CELLS.items():
        cell = (bx + dx, 200, bz + dz)
        changes = [entry for entry in entries if entry["event"] == "poll" and tuple(entry["at"]) == cell]
        visible = [later["tick"] - earlier["tick"] for earlier, later in itertools.pairwise(changes)
                   if earlier["type"] == "minecraft:gold_block" and later["type"] == "minecraft:air"]
        short[str(ticks)] = {"seen": len(visible), "visible_ticks": visible}
    summary["short_lived"] = short
    x, z = origin
    platform = {(x + dx, y, z + dz) for dx in range(-3, 4) for dz in range(-3, 8) for y in range(200, 204)}
    summary["bot"] = {
        "events": [{key: entry[key] for key in ("event", "tick", "at", "type", "cancelled") if key in entry}
                   | ({"player": entry["player"]} if "player" in entry else {})
                   for entry in entries if entry["event"] in {"break", "place"} and tuple(entry["at"]) in platform],
        "confirms": [entry for entry in entries if entry["event"] == "confirm" and tuple(entry["at"]) in platform],
        "poll_changes": [entry for entry in entries if entry["event"] == "poll" and tuple(entry["at"]) in platform],
    }
    summary["presence"] = [entry for entry in entries
                           if entry["event"] in {"chat", "command", "death", "join", "quit", "bot_command"}]
    summary["errors"] = [entry for entry in entries if "error" in entry]
    return summary


def exercise_block_events(output: Path, console, control, wait, report, log, spawn_again):
    """Run the scene, the Bot break and place, then presence; judge from BDS state, never from the product."""
    from mining_probe import mining_state, setup_mining_fixture

    _write(output, "block-events-request.json", {"id": "scene", "scene": True})
    wait("block events probe started", lambda: _state(output).get("id") == "scene", 20)
    state = _state(output)
    origin = [state["base"][0] - 6, state["base"][2] + 6]
    wait("block events scene finished", lambda: _state(output).get("done"), END_TICK / 20 + 60)
    scene = _state(output)
    # The Bot breaks a block and places one: real player actions on BDS (EVENTS.md S9).
    setup_mining_fixture(output, console, control, wait, "block-events-break", "minecraft:dirt")
    mined = mining_state(output)
    _write(output, "block-events-request.json", {"id": "bot", "scene": False, "origin": origin})
    wait("block events probe follows the Bot", lambda: _state(output).get("id") == "bot", 20)
    console(f"bot {BOT} destroy")
    wait("Bot broke the dirt", lambda: mining_state(output).get("block") == "minecraft:air", 20)
    time.sleep(1)
    from mining_probe import mining_request

    mining_request(output, {"id": "block-events-place", "block": "minecraft:stone",
                            "product_items": [["1", "minecraft:dirt", 4]]})
    wait("place fixture ready", lambda: mining_state(output).get("id") == "block-events-place", 10)
    time.sleep(0.5)
    console(f"bot {BOT} hotbar 1")
    time.sleep(0.3)
    console(f"bot {BOT} interact")
    time.sleep(2)
    # A Bot empties a water bucket: liquid placed by a player, then left to flow (BlockFromToEvent).
    mining_request(output, {"id": "block-events-bucket", "block": "minecraft:stone",
                            "product_items": [["1", "minecraft:water_bucket", 1]]})
    wait("bucket fixture ready", lambda: mining_state(output).get("id") == "block-events-bucket", 10)
    time.sleep(0.5)
    console(f"bot {BOT} hotbar 1")
    time.sleep(0.3)
    console(f"bot {BOT} interact")
    time.sleep(6)
    # Presence: a Bot's /tell and /me must not raise a chat event; death, quit and join are recorded.
    _write(output, "block-events-presence.json", {"id": "commands", "commands": [f"tell {BOT} probe", "me probe"]})
    time.sleep(1)
    console(f"kill {BOT}")
    time.sleep(2)
    spawn_again()
    time.sleep(2)
    entries = _entries(output)
    state = _state(output)
    report["block_events"] = {
        "base": state.get("base"), "origin": origin, "mining_target": mined.get("target"),
        "measurements": scene.get("measurements"),
        "summary": summarise(entries, state["base"], origin),
        "entries": len(entries),
    }
    return report["block_events"]
