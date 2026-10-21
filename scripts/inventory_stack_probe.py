"""Opt-in stack-request fixture; installed only in raknet_spike's disposable world.

Like mining_probe, SDK writes prepare fixtures, never implement Bot operations.
The client request is the only inventory mutation after a fixture is ready.
"""

import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

EQUIPMENT = ("item_in_off_hand", "helmet", "chestplate", "leggings", "boots")


def probe_inventory_snapshot(player, server, block_ids=None):
    """Probe-only full item data, using the unchanged hotbar item encoder."""
    from endstone_endbot.inventory_sync import hotbar_snapshot

    items = [player.inventory.get_item(slot) for slot in range(36)]
    equipment = [getattr(player.inventory, attribute) for attribute in EQUIPMENT]

    def encode(batch):
        view = SimpleNamespace(unique_id=player.unique_id, inventory=SimpleNamespace(get_item=batch.__getitem__))
        return hotbar_snapshot(view, server, block_ids)["slots"]

    return {"identityId": str(player.unique_id),
            "slots": [item for offset in range(0, 36, 9) for item in encode(items[offset:offset + 9])],
            "equipment": encode(equipment + [None] * 4)[:5]}


def install_probe(plugin_class):
    from endstone.actor import Item
    from endstone.inventory import ItemStack
    from endstone.level import Location

    original_enable = plugin_class.on_enable

    def enable(self):
        original_enable(self)
        base = Path(os.environ["ENDBOT_INVENTORY_STACK_PROBE"])
        current = None
        drops = []
        block_ids = {}

        def sample():
            nonlocal current, drops
            player = self.server.get_player("RakNetSpike")
            if player is None:
                return
            try:
                request = json.loads((base / "stack-fixture.json").read_text())
            except (OSError, ValueError):
                return
            inventory = player.inventory
            if current != request["id"]:
                location = player.location
                for actor in location.dimension.actors:
                    if isinstance(actor, Item):
                        actor.remove()
                x, z = int(location.x), int(location.z)
                for dx in range(-3, 4):
                    for dz in range(-3, 4):
                        location.dimension.get_block_at(x + dx, 199, z + dz).set_type("minecraft:stone")
                        for y in range(200, 204):
                            location.dimension.get_block_at(x + dx, y, z + dz).set_type("minecraft:air")
                player.teleport(Location(location.dimension, x + 0.5, 200, z + 0.5, 0, 0))
                inventory.clear()
                for attribute in EQUIPMENT:
                    setattr(inventory, attribute, None)
                for slot, name, count in request["items"]:
                    item = ItemStack(name, count)
                    if isinstance(slot, int):
                        inventory.set_item(slot, item)
                    else:
                        setattr(inventory, slot, item)
                inventory.held_item_slot = 0
                current, drops = request["id"], []
            for actor in player.location.dimension.actors:
                if isinstance(actor, Item):
                    drops.append({"name": actor.item_stack.type.id, "count": actor.item_stack.amount})
                    actor.remove()  # Prevent re-pickup after recording each ejection.

            def describe(item):
                return {"name": item.type.id, "count": item.amount} if item and item.amount > 0 else None

            state = {
                "id": current,
                "slots": [describe(inventory.get_item(slot)) for slot in range(36)],
                "equipment": {attribute: describe(getattr(inventory, attribute)) for attribute in EQUIPMENT},
                "selected": inventory.held_item_slot,
                "drops": drops,
                "observation": probe_inventory_snapshot(player, self.server, block_ids),
            }
            temporary = base / "stack-state.tmp"
            temporary.write_text(json.dumps(state), encoding="utf-8")
            try:
                temporary.replace(base / "stack-state.json")
            except PermissionError:
                pass

        self.server.scheduler.run_task(self, sample, delay=1, period=1)

    plugin_class.on_enable = enable


def configure_fixture(root: Path, output: Path, environment: dict):
    import shutil

    source = output / "mining-plugin"
    shutil.copy2(root / "scripts/inventory_stack_probe.py", source / "_endbot_inventory_stack_probe.py")
    with (source / "endstone_endbot/plugin.py").open("a", encoding="utf-8") as stream:
        stream.write(
            "\nfrom _endbot_inventory_stack_probe import install_probe as install_stack_probe\n"
            "install_stack_probe(EndbotPlugin)\n"
        )
    environment["ENDBOT_INVENTORY_STACK_PROBE"] = str(output)


def inventory_stack_cases():
    def slot(index, container="hotbar_and_inventory"):
        return {"container": container, "slot": index}

    cases = []
    for operation in ("take", "place", "cursor-move"):
        for source, destination in ((9, 0), (0, 35)):
            cases.append((f"{operation}-{source + 1}-to-{destination + 1}", operation,
                          slot(source), slot(destination), None, [(source, "minecraft:stone", 5)]))
    cases.append(("swap-10-2", "swap", slot(9), slot(1), None,
                  [(9, "minecraft:stone", 5), (1, "minecraft:dirt", 3)]))
    for operation in ("drop", "legacy-drop"):
        for count in (None, 1):
            cases.append((f"{operation}-main-{'stack' if count is None else 'partial'}", operation,
                          slot(9), None, count, [(9, "minecraft:stone", 5)]))
    for index, (attribute, item) in enumerate(zip(EQUIPMENT, (
        "shield", "iron_helmet", "iron_chestplate", "iron_leggings", "iron_boots",
    ), strict=True)):
        cases.append((f"drop-{attribute}", "drop", slot(0 if index == 0 else index - 1,
                      "offhand" if index == 0 else "armor"), None, None,
                      [(attribute, f"minecraft:{item}", 1)]))
    # Legacy equipment transactions carry exact SDK-observed item data, not
    # native stack IDs. Each gets its own disposable fixture.
    equipment_cases = [case for case in cases if case[2]["container"] in {"offhand", "armor"}]
    cases.extend((f"legacy-{name}", "legacy-drop", source, destination, count, items)
                 for name, _, source, destination, count, items in equipment_cases)
    recorded = [{"id": name, "operation": operation, "source": source, "destination": destination,
                 "count": count, "items": items, "openInventory": False}
                for name, operation, source, destination, count, items in cases]
    for case in recorded[:]:
        if case["id"] in {"take-10-to-1", "place-10-to-1", "swap-10-2", "drop-main-stack",
                          "drop-main-partial", "drop-item_in_off_hand"}:
            opened = {**case, "id": "open-" + case["id"], "openInventory": True}
            # The inventory screen uses the inventory/hotbar selectors, not
            # the combined selector used by the closed baseline cases.
            opened["source"] = {**case["source"], "container": (
                "inventory" if case["source"]["container"] == "hotbar_and_inventory" else case["source"]["container"])}
            if case["destination"]:
                opened["destination"] = {**case["destination"], "container": "hotbar"}
            recorded.append(opened)
    # Disconnect-prone native armor requests are always last; legacy armor
    # cases still run before these even when a native request kills the Bot.
    return sorted(recorded, key=lambda case: case["operation"] != "legacy-drop" and case["source"]["container"] == "armor")


def expected_inventory(before, case):
    expected = json.loads(json.dumps(before))
    source, destination, count = case["source"], case["destination"], case["count"]
    if case["operation"] in {"take", "place", "cursor-move", "swap"}:
        a, b = source["slot"], destination["slot"]
        expected["slots"][a], expected["slots"][b] = expected["slots"][b], expected["slots"][a]
    else:
        if source["container"] in {"offhand", "armor"}:
            key = EQUIPMENT[0 if source["container"] == "offhand" else source["slot"] + 1]
            original = before["equipment"][key]
            target = expected["equipment"]
        else:
            key = source["slot"]
            original = before["slots"][key]
            target = expected["slots"]
        amount = original["count"] if count is None else count
        target[key] = {**original, "count": original["count"] - amount} if amount < original["count"] else None
        expected["drops"] = [{**original, "count": amount}]
    if "observation" in expected:
        observed = expected["observation"]
        if case["operation"] in {"take", "place", "cursor-move", "swap"}:
            a, b = source["slot"], destination["slot"]
            observed["slots"][a], observed["slots"][b] = observed["slots"][b], observed["slots"][a]
        else:
            index = (0 if source["container"] == "offhand" else source["slot"] + 1)
            target = observed["equipment"] if source["container"] in {"offhand", "armor"} else observed["slots"]
            index = index if source["container"] in {"offhand", "armor"} else source["slot"]
            item = target[index]
            amount = item["count"] if count is None else count
            target[index] = {**item, "count": item["count"] - amount} if amount < item["count"] else None
    return expected


def classify_inventory_case(case, wire, after, expected):
    keys = ("slots", "equipment", "drops", "selected") + (("observation",) if "observation" in expected else ())
    matches = bool(expected) and after.get("id") == case["id"] and all(after.get(key) == expected[key] for key in keys)
    request_id = wire.get("sent", {}).get("packet", {}).get("requests", [{}])[0].get("request_id")
    responses = [response for event in wire.get("received", []) if event["name"] == "item_stack_response"
                 for response in event["packet"]["responses"]
                 if request_id is not None and response["request_id"] == request_id]
    if wire.get("error") and not wire.get("sent"):
        outcome = "not-sent"
    elif responses and all(response["status"] not in {"ok", 0} for response in responses):
        outcome = "rejected"
    elif (wire.get("sent") and not wire.get("disconnected") and matches
          and (case["operation"] == "legacy-drop" or (responses and all(r["status"] in {"ok", 0} for r in responses)))):
        outcome = "accepted"
    else:
        outcome = "inconclusive"
    return {"inventory_matches": matches, "outcome": outcome}


def reconnect_inventory_bot(control, wait, *, disconnected=False):
    if disconnected or control("status")["connectionState"] != "online":
        control("reconnect")
        wait("inventory Bot reconnect", lambda: control("status")["connectionState"] == "online", 45)
        return True
    return False


def exercise_inventory_stack(output: Path, console, control, wait, report):
    def read(name):
        try:
            return json.loads((output / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def write(name, value):
        temporary = output / (name + ".tmp")
        temporary.write_text(json.dumps(value), encoding="utf-8")
        temporary.replace(output / name)

    results = report["inventory_stack"] = {}
    for case in inventory_stack_cases():
        name = case["id"]
        reconnect_inventory_bot(control, wait)
        before = {}
        interrupted = None
        try:
            control("stop")
            write("stack-fixture.json", {"id": name, "items": case["items"]})
            wait("stack fixture setup", lambda name=name: (
                read("stack-state.json").get("id") == name or control("status")["connectionState"] != "online"), 10)
            if control("status")["connectionState"] != "online":
                raise ConnectionError("Bot disconnected during fixture setup")
            console("bot RakNetSpike hotbar 1")
            time.sleep(1)  # Native packets must arrive before deriving stack IDs.
            before = read("stack-state.json")
            write("stack-send.json", {key: value for key, value in case.items() if key != "items"})
            wait("stack request dispatch", lambda name=name: (
                (read("stack-wire.json").get("id") == name and read("stack-wire.json").get("complete"))
                or control("status")["connectionState"] != "online"), 10)
            time.sleep(0.25)  # Let close acknowledgement and server snapshots settle.
        except Exception as error:
            # Only connection loss is recoverable here. Keep real fixture,
            # filesystem and harness failures visible when the Bot is online.
            if control("status")["connectionState"] == "online":
                raise
            interrupted = str(error)
        wire, after = read("stack-wire.json"), read("stack-state.json")
        if wire.get("id") != name:
            wire = {"id": name, "received": [], "priorWire": wire}
        if interrupted or control("status")["connectionState"] != "online":
            wire["disconnected"] = True
            wire["runnerError"] = interrupted or "Bot disconnected before case completion"
        expected = expected_inventory(before, case) if before.get("id") == name else {}
        result = results[name] = {"case": case, "before": before, "wire": wire, "after": after, "expected": expected,
                                  **classify_inventory_case(case, wire, after, expected)}
        write("inventory-stack-results.json", results)  # Preserve evidence before attempting recovery.
        result["reconnected"] = reconnect_inventory_bot(control, wait, disconnected=wire.get("disconnected", False))
        write("inventory-stack-results.json", results)
