import tempfile
import unittest
from pathlib import Path

from plain_service import PlainCommandService
from test_command_service import FakeWorld
from test_groups import GroupControl

from endstone_endbot.commands import CommandSyntaxError, parse_command, strip_colors
from endstone_endbot.forms import BotView, ModalScreen, Show, bot_screen, modal_commands
from endstone_endbot.groups import GroupCommandService, GroupStore

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
                command = parse_command(["Alice", "interact", "entity", *selector])
                self.assertEqual((command.operation, command.parameters), ("interact", parameters))
        for operation in ("entities", "dismount"):
            self.assertEqual(parse_command(["Alice", operation]).operation, operation)

    def test_invalid_forms(self):
        for arguments in (["interact", "entity"], ["interact", "entity", "cow", "pig"], ["interact", "entity", "a/:b"],
                          ["entities", "all"], ["dismount", "now"]):
            with self.subTest(arguments=arguments), self.assertRaises(CommandSyntaxError):
                parse_command(["Alice", *arguments])


class EntityCommandTests(unittest.TestCase):
    def test_entities_lists_with_ids_and_returns_script_data(self):
        control = EntityControl()
        result = PlainCommandService(control).execute(["Alice", "entities"])
        self.assertEqual(result.messages, ("Endbot: Alice: nearby: cow #20 2.1m",))
        self.assertEqual(result.data["entities"][0]["runtimeId"], "20")
        empty = PlainCommandService(EntityControl(entities=[])).execute(["Alice", "entities"])
        self.assertEqual(empty.messages, ("Endbot: Alice: no entities nearby",))

    def test_interact_entity_sends_only_the_chosen_selector(self):
        control = EntityControl()
        service = PlainCommandService(control)
        self.assertEqual(service.execute(["Alice", "interact", "entity", "cow"]).messages,
                         ("Endbot: Alice: interaction with cow #20 requested",))
        service.execute(["Alice", "interact", "entity", "20"])
        service.execute(["Alice", "interact", "entity", "nearest"])
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

        result = PlainCommandService(EntityControl()).execute(["Alice", "interact", "entity", "cow"], Japanese())
        self.assertEqual(result.messages, ("Endbot: Alice: cow #20 への操作を要求しました",))


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
        self.assertIn("#herd[1] Alice: nearby: cow #20 2.0m", lines[0])
        self.assertIn("#herd[2] Bob: nearby: cow #20 2.0m", lines[1])
        self.assertIsNone(result.log_level)
        self.assertEqual(len(service.results.records), 0)


class EntityFormTests(unittest.TestCase):
    def test_gameplay_offers_entity_controls_that_run_commands(self):
        screen = bot_screen(BotView("Alice", "", BOT, None))
        gameplay = next(item for item in screen.items if item.text == "Gameplay...").action.screen
        texts = [item.text for item in gameplay.items]
        self.assertEqual(texts[4:7], ["List nearby entities", "Interact with entity...", "Dismount"])
        self.assertEqual(gameplay.items[4].action.commands, (("Alice", "entities"),))
        self.assertEqual(gameplay.items[6].action.commands, (("Alice", "dismount"),))
        modal = gameplay.items[5].action
        self.assertIsInstance(modal, Show)
        self.assertIsInstance(modal.screen, ModalScreen)
        self.assertEqual(modal_commands(modal.screen, '["Cow"]'), (("Alice", "interact", "entity", "cow"),))
        with self.assertRaises(ValueError):
            modal_commands(modal.screen, '["cow pig"]')

    def test_riding_shows_on_the_bot_screen(self):
        screen = bot_screen(BotView("Alice", "", {**BOT, "vehicle": {"type": "minecraft:boat"}}, None))
        self.assertIn("riding boat", screen.content)


if __name__ == "__main__":
    unittest.main()
