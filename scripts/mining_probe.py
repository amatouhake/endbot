"""Opt-in fixture for raknet_spike --mining, on a new ignored disposable world.

Never shipped in the Endbot plugin. Only the smoke runner copies/installs this
observer into its isolated plugin source; it prepares blocks/tools via the SDK.
"""

import json
import math
import os
from pathlib import Path


def install_probe(plugin_class):
    from endstone.actor import Item
    from endstone.event import BlockBreakEvent, event_handler
    from endstone.inventory import ItemStack
    from endstone.level import Location

    original_enable = plugin_class.on_enable

    @event_handler
    def on_probe_break(self, event: BlockBreakEvent):
        probe = getattr(self, "_mining_probe", None)
        if not probe or event.player.name != "RakNetSpike":
            return
        if [event.block.x, event.block.y, event.block.z] not in probe["targets"]:
            return
        if probe["cancel"]:
            event.is_cancelled = True
        probe["events"].append({"player": event.player.name, "block": event.block.type,
                                "canceled": event.is_cancelled})

    def enable(self):
        original_enable(self)
        base = Path(os.environ["ENDBOT_MINING_PROBE"])
        self._mining_probe = None

        def sample():
            player = self.server.get_player("RakNetSpike")
            if player is None:
                return
            request = base / "mining-request.json"
            if request.exists():
                try:
                    data = json.loads(request.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    return  # Windows can briefly deny access during atomic replacement.
                if self._mining_probe is None or self._mining_probe["id"] != data["id"]:
                    dimension = player.location.dimension
                    x, z = math.floor(player.location.x), math.floor(player.location.z)
                    for actor in dimension.actors:
                        if isinstance(actor, Item):
                            actor.remove()
                    for dx in range(-3, 4):
                        for dz in range(-3, 8):
                            dimension.get_block_at(x + dx, 199, z + dz).set_type("minecraft:stone")
                            for y in range(200, 204):
                                dimension.get_block_at(x + dx, y, z + dz).set_type("minecraft:air")
                    target = [x, 201, z + 2]
                    targets = [[x, 201, z + 2 + index] for index in range(data.get("count", 1))]
                    for cell in targets:
                        dimension.get_block_at(*cell).set_type(data["block"])
                    player.inventory.clear()
                    if data.get("tool"):
                        player.inventory.set_item(0, ItemStack(data["tool"], 1))
                    player.inventory.held_item_slot = 0
                    player.teleport(Location(dimension, x + 0.5, 200, z + 0.5, 0, 0))
                    self._mining_probe = {"id": data["id"], "target": target, "cancel": data.get("cancel", False),
                                          "targets": targets, "events": [], "refill": data.get("refill", 0)}
                elif self._mining_probe["refill"] != data.get("refill", 0):
                    # Replace blocks without teleporting/restarting the Bot.
                    for cell in self._mining_probe["targets"]:
                        player.location.dimension.get_block_at(*cell).set_type(data["block"])
                    self._mining_probe["refill"] = data.get("refill", 0)
            if self._mining_probe is None:
                return
            probe = self._mining_probe
            item = player.inventory.get_item(0)
            state = {**probe, "block": player.location.dimension.get_block_at(*probe["target"]).type,
                     "blocks": [player.location.dimension.get_block_at(*cell).type for cell in probe["targets"]],
                     "damage": item.item_meta.damage if item else 0,
                     "position": [player.location.x, player.location.y, player.location.z],
                     "drops": [actor.item_stack.type.id for actor in player.location.dimension.actors
                               if isinstance(actor, Item)]}
            temporary = base / "mining-state.tmp"
            temporary.write_text(json.dumps(state), encoding="utf-8")
            try:
                temporary.replace(base / "mining-state.json")
            except PermissionError:
                pass  # Retry the latest observation next server tick.

        self.server.scheduler.run_task(self, sample, delay=1, period=1)

    plugin_class.on_probe_break = on_probe_break
    plugin_class.on_enable = enable


def configure_fixture(root: Path, output: Path, environment: dict):
    import shutil

    source = output / "mining-plugin"
    shutil.copytree(root / "plugin/endbot/src/endstone_endbot", source / "endstone_endbot")
    shutil.copy2(root / "scripts/mining_probe.py", source / "_endbot_mining_probe.py")
    with (source / "endstone_endbot/plugin.py").open("a", encoding="utf-8") as stream:
        stream.write("\nfrom _endbot_mining_probe import install_probe\ninstall_probe(EndbotPlugin)\n")
    environment["PYTHONPATH"] = os.pathsep.join([str(source), str(root / "cli/endbot/src")])
    environment["ENDBOT_MINING_PROBE"] = str(output)


def exercise_mining(output: Path, console, control, wait, report):
    import time

    def observed():
        try:
            return json.loads((output / "mining-state.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def request(data):
        temporary = output / "mining-request.tmp"
        temporary.write_text(json.dumps(data))
        temporary.replace(output / "mining-request.json")

    def setup(name, block, tool=None, cancel=False, count=1):
        control("mine", mode="stop")
        request({"id": name, "block": block, "tool": tool, "cancel": cancel, "count": count})
        wait("mining fixture setup", lambda: observed().get("id") == name, 10)
        time.sleep(0.3)
        console("bot RakNetSpike hotbar 1")
        time.sleep(0.15)

    for name, block, tool, minimum in (
        ("dirt-hand", "minecraft:dirt", None, 0.3),
        ("stone-pickaxe", "minecraft:stone", "minecraft:iron_pickaxe", 0.1),
        ("stone-hand", "minecraft:stone", None, 4.0),
    ):
        setup(name, block, tool)
        started = time.monotonic()
        console("bot RakNetSpike destroy")
        wait("BDS mined target", lambda: observed().get("block") == "minecraft:air", 20)
        elapsed = time.monotonic() - started
        state = observed()
        if elapsed < minimum or not state["events"] or state["events"][-1]["canceled"]:
            raise RuntimeError(f"invalid normal mining behavior: {name}: {state}")
        if tool and state["damage"] < 1:
            raise RuntimeError(f"tool durability did not change: {state}")
        if name == "stone-pickaxe" and "minecraft:cobblestone" not in state["drops"]:
            raise RuntimeError(f"stone did not drop cobblestone with pickaxe: {state}")
        if name == "stone-hand" and state["drops"]:
            raise RuntimeError(f"stone dropped loot with the wrong tool: {state}")
        wait("mining runtime confirmation", lambda: (control("status").get("mining") or {}).get("state") == "completed", 5)
        report.setdefault("mining", {})[name] = {"seconds": round(elapsed, 2), "damage": state["damage"], "drops": state["drops"]}
    setup("continuous", "minecraft:stone", "minecraft:iron_pickaxe", count=3)
    console("bot RakNetSpike destroy start")
    wait("three consecutive mined blocks", lambda: observed().get("blocks") == ["minecraft:air"] * 3, 15)
    first = observed()
    if len(first["events"]) != 3 or first["damage"] < 3:
        raise RuntimeError(f"continuous mining missed normal block events/tool wear: {first}")
    wait("continuous mining waits after clearing sight", lambda: (control("status").get("mining") or {}).get("state") == "waiting", 5)
    loop_id = control("status")["mining"]["loopId"]
    request({"id": "continuous", "block": "minecraft:stone", "refill": 1})
    wait("waiting continuous mining resumes", lambda: len(observed().get("events", [])) == 6, 15)
    if control("status")["mining"].get("loopId") != loop_id:
        raise RuntimeError("continuous mining resumed through an unexpected new session")
    console("bot RakNetSpike destroy stop")
    wait("continuous mining stopped while waiting", lambda: not (control("status").get("mining") or {}).get("continuous"), 5)
    request({"id": "continuous", "block": "minecraft:stone", "refill": 2})
    wait("post-stop refill", lambda: observed().get("refill") == 2, 5)
    time.sleep(1.5)
    if observed()["blocks"] != ["minecraft:stone"] * 3 or len(observed()["events"]) != 6:
        raise RuntimeError(f"continuous mining restarted after stop: {observed()}")
    report["mining"]["continuous-retarget-wait-resume-stop"] = True
    for name, command in (("explicit-stop", "bot RakNetSpike destroy stop"),
                          ("all-stop", "bot RakNetSpike stop"),
                          ("hotbar-stop", "bot RakNetSpike hotbar 2")):
        setup(name, "minecraft:obsidian", "minecraft:diamond_pickaxe")
        console("bot RakNetSpike destroy start")
        wait("mining active", lambda: (control("status").get("mining") or {}).get("state") == "active", 5)
        time.sleep(0.15)
        console(command)
        wait("mining canceled", lambda: (control("status").get("mining") or {}).get("state") == "canceled", 5)
        time.sleep(0.4)
        if observed()["block"] != "minecraft:obsidian":
            raise RuntimeError(f"stop did not preserve the block: {name}")
        report["mining"][name] = True
    setup("protection", "minecraft:stone", "minecraft:iron_pickaxe", cancel=True)
    target = observed()["target"]
    console(f"bot RakNetSpike destroy {' '.join(map(str, target))} north")
    wait("cancelable native BlockBreakEvent", lambda: bool(observed().get("events")), 10)
    console("bot RakNetSpike stop")
    time.sleep(0.4)
    if observed()["block"] != "minecraft:stone" or observed()["drops"] or observed()["damage"]:
        raise RuntimeError(f"canceled break modified world/item: {observed()}")
    report["mining"]["protected-block"] = True
    setup("cleanup", "minecraft:air")
