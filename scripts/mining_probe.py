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
    from endstone.event import BlockBreakEvent, PlayerDropItemEvent, event_handler
    from endstone.inventory import ItemStack
    from endstone.level import Location

    original_enable = plugin_class.on_enable

    @event_handler
    def on_probe_drop(self, event: PlayerDropItemEvent):
        probe = getattr(self, "_mining_probe", None)
        if (
            probe
            and event.player.name == "RakNetSpike"
            and probe.get("inventory")
            and probe.get("cancel_drop")
        ):
            event.is_cancelled = True
            probe["canceled_drops"] = probe.get("canceled_drops", 0) + 1

    @event_handler
    def on_probe_break(self, event: BlockBreakEvent):
        probe = getattr(self, "_mining_probe", None)
        if not probe or event.player.name != "RakNetSpike":
            return
        if [event.block.x, event.block.y, event.block.z] not in probe["targets"]:
            return
        if probe["cancel"]:
            event.is_cancelled = True
        probe["events"].append(
            {
                "player": event.player.name,
                "block": event.block.type,
                "canceled": event.is_cancelled,
            }
        )

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
                            dimension.get_block_at(x + dx, 199, z + dz).set_type(
                                "minecraft:stone"
                            )
                            for y in range(200, 204):
                                dimension.get_block_at(x + dx, y, z + dz).set_type(
                                    "minecraft:air"
                                )
                    target = [x, 201, z + 2]
                    targets = [
                        [x, 201, z + 2 + index] for index in range(data.get("count", 1))
                    ]
                    for cell in targets:
                        dimension.get_block_at(*cell).set_type(data["block"])
                    player.inventory.clear()
                    inventory_test = data.get("inventory")
                    if inventory_test:
                        for slot in range(data.get("slot", 0)):
                            player.inventory.set_item(
                                slot, ItemStack("minecraft:stone", 64)
                            )
                        if data.get("existing"):
                            player.inventory.set_item(
                                data.get("slot", 0),
                                ItemStack(inventory_test, data["existing"]),
                            )
                    if data.get("tool"):
                        player.inventory.set_item(0, ItemStack(data["tool"], 1))
                    player.inventory.held_item_slot = 0
                    player.teleport(Location(dimension, x + 0.5, 200, z + 0.5, 0, 0))
                    self._mining_probe = {
                        "id": data["id"],
                        "target": target,
                        "cancel": data.get("cancel", False),
                        "targets": targets,
                        "events": [],
                        "refill": data.get("refill", 0),
                        "kill": data.get("kill", 0),
                        "inventory": inventory_test,
                        "cancel_drop": data.get("cancel_drop", False),
                        "dropped": [],
                    }
                    if inventory_test:
                        stack = ItemStack(inventory_test, data.get("amount", 5))
                        if data.get("named"):
                            meta = stack.item_meta
                            meta.display_name = "Endbot pickup probe"
                            meta.damage = 7
                            meta.add_enchant("minecraft:efficiency", 2)
                            stack.set_item_meta(meta)
                        self._mining_probe["pickup_actor"] = str(
                            dimension.drop_item(player.location, stack).id
                        )
                elif self._mining_probe["refill"] != data.get("refill", 0):
                    # Replace blocks without teleporting/restarting the Bot.
                    for cell in self._mining_probe["targets"]:
                        player.location.dimension.get_block_at(*cell).set_type(
                            data["block"]
                        )
                    self._mining_probe["refill"] = data.get("refill", 0)
                if self._mining_probe is not None and self._mining_probe.get(
                    "kill", 0
                ) != data.get("kill", 0):
                    self._mining_probe["kill"] = data.get("kill", 0)
                    player.health = 0
            if self._mining_probe is None:
                return
            probe = self._mining_probe
            if probe.get("inventory"):
                probe["cancel_drop"] = (
                    data.get("cancel_drop", False) if request.exists() else False
                )
                for actor in player.location.dimension.actors:
                    if (
                        isinstance(actor, Item)
                        and str(actor.id) != probe["pickup_actor"]
                    ):
                        probe["dropped"].append(
                            {
                                "name": actor.item_stack.type.id,
                                "count": actor.item_stack.amount,
                                "nbt": str(actor.item_stack.nbt),
                            }
                        )
                        actor.remove()  # Observe each ejection once; prevent a later re-pickup in the fixture.
            item = player.inventory.get_item(0)
            state = {
                **probe,
                "block": player.location.dimension.get_block_at(*probe["target"]).type,
                "blocks": [
                    player.location.dimension.get_block_at(*cell).type
                    for cell in probe["targets"]
                ],
                "damage": item.item_meta.damage if item else 0,
                "position": [player.location.x, player.location.y, player.location.z],
                "drops": [
                    actor.item_stack.type.id
                    for actor in player.location.dimension.actors
                    if isinstance(actor, Item)
                ],
            }
            if probe.get("inventory"):
                state["hotbar"] = [
                    None
                    if (stack := player.inventory.get_item(slot)) is None
                    else {
                        "name": stack.type.id,
                        "count": stack.amount,
                        "nbt": str(stack.nbt),
                    }
                    for slot in range(9)
                ]
            temporary = base / "mining-state.tmp"
            temporary.write_text(json.dumps(state), encoding="utf-8")
            try:
                temporary.replace(base / "mining-state.json")
            except PermissionError:
                pass  # Retry the latest observation next server tick.

        self.server.scheduler.run_task(self, sample, delay=1, period=1)

    plugin_class.on_probe_break = on_probe_break
    plugin_class.on_probe_drop = on_probe_drop
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


def exercise_mining(output: Path, console, control, wait, report, log=lambda: ""):
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
        # Only drops of the mined block count; world generation can leave unrelated item actors nearby.
        if name == "stone-hand" and {"minecraft:stone", "minecraft:cobblestone"} & set(state["drops"]):
            raise RuntimeError(f"stone dropped loot with the wrong tool: {state}")
        wait("mining runtime confirmation", lambda: (control("status").get("mining") or {}).get("state") == "completed", 5)
        report.setdefault("mining", {})[name] = {"seconds": round(elapsed, 2), "damage": state["damage"], "drops": state["drops"]}
    setup("continuous", "minecraft:stone", "minecraft:iron_pickaxe", count=3)
    console("bot RakNetSpike destroy continuous")
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
    def mining():
        return control("status").get("mining") or {}

    # Other operations must not stop a single-block break, even though they
    # change the Bot's look direction mid-break.
    setup("operations-continue", "minecraft:stone")
    console("bot RakNetSpike destroy")
    wait("single break active", lambda: mining().get("state") == "active", 5)
    for command in ("look 30 10", "use", "attack", "jump"):
        time.sleep(0.4)
        console(f"bot RakNetSpike {command}")
    if mining().get("state") != "active":
        raise RuntimeError(f"another operation stopped a single break: {mining()}")
    wait("single break completes after other operations", lambda: observed().get("block") == "minecraft:air", 30)
    if not observed()["events"] or observed()["events"][-1]["canceled"]:
        raise RuntimeError(f"break after other operations was not a normal Bot break: {observed()}")
    report["mining"]["operations-continue"] = True

    # Turning away pauses a continuous loop; turning back resumes it.
    setup("continuous-look", "minecraft:stone", "minecraft:iron_pickaxe", count=3)
    console("bot RakNetSpike destroy continuous")
    wait("first continuous block", lambda: observed().get("blocks", [None])[0] == "minecraft:air", 10)
    loop_id = mining().get("loopId")
    console("bot RakNetSpike look 90 0")
    wait("loop waits while facing away", lambda: mining().get("state") == "waiting", 10)
    time.sleep(1.0)
    # The block already being broken when the Bot turns still finishes (other
    # operations never interrupt it); the loop then has nothing in sight.
    if observed()["blocks"][2] != "minecraft:stone" or mining().get("loopId") != loop_id:
        raise RuntimeError(f"look change did not pause the same loop: {observed()} {mining()}")
    console("bot RakNetSpike look 0 0")
    wait("loop resumes after facing back", lambda: observed().get("blocks") == ["minecraft:air"] * 3, 15)
    if mining().get("loopId") != loop_id:
        raise RuntimeError(f"look change replaced the continuous loop: {mining()}")
    console("bot RakNetSpike destroy stop")
    report["mining"]["continuous-follows-look"] = True

    # A hotbar switch restarts the current block with the new item.
    setup("hotbar-restart", "minecraft:obsidian", "minecraft:diamond_pickaxe")
    console("bot RakNetSpike destroy continuous")
    wait("mining active", lambda: mining().get("state") == "active", 5)
    loop_id = mining().get("loopId")
    time.sleep(0.3)
    console("bot RakNetSpike hotbar 2")
    time.sleep(1.0)
    if mining().get("state") != "active" or mining().get("loopId") != loop_id:
        raise RuntimeError(f"hotbar switch ended mining: {mining()}")
    console("bot RakNetSpike destroy stop")
    wait("mining canceled", lambda: mining().get("state") == "canceled", 5)
    if observed()["block"] != "minecraft:obsidian":
        raise RuntimeError("bare-hand restart unexpectedly broke obsidian")
    report["mining"]["hotbar-restart"] = True

    for name, command in (("explicit-stop", "bot RakNetSpike destroy stop"),
                          ("all-stop", "bot RakNetSpike stop")):
        setup(name, "minecraft:obsidian", "minecraft:diamond_pickaxe")
        console("bot RakNetSpike destroy continuous")
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

    # Death is an abnormal end: the loop stops and the controller is told.
    setup("death-notice", "minecraft:obsidian", "minecraft:diamond_pickaxe")
    console("bot RakNetSpike destroy continuous")
    wait("mining active", lambda: mining().get("state") == "active", 5)
    offset = len(log())
    request({"id": "death-notice", "block": "minecraft:obsidian", "kill": 1})
    wait("death ends continuous mining", lambda: not mining().get("continuous"), 10)
    wait(
        "death notice in the console log",
        lambda: "destroy continuous stopped: canceled by death" in log()[offset:],
        10,
    )
    wait("Bot respawned", lambda: control("status").get("connectionState") == "online", 20)
    time.sleep(2)
    report["mining"]["death-notice"] = True

    # destroy for: the loop ends after its duration and aborts the slow block.
    setup("duration", "minecraft:obsidian", "minecraft:diamond_pickaxe")
    offset = len(log())
    console("bot RakNetSpike destroy for 40")
    wait("destroy for reply", lambda: "destroy continuous started for 40 ticks" in log()[offset:], 10)
    wait("mining active", lambda: mining().get("state") == "active", 5)
    wait("destroy for expired", lambda: not mining().get("continuous"), 6)
    wait(
        "duration notice in the console log",
        lambda: "destroy continuous stopped: duration elapsed" in log()[offset:],
        10,
    )
    time.sleep(1)
    if mining().get("reason") != "duration elapsed" or observed()["block"] != "minecraft:obsidian":
        raise RuntimeError(f"destroy for did not abort the block: {mining()} {observed()}")
    report["mining"]["duration-end"] = True
    setup("cleanup", "minecraft:air")


def exercise_inventory(output: Path, console, control, wait, report):
    """Natural pickups and real BDS transactions, with no Bot reconnect."""
    import time

    last_state = {}

    def state():
        nonlocal last_state
        try:
            last_state = json.loads((output / "mining-state.json").read_text())
        except (OSError, ValueError):
            pass  # Atomic replacement can briefly deny reads on Windows.
        return last_state

    def request(data):
        temporary = output / "mining-request.tmp"
        temporary.write_text(json.dumps(data))
        temporary.replace(output / "mining-request.json")

    results = {}
    for label, name, slot, amount, existing, named in (
        ("block", "minecraft:dirt", 0, 5, 0, False),
        ("food-nonselected-slot", "minecraft:apple", 1, 5, 0, False),
        ("merge", "minecraft:apple", 0, 4, 3, False),
        ("named-enchanted-damaged-tool", "minecraft:diamond_pickaxe", 2, 1, 0, True),
        ("canceled-drop", "minecraft:apple", 0, 5, 0, False),
    ):
        control("stop")
        fixture = {
            "id": f"inventory-{label}",
            "block": "minecraft:air",
            "inventory": name,
            "slot": slot,
            "amount": amount,
            "existing": existing,
            "named": named,
            "cancel_drop": label == "canceled-drop",
        }
        request(fixture)

        def picked_up(slot=slot, label=label, amount=amount, existing=existing):
            current = state()
            stack = (current.get("hotbar") or [None] * 9)[slot]
            return (
                current.get("id") == f"inventory-{label}"
                and stack
                and stack["count"] == amount + existing
            )

        wait("natural world pickup", picked_up, 15)
        time.sleep(0.6)  # Two or more observations after BDS completed the pickup.
        before = state()["hotbar"][slot]
        control("hotbar", slot=slot + 1)
        if label == "canceled-drop":
            control("drop", stack=False)
            wait(
                "native drop cancellation",
                lambda: state().get("canceled_drops", 0) == 1,
                10,
            )
            time.sleep(1.2)  # Include the default unchanged-state resend after a rejected prediction.
            if state()["hotbar"][slot]["count"] != amount or state().get("dropped"):
                raise RuntimeError(
                    "Canceled drop changed BDS inventory or created loot"
                )
            request({**fixture, "cancel_drop": False})
            wait(
                "drop cancellation disabled", lambda: not state().get("cancel_drop"), 10
            )
        control("drop", stack=False)
        remaining = amount + existing - 1

        def dropped_one(slot=slot, remaining=remaining):
            current = state()
            stack = (current.get("hotbar") or [None] * 9)[slot]
            return (
                bool(current.get("dropped"))
                and (stack["count"] if stack else 0) == remaining
            )

        wait("one real selected item dropped", dropped_one, 10)
        first = state()
        if first["dropped"][0]["count"] != 1 or first["dropped"][0]["name"] != name:
            raise RuntimeError(f"Wrong item/count dropped after pickup: {label}")
        if named and first["dropped"][0]["nbt"] != before["nbt"]:
            raise RuntimeError("Pickup/drop lost custom item NBT")
        if remaining:
            control("drop", stack=True)
            wait(
                "remaining real stack dropped",
                lambda slot=slot: (
                    len(state().get("dropped", [])) == 2
                    and state()["hotbar"][slot] is None
                ),
                10,
            )
            if state()["dropped"][1]["count"] != remaining:
                raise RuntimeError("Drop stack ejected the wrong amount")
        time.sleep(0.4)
        try:
            control("drop", stack=False)
        except RuntimeError as error:
            if "Selected hotbar slot is empty" not in str(error):
                raise
        else:
            raise RuntimeError(
                "Empty selected slot still appeared usable after the drop"
            )
        results[label] = {
            "picked_up": amount,
            "existing": existing,
            "slot": slot + 1,
            "dropped": [drop["count"] for drop in state()["dropped"]],
            "nbt_preserved": True,
        }
    request({"id": "inventory-cleanup", "block": "minecraft:air"})
    wait(
        "inventory fixture cleanup",
        lambda: state().get("id") == "inventory-cleanup",
        10,
    )
    report["pickup_inventory"] = results
