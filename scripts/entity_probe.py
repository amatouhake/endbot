"""Opt-in entity interaction check for raknet_spike --entity (BOT-012).

Never installed in the product. Fixture blocks, items and actors are confined
to the disposable smoke world; outcomes are read from BDS through the SDK.
"""

import json
import math
import os
import time
from pathlib import Path


def install_probe(plugin_class):
    from endstone.inventory import ItemStack
    from endstone.level import Location

    original_enable = plugin_class.on_enable

    def enable(self):
        original_enable(self)
        base = Path(os.environ["ENDBOT_ENTITY_PROBE"])
        self._entity_probe = None

        def sample():
            player = self.server.get_player("RakNetSpike")
            if player is None:
                return
            try:
                request = json.loads((base / "entity-request.json").read_text())
            except (OSError, ValueError):
                return
            if self._entity_probe is None or self._entity_probe["id"] != request["id"]:
                if self._entity_probe is None:
                    loc = player.location
                    self._entity_origin = (math.floor(loc.x), math.floor(loc.z))
                x, z = self._entity_origin
                dimension = player.location.dimension
                for dx in range(-4, 5):
                    for dz in range(-4, 18):
                        dimension.get_block_at(x + dx, 199, z + dz).set_type("minecraft:stone")
                        for y in range(200, 205):
                            dimension.get_block_at(x + dx, y, z + dz).set_type("minecraft:air")
                for actor in dimension.actors:
                    if actor.type != "minecraft:player":
                        actor.remove()
                player.inventory.clear()
                if request.get("item"):
                    player.inventory.set_item(0, ItemStack(request["item"], request.get("count", 1)))
                player.inventory.held_item_slot = 1
                player.teleport(Location(dimension, x + 0.5, 200, z + 0.5, 0, 20))
                target = Location(dimension, x + 0.5, 200, z + 2.5, 0, 0)
                if request["entity"] == "minecraft:minecart" and request.get("track"):
                    # A launch block behind a powered track; power arrives later.
                    dimension.get_block_at(x, 200, z + 1).set_type("minecraft:stone")
                    for dz in range(2, 15):
                        dimension.get_block_at(x, 200, z + dz).set_type("minecraft:golden_rail")
                    dimension.get_block_at(x, 200, z + 15).set_type("minecraft:stone")
                    player.teleport(Location(dimension, x - 0.5, 200, z + 2.5, -90, 20))
                    target = Location(dimension, x + 0.5, 200.1, z + 2.5, 0, 0)
                elif request["entity"] == "minecraft:minecart":
                    dimension.get_block_at(x, 200, z + 2).set_type("minecraft:rail")
                    target = Location(dimension, x + 0.5, 200.1, z + 2.5, 0, 0)
                dimension.spawn_actor(target, request["entity"])
                self._entity_probe = {"id": request["id"]}
            if request.get("power") and not self._entity_probe.get("powered"):
                x, z = self._entity_origin
                for dz in range(2, 15):
                    player.location.dimension.get_block_at(x, 199, z + dz).set_type("minecraft:redstone_block")
                self._entity_probe["powered"] = True
            loc = player.location
            actors = [
                {"type": actor.type, "runtime_id": str(actor.runtime_id),
                 "position": [actor.location.x, actor.location.y, actor.location.z]}
                for actor in loc.dimension.actors if actor.type != "minecraft:player"
            ]
            state = {
                **self._entity_probe,
                "player": [loc.x, loc.y, loc.z],
                "actors": actors,
                "hotbar": [
                    None if (item := player.inventory.get_item(slot)) is None
                    else {"name": item.type.id, "count": item.amount}
                    for slot in range(9)
                ],
            }
            temporary = base / "entity-state.tmp"
            temporary.write_text(json.dumps(state), encoding="utf-8")
            try:
                temporary.replace(base / "entity-state.json")
            except PermissionError:
                pass

        self.server.scheduler.run_task(self, sample, delay=1, period=1)

    plugin_class.on_enable = enable


def configure_fixture(root: Path, output: Path, environment: dict):
    import shutil

    source = output / "mining-plugin"
    shutil.copy2(root / "scripts/entity_probe.py", source / "_endbot_entity_probe.py")
    with (source / "endstone_endbot/plugin.py").open("a", encoding="utf-8") as stream:
        stream.write(
            "\nfrom _endbot_entity_probe import install_probe as install_entity_probe\n"
            "install_entity_probe(EndbotPlugin)\n"
        )
    environment["ENDBOT_ENTITY_PROBE"] = str(output)


def exercise_entity(output: Path, console, control, wait, report, log):
    """Drive the product commands through the native parser; judge outcomes by BDS state."""
    last = {}

    def state():
        nonlocal last
        try:
            last = json.loads((output / "entity-state.json").read_text())
        except (OSError, ValueError):
            pass
        return last

    def request(data):
        temporary = output / "entity-request.tmp"
        temporary.write_text(json.dumps(data))
        temporary.replace(output / "entity-request.json")

    def command(line, expected):
        offset = len(log())
        console(f"bot RakNetSpike {line}")
        wait(f"reply to {line}", lambda: expected in log()[offset:], 10)
        return log()[offset:]

    def prepare(label, entity, item=None, count=1, track=False):
        control("stop")
        request({"id": label, "entity": entity, "item": item, "count": count, "track": track})
        wait(f"{label} fixture ready", lambda: state().get("id") == label and state().get("actors"), 10)
        wait(f"Bot tracks the {entity}", lambda: any(
            candidate["type"] == entity for candidate in control("entities")["entities"]), 10)

    def riding():
        return control("status")["vehicle"]

    results = {}
    # Feeding: the Bot turns to the nearest cow by itself; an adult cow eats wheat only through BDS.
    prepare("feed-cow", "minecraft:cow", "minecraft:wheat", 5)
    command("hotbar 1", "hotbar slot 1 selected")
    listing = command("entities", "nearby: cow #")
    command("interact entity cow", "interaction with cow #")
    wait("BDS consumed one wheat", lambda: (state()["hotbar"][0] or {}).get("count") == 4, 10)
    results["feed_cow"] = {"listing": listing.strip().splitlines()[-1][-160:], "hotbar": state()["hotbar"][0]}

    # Riding by id from the listing, then dismount and walk.
    prepare("ride-minecart", "minecraft:minecart")
    command("hotbar 2", "hotbar slot 2 selected")
    cart = next(entry for entry in control("entities")["entities"] if entry["type"] == "minecraft:minecart")
    command(f"interact entity {cart['runtimeId']}", f"interaction with minecart #{cart['runtimeId']}")
    wait("BDS linked the Bot to the minecart", lambda: (riding() or {}).get("type") == "minecraft:minecart", 10)
    command("status", "riding minecart")
    offset = len(log())
    console("bot RakNetSpike interact entity nearest")
    wait("riding Bots refuse entity interaction", lambda: "dismount first" in log()[offset:], 10)
    command("dismount", "dismount from minecart requested")
    wait("BDS removed the vehicle link", lambda: riding() is None, 10)
    time.sleep(0.5)
    start = list(state()["player"])
    control("look", yaw=180, pitch=0)
    control("move", direction="forward", durationTicks=20)
    wait("Bot walks after dismount", lambda: math.dist(start, state()["player"]) > 0.5, 10)
    results["ride_minecart"] = {"start": start, "after_walk": state()["player"]}

    # A moving minecart: board while stationary, then power the track.
    prepare("ride-powered", "minecraft:minecart", track=True)
    command("hotbar 2", "hotbar slot 2 selected")
    command("interact entity minecart", "interaction with minecart #")
    wait("BDS linked the Bot to the launch minecart", lambda: riding() is not None, 10)
    start = list(state()["player"])
    samples = []
    request({"id": "ride-powered", "entity": "minecraft:minecart", "track": True, "power": True})

    def travelled():
        samples.append(state()["player"])
        return state()["player"][2] - start[2] > 8

    wait("BDS player travels with the powered minecart", travelled, 15)
    time.sleep(1)
    status = control("status")
    if status["vehicle"] is None or status["connectionState"] != "online":
        raise RuntimeError(f"Bot lost the moving minecart: {status['connectionState']} {status['vehicle']}")
    command("dismount", "dismount from minecart requested")
    wait("BDS removed the moving-cart link", lambda: riding() is None, 10)
    time.sleep(1)
    start = list(state()["player"])
    control("look", yaw=0, pitch=0)
    control("move", direction="backward", durationTicks=20)
    wait("Bot walks after leaving a moving cart", lambda: math.dist(start, state()["player"]) > 0.5, 10)
    time.sleep(1.5)
    results["ride_powered"] = {"start": start, "samples": samples, "after_walk": state()["player"],
                               "connection": control("status")["connectionState"]}
    report["entity"] = results
