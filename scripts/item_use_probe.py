"""Opt-in item-use observer for raknet_spike's fresh disposable Survival world.

Never installed in the product. Fixture inventory/block writes are confined to
the smoke world; observations use the SDK and incoming normal equipment packets.
"""

import json
import os
import time
from pathlib import Path


def install_probe(plugin_class):
    from endstone.event import PacketReceiveEvent, event_handler
    from endstone.inventory import ItemStack
    from endstone.level import Location

    original_enable = plugin_class.on_enable

    @event_handler
    def on_item_equipment(self, event: PacketReceiveEvent):
        if (
            event.packet_id != 0x1F
            or event.player is None
            or event.player.name != "RakNetSpike"
        ):
            return
        probe = getattr(self, "_item_use_probe", None)
        if not probe:
            return
        # Payload excludes the packet header. Skip runtime entity ID, then
        # inspect the signed-varint item network ID (zero denotes empty).
        offset = 0
        for _ in range(2):
            start = offset
            while offset < len(event.payload) and event.payload[offset] & 0x80:
                offset += 1
            if offset >= len(event.payload):
                return
            offset += 1
        probe["equipment"].append({"empty": event.payload[start:offset] == b"\x00"})

    def enable(self):
        original_enable(self)
        base = Path(os.environ["ENDBOT_ITEM_USE_PROBE"])
        self._item_use_probe = None
        metrics = {"reads": 0, "sends": 0, "payload_bytes": 0, "nanoseconds": []}
        original_publish = self._hotbar_publisher.publish

        def publish(player, server, tick):
            start = time.perf_counter_ns()
            sent = original_publish(player, server, tick)
            metrics["nanoseconds"].append(time.perf_counter_ns() - start)
            metrics["reads"] += 1
            if sent:
                metrics["sends"] += 1
                metrics["payload_bytes"] += len(
                    self._hotbar_publisher.previous[str(player.unique_id)][0]
                )
            return sent

        self._hotbar_publisher.publish = publish

        def sample():
            player = self.server.get_player("RakNetSpike")
            if player is None:
                return
            try:
                request = json.loads((base / "item-use-request.json").read_text())
            except (OSError, ValueError):
                return
            if (
                self._item_use_probe is None
                or self._item_use_probe["id"] != request["id"]
            ):
                loc = player.location
                x, z = int(loc.x), int(loc.z)
                for dx in range(-3, 4):
                    for dz in range(-3, 8):
                        loc.dimension.get_block_at(x + dx, 199, z + dz).set_type(
                            "minecraft:stone"
                        )
                        for y in range(200, 204):
                            loc.dimension.get_block_at(x + dx, y, z + dz).set_type(
                                "minecraft:air"
                            )
                for actor in loc.dimension.actors:
                    if "trident" in actor.type or actor.type == "minecraft:arrow":
                        actor.remove()
                player.inventory.clear()
                player.inventory.set_item(
                    0, ItemStack(request["item"], request.get("count", 1))
                )
                if request["item"] == "minecraft:bow":
                    player.inventory.set_item(1, ItemStack("minecraft:arrow", 5))
                player.inventory.held_item_slot = 0
                player.teleport(Location(loc.dimension, x + 0.5, 200, z + 0.5, 0, -20))
                self._item_use_probe = {
                    "id": request["id"],
                    "equipment": [],
                    "projectiles": [],
                }
            probe = self._item_use_probe
            for actor in player.location.dimension.actors:
                if (
                    "trident" in actor.type or actor.type == "minecraft:arrow"
                ) and actor.type not in probe["projectiles"]:
                    probe["projectiles"].append(actor.type)
            state = {
                **probe,
                "hotbar": [
                    None
                    if (item := player.inventory.get_item(slot)) is None
                    else {"name": item.type.id, "count": item.amount}
                    for slot in range(9)
                ],
                "sync_metrics": metrics,
            }
            temporary = base / "item-use-state.tmp"
            temporary.write_text(json.dumps(state), encoding="utf-8")
            try:
                temporary.replace(base / "item-use-state.json")
            except PermissionError:
                pass

        self.server.scheduler.run_task(self, sample, delay=1, period=1)

    plugin_class.on_item_equipment = on_item_equipment
    plugin_class.on_enable = enable


def configure_fixture(root: Path, output: Path, environment: dict):
    import shutil

    source = output / "mining-plugin"
    shutil.copy2(
        root / "scripts/item_use_probe.py", source / "_endbot_item_use_probe.py"
    )
    with (source / "endstone_endbot/plugin.py").open("a", encoding="utf-8") as stream:
        stream.write(
            "\nfrom _endbot_item_use_probe import install_probe as install_item_use_probe\n"
            "install_item_use_probe(EndbotPlugin)\n"
        )
    environment["ENDBOT_ITEM_USE_PROBE"] = str(output)


def exercise_item_use(output: Path, console, control, wait, log, report):
    last_state = {}

    def state():
        nonlocal last_state
        try:
            last_state = json.loads((output / "item-use-state.json").read_text())
        except (OSError, ValueError):
            pass
        return last_state

    results = {}
    for label, item, duration, count in (
        ("trident", "minecraft:trident", 40, 1),
        ("bow", "minecraft:bow", 40, 1),
        ("food", "minecraft:golden_apple", 50, 3),
    ):
        control("stop")
        temporary = output / "item-use-request.tmp"
        temporary.write_text(json.dumps({"id": label, "item": item, "count": count}))
        temporary.replace(output / "item-use-request.json")
        wait(
            "item-use fixture ready", lambda label=label: state().get("id") == label, 10
        )
        time.sleep(0.6)
        control("hotbar", slot=1)
        offset = len(log())
        equipment_start = len(state()["equipment"])
        console(f"bot RakNetSpike use for {duration}")
        wait(
            "native parser accepts use for",
            lambda duration=duration, offset=offset: (
                f"use started for {duration} ticks" in log()[offset:]
            ),
            10,
        )
        wait(
            "timed use expired",
            lambda: "use" not in control("status")["inputs"]["actions"],
            10,
        )
        if label == "trident":
            wait(
                "BDS trident thrown and empty hand announced",
                lambda equipment_start=equipment_start: (
                    state()["hotbar"][0] is None
                    and any("trident" in actor for actor in state()["projectiles"])
                    and any(
                        packet["empty"]
                        for packet in state()["equipment"][equipment_start:]
                    )
                ),
                10,
            )
        elif label == "bow":
            wait(
                "BDS arrow fired and one arrow consumed",
                lambda: (
                    "minecraft:arrow" in state()["projectiles"]
                    and state()["hotbar"][1]["count"] == 4
                ),
                10,
            )
        else:
            wait(
                "BDS food consumed",
                lambda count=count: state()["hotbar"][0]["count"] < count,
                10,
            )
        results[label] = {
            "duration_ticks": duration,
            "hotbar": state()["hotbar"],
            "projectiles": state()["projectiles"],
            "equipment": state()["equipment"],
        }
    # Stable held item: compare observed sends with reads on the real server.
    before = state()["sync_metrics"]
    time.sleep(3)
    after = state()["sync_metrics"]
    durations = sorted(after["nanoseconds"][before["reads"] :])
    report["hotbar_sync_idle_sample"] = {
        "seconds": 3,
        "bot_count": 1,
        "reads": after["reads"] - before["reads"],
        "sends": after["sends"] - before["sends"],
        "payload_bytes": after["payload_bytes"] - before["payload_bytes"],
        "publish_mean_us": sum(durations) / len(durations) / 1000,
        "publish_max_us": max(durations) / 1000,
    }
    report["item_use"] = results
