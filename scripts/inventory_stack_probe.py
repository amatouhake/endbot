"""Opt-in stack-request fixture; installed only in raknet_spike's disposable world.

Like mining_probe, SDK writes prepare fixtures, never implement Bot operations.
The client request is the only inventory mutation after a fixture is ready.
"""

import json
import os
import time
from pathlib import Path

EQUIPMENT = ("item_in_off_hand", "helmet", "chestplate", "leggings", "boots")


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
    results = report["inventory_stack"] = {}
    for name, operation, source, destination, count, items in cases:
        control("stop")
        write("stack-fixture.json", {"id": name, "items": items})
        wait("stack fixture setup", lambda name=name: read("stack-state.json").get("id") == name, 10)
        console("bot RakNetSpike hotbar 1")
        time.sleep(1)  # Native inventory packets must arrive before deriving stack IDs.
        before = read("stack-state.json")
        write("stack-send.json", {"id": name, "operation": operation, "source": source,
                                  "destination": destination, "count": count})
        wait("stack request dispatch", lambda name=name: read("stack-wire.json").get("id") == name, 10)
        time.sleep(2)  # Record rejection/no response too; do not assume success from a send.
        wire, after = read("stack-wire.json"), read("stack-state.json")
        responses = [response for event in wire.get("received", []) if event["name"] == "item_stack_response"
                     for response in event["packet"]["responses"]
                     if response["request_id"] == wire.get("sent", {}).get("packet", {}).get("requests", [{}])[0].get("request_id")]
        expected = json.loads(json.dumps(before))
        if operation in {"take", "place", "cursor-move", "swap"}:
            a, b = source["slot"], destination["slot"]
            expected["slots"][a], expected["slots"][b] = expected["slots"][b], expected["slots"][a]
        else:
            if source["container"] in {"offhand", "armor"}:
                key = EQUIPMENT[0 if source["container"] == "offhand" else source["slot"] + 1]
                expected["equipment"][key] = None
                removed = before["equipment"][key]
            else:
                original = before["slots"][source["slot"]]
                removed = {**original, "count": count or original["count"]}
                expected["slots"][source["slot"]] = {**original, "count": original["count"] - count} if count else None
            expected["drops"] = [removed]
        matches = all(after.get(key) == expected[key] for key in ("slots", "equipment", "drops", "selected"))
        results[name] = {"before": before, "wire": wire, "after": after, "expected": expected,
                         "inventory_matches": matches,
                         "outcome": "not-sent" if wire.get("error") else (
                             "accepted" if matches and (operation == "legacy-drop" or any(r["status"] == "ok" for r in responses))
                             else "rejected" if responses and all(r["status"] != "ok" for r in responses)
                             else "inconclusive")}
        (output / "inventory-stack-results.json").write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
