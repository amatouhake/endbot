import tempfile
import unittest
from pathlib import Path

from endbot_core.commands import CommandSyntaxError, parse_command, strip_colors
from endbot_core.forms import BotView, ModalScreen, Show, bot_screen, modal_commands
from endbot_core.groups import GroupCommandService, GroupStore
from plain_service import PlainCommandService
from test_command_service import FakeWorld
from test_groups import GroupControl

BOT = {"identityId": "00000000-0000-4000-8000-000000000001", "name": "Alice", "desiredState": "online",
       "connectionState": "online"}


class EntityControl:
    def __init__(self, vehicle=None, entities=None):
        self.calls = []
        self.vehicle = vehicle
        cow = {"runtimeId": "20", "type": "minecraft:cow", "distance": 2.1}
        self.entities = [cow] if entities is None else entities

    def request(self, method, **params):
        self.calls.append((method, params))
        status = {**BOT, "vehicle": self.vehicle}
        if method == "entities":
            return {**status, "entities": self.entities}
        if method == "interactEntity":
            return {**status, "entity": {"runtimeId": "20", "type": "minecraft:cow", "distance": 2.1}}
        if method == "dismount":
            return {**status, "vehicle": {"runtimeId": "30", "type": "minecraft:minecart"}}
        return status


class EntityParseTests(unittest.TestCase):
    def test_selectors(self):
        cases = {
            ("nearest",): {"entity": "nearest"},
            ("NEAREST",): {"entity": "nearest"},
            ("cow",): {"entity": "type", "type": "minecraft:cow"},
            ("minecraft:cow",): {"entity": "type", "type": "minecraft:cow"},
            ("0042",): {"entity": "id", "runtimeId": "42"},
        }
        for selector, parameters in cases.items():
            with self.subTest(selector=selector):
                command = parse_command(["Alice", "interact", "@" + selector[0]])
                self.assertEqual((command.operation, command.parameters), ("interact", parameters))
        for operation in ("entities", "dismount"):
            self.assertEqual(parse_command(["Alice", operation]).operation, operation)

    def test_invalid_forms(self):
        for arguments in (["interact", "cow", "pig"], ["interact", "@a/:b"], ["destroy", "@cow"],
                          ["entities", "all"], ["dismount", "now"]):
            with self.subTest(arguments=arguments), self.assertRaises(CommandSyntaxError):
                parse_command(["Alice", *arguments])


class EntityCommandTests(unittest.TestCase):
    def test_entities_lists_with_ids_and_returns_script_data(self):
        control = EntityControl()
        result = PlainCommandService(control).execute(["Alice", "entities"])
        self.assertEqual(result.messages, ("Endbot: Alice: nearby: cow @20 (2.1m)",))
        self.assertEqual(result.data["entities"][0]["runtimeId"], "20")
        self.assertEqual(result.data["entities"][0]["selector"], "@20")
        empty = PlainCommandService(EntityControl(entities=[])).execute(["Alice", "entities"])
        self.assertEqual(empty.messages, ("Endbot: Alice: no entities nearby",))

    def test_interact_entity_sends_only_the_chosen_selector(self):
        control = EntityControl()
        service = PlainCommandService(control)
        self.assertEqual(service.execute(["Alice", "interact", "@cow"]).messages,
                         ("Endbot: Alice: interaction with cow @20 requested",))
        service.execute(["Alice", "interact", "@20"])
        service.execute(["Alice", "interact", "@nearest"])
        self.assertEqual([params for method, params in control.calls if method == "interactEntity"], [
            {"name": "Alice", "type": "minecraft:cow"}, {"name": "Alice", "runtimeId": "20"}, {"name": "Alice"},
        ])

    def test_dismount_and_status_show_the_vehicle(self):
        control = EntityControl(vehicle={"runtimeId": "30", "type": "minecraft:minecart"})
        service = PlainCommandService(control)
        self.assertEqual(service.execute(["Alice", "dismount"]).messages,
                         ("Endbot: Alice: dismount from minecart requested",))
        status = service.execute(["Alice", "status"])
        self.assertIn("riding minecart", status.messages[0])
        self.assertEqual(status.data["vehicle"]["type"], "minecraft:minecart")

    def test_japanese_replies(self):
        class Japanese:
            language = "ja"

        result = PlainCommandService(EntityControl()).execute(["Alice", "interact", "@cow"], Japanese())
        self.assertEqual(result.messages, ("Endbot: Alice: cow @20 への操作を要求しました",))


class EntityGroupControl(GroupControl):
    def request(self, operation, **parameters):
        if operation == "entities":
            self.calls.append((operation, parameters))
            return {**self.bots[parameters["name"]], "entities": [
                {"runtimeId": "20", "type": "minecraft:cow", "distance": 2.0}]}
        return super().request(operation, **parameters)


class EntityGroupTests(unittest.TestCase):
    def test_group_entities_is_a_per_member_query_without_history(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        control = EntityGroupControl()
        service = GroupCommandService(control, FakeWorld(), GroupStore(Path(temporary.name) / "groups.json"),
                                      lambda _: True)
        for name in ("Alice", "Bob"):
            service.execute([name, "spawn"])
        service.execute_group(["create", "#herd"])
        service.execute_group(["add", "#herd", "Alice"])
        service.execute_group(["add", "#herd", "Bob"])
        result = service.execute(["#herd", "entities"])
        lines = [strip_colors(message) for message in result.messages]
        self.assertEqual(len(lines), 2)
        self.assertIn("#herd[1] Alice: nearby: cow @20 (2.0m)", lines[0])
        self.assertIn("#herd[2] Bob: nearby: cow @20 (2.0m)", lines[1])
        self.assertIsNone(result.log_level)
        self.assertEqual(len(service.results.records), 0)


class EntityFormTests(unittest.TestCase):
    def test_gameplay_offers_entity_controls_that_run_commands(self):
        screen = bot_screen(BotView("Alice", "", BOT, None))
        gameplay = next(item for item in screen.items if item.text == "Gameplay...").action.screen
        texts = [item.text for item in gameplay.items]
        self.assertEqual(texts[4:7], ["Interact with entity...", "Dismount", "Look around..."])
        self.assertEqual(gameplay.items[5].action.commands, (("Alice", "dismount"),))
        around = gameplay.items[6].action.screen
        self.assertEqual([item.action.commands for item in around.items[:2]],
                         [(("Alice", "target"),), (("Alice", "entities"),)])
        self.assertEqual(modal_commands(around.items[2].action.screen, '["Sheep"]'), (("Alice", "entity", "@sheep"),))
        # The form's argv must parse; `@` typed by the player is accepted too.
        self.assertEqual(modal_commands(around.items[2].action.screen, '["@cow"]'), (("Alice", "entity", "@cow"),))
        self.assertEqual(parse_command(["Alice", "entity", "@sheep"]).parameters,
                         {"entity": "type", "type": "minecraft:sheep"})
        self.assertEqual(modal_commands(around.items[3].action.screen, '["~ ~-1 ~"]'),
                         (("Alice", "block", "~", "~-1", "~"),))
        self.assertEqual(modal_commands(around.items[4].action.screen, '["oak_log", 5, ""]'),
                         (("Alice", "blocks", "oak_log", "radius", "5"),))
        self.assertEqual(modal_commands(around.items[4].action.screen, '["oak_log", 5, "~ ~10 ~"]'),
                         (("Alice", "blocks", "oak_log", "radius", "5", "at", "~", "~10", "~"),))
        modal = gameplay.items[4].action
        self.assertIsInstance(modal, Show)
        self.assertIsInstance(modal.screen, ModalScreen)
        self.assertEqual(modal_commands(modal.screen, '["Cow"]'), (("Alice", "interact", "@cow"),))
        self.assertEqual(modal_commands(modal.screen, '["@9"]'), (("Alice", "interact", "@9"),))
        with self.assertRaises(ValueError):
            modal_commands(modal.screen, '["cow pig"]')

    def test_riding_shows_on_the_bot_screen(self):
        screen = bot_screen(BotView("Alice", "", {**BOT, "vehicle": {"type": "minecraft:boat"}}, None))
        self.assertIn("riding boat", screen.content)


if __name__ == "__main__":
    unittest.main()


class FakeBlock:
    def __init__(self, block_type):
        self.type = block_type


class FakeDimension:
    def __init__(self, solid, actors):
        self.solid, self.actors = solid, actors

    def get_block_at(self, x, y, z):
        return FakeBlock("minecraft:stone" if (x, y, z) in self.solid else "minecraft:air")


class FakeActor:
    def __init__(self, runtime_id, actor_type, x, y, z, yaw=0.0, velocity=(0.0, 0.0, 0.0)):
        from types import SimpleNamespace

        self.runtime_id, self.type = runtime_id, actor_type
        self.location = SimpleNamespace(x=x, y=y, z=z, yaw=yaw, pitch=0.0)
        self.velocity = SimpleNamespace(x=velocity[0], y=velocity[1], z=velocity[2])


class DismountExitTests(unittest.TestCase):
    def world(self, solid, vehicle, bot_yaw=0.0):
        from types import SimpleNamespace

        from endstone_endbot.world import EndstoneWorld

        dimension = FakeDimension(solid, [vehicle])
        bot = SimpleNamespace(location=SimpleNamespace(x=vehicle.location.x, y=vehicle.location.y,
                                                       z=vehicle.location.z, yaw=bot_yaw, dimension=dimension))
        server = SimpleNamespace(get_player=lambda _identity: bot)
        return EndstoneWorld(server)

    def floor(self, *cells):
        return {(x, 63, z) for x, z in cells}

    def test_minecart_exits_right_of_travel_then_left(self):
        cart = FakeActor(8, "minecraft:minecart", 0.5, 64.0, 0.5, velocity=(0.0, 0.0, 0.4))  # travelling +Z
        world = self.world(self.floor((-1, 0), (1, 0)), cart)
        self.assertEqual(world.resolve_dismount(BOT["identityId"], "8"), {"x": -0.5, "y": 64.0, "z": 0.5})
        world = self.world(self.floor((1, 0)), cart)
        self.assertEqual(world.resolve_dismount(BOT["identityId"], "8"), {"x": 1.5, "y": 64.0, "z": 0.5})

    def test_boat_prefers_the_look_direction_and_blocked_cells_are_skipped(self):
        boat = FakeActor(9, "minecraft:oak_boat", 0.5, 64.0, 0.5)
        world = self.world(self.floor((0, 1), (-1, 0)), boat, bot_yaw=0.0)
        self.assertEqual(world.resolve_dismount(BOT["identityId"], "9"), {"x": 0.5, "y": 64.0, "z": 1.55})
        walled = self.floor((0, 1), (-1, 0)) | {(0, 64, 1)}
        world = self.world(walled, boat, bot_yaw=0.0)
        self.assertEqual(world.resolve_dismount(BOT["identityId"], "9"), {"x": -0.55, "y": 64.0, "z": 0.5})

    def test_steps_up_one_level_or_stays_without_a_free_spot(self):
        cart = FakeActor(8, "minecraft:minecart", 0.5, 64.0, 0.5, yaw=90.0)
        world = self.world({(0, 64, -1)}, cart)
        self.assertEqual(world.resolve_dismount(BOT["identityId"], "8"), {"x": 0.5, "y": 65.0, "z": -0.5})
        self.assertIsNone(self.world(set(), cart).resolve_dismount(BOT["identityId"], "8"))
        self.assertIsNone(self.world(set(), cart).resolve_dismount(BOT["identityId"], "99"))

    def test_dismount_command_passes_the_exit_position(self):
        class World:
            def resolve_dismount(self, identity, runtime_id):
                self.args = (identity, runtime_id)
                return {"x": -0.5, "y": 64.0, "z": 0.5}

        control, world = EntityControl(vehicle={"runtimeId": "30", "type": "minecraft:minecart"}), World()
        PlainCommandService(control, world).execute(["Alice", "dismount"])
        self.assertEqual(world.args, (BOT["identityId"], "30"))
        exit_position = {"x": -0.5, "y": 64.0, "z": 0.5}
        self.assertEqual(control.calls[-1], ("dismount", {"name": "Alice", "exit": exit_position}))


class VehicleClearanceTests(unittest.TestCase):
    def test_exit_point_stays_inside_its_cell_and_clear_of_the_vehicle(self):
        from endstone_endbot.world import _clear_of_vehicle

        # A boat 1.0 from the cell centre: shift 0.05 further, still inside the cell.
        self.assertEqual(_clear_of_vehicle((0, 1), (0.5, 0.5), 0.7), (0.5, 1.55))
        # A boat 0.8 from the cell centre cannot be cleared without leaving the cell.
        self.assertIsNone(_clear_of_vehicle((3, 0), (2.7, 0.5), 0.7))
        # A minecart one block away is already clear.
        self.assertEqual(_clear_of_vehicle((1, 0), (0.5, 0.5), 0.49), (1.5, 0.5))


class GroundCheckTests(unittest.TestCase):
    def player(self, solid, y=64.0, x=0.5, z=0.5, actors=(), water=False):
        from types import SimpleNamespace

        dimension = FakeDimension(solid, list(actors))
        return SimpleNamespace(location=SimpleNamespace(x=x, y=y, z=z, dimension=dimension), is_in_water=water,
                               is_flying=False)

    def test_reports_only_a_missing_floor_under_a_block_top(self):
        from endstone_endbot.world import unsupported

        self.assertFalse(unsupported(self.player({(0, 63, 0)})))
        self.assertTrue(unsupported(self.player(set())))
        # Straddling an edge: a neighbouring floor block still supports the Bot.
        self.assertFalse(unsupported(self.player({(1, 63, 0)}, x=0.9)))
        # Slab/carpet heights, water and standing on a boat are never reported.
        self.assertFalse(unsupported(self.player(set(), y=64.5)))
        self.assertFalse(unsupported(self.player(set(), water=True)))
        boat = FakeActor(9, "minecraft:boat", 0.6, 63.6, 0.5)
        self.assertFalse(unsupported(self.player(set(), actors=[boat])))
        far_boat = FakeActor(9, "minecraft:boat", 2.5, 64.0, 0.5)
        self.assertTrue(unsupported(self.player(set(), actors=[far_boat])))

    def test_payload_names_the_bot(self):
        from endstone_endbot.inventory_sync import GROUND_EVENT, unsupported_payload

        payload = unsupported_payload("abc")
        self.assertIn(GROUND_EVENT.encode(), payload)
        self.assertTrue(payload.endswith(b'{"identityId":"abc"}'))
