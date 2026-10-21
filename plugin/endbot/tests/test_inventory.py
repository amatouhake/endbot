import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID

from plain_service import PlainCommandService
from test_world import EndstoneWorld

from endstone_endbot.commands import parse_command
from endstone_endbot.inventory_sync import EQUIPMENT_SLOTS, inventory_snapshot


def item(name="minecraft:stone", count=5):
    return SimpleNamespace(type=SimpleNamespace(id=name), amount=count)


def player(slots=None, equipment=None):
    slots = slots or {}
    inventory = SimpleNamespace(get_item=Mock(side_effect=lambda slot: slots.get(slot)), **{
        attribute: (equipment or {}).get(label) for label, attribute in EQUIPMENT_SLOTS
    })
    return SimpleNamespace(name="Alice", xuid="", inventory=inventory)


class InventoryTests(unittest.TestCase):
    def service(self, bot):
        server = SimpleNamespace(get_player=Mock(return_value=bot))
        # No runtime at all: only the server adapter is needed for this query.
        return PlainCommandService(world=EndstoneWorld(server)), server

    def test_parser_accepts_only_the_argument_free_query(self):
        for target in ("Alice", "#miners", "#miners[1]"):
            command = parse_command([target, "INVENTORY"])
            self.assertEqual((command.operation, command.name, command.parameters), ("inventory", target, {}))
        for tail in ("all", "1", "offhand"):
            with self.assertRaises(ValueError):
                parse_command(["Alice", "inventory", tail])

    def test_empty_inventory_including_zero_and_air_says_so(self):
        bot = player({0: item(count=0), 9: item("minecraft:air")})
        service, server = self.service(bot)
        result = service.execute(["Alice", "inventory"])
        self.assertEqual(result.messages, ("Endbot: Alice: inventory is empty",))
        self.assertIsNone(result.log_level)
        server.get_player.assert_called_once_with("Alice")
        self.assertEqual(bot.inventory.get_item.call_count, 36)

    def test_full_inventory_shows_all_screen_slots_and_equipment(self):
        bot = player({slot: item("minecraft:stone", slot + 1) for slot in range(36)},
                     {label: item(f"minecraft:{label}_item", 1) for label, _ in EQUIPMENT_SLOTS})
        service, _ = self.service(bot)
        result = service.execute(["Alice", "inventory"])
        self.assertNotIn("minecraft:", result.message)
        self.assertIn("inventory: 1: stone x1, 2: stone x2", result.message)
        self.assertIn("9: stone x9, 10: stone x10", result.message)
        self.assertIn("36: stone x36, offhand: offhand_item x1", result.message)
        for label in ("head", "chest", "legs", "feet"):
            self.assertIn(f"{label}: {label}_item x1", result.message)
        self.assertEqual(len(inventory_snapshot(bot)), 41)

    def test_sparse_inventory_and_equipment_only_are_compact_and_read_only(self):
        tool = item("minecraft:diamond_pickaxe", 1)
        bot = player({2: tool, 35: item("custom:keeps_namespace", 7)}, {"offhand": item("minecraft:shield", 1)})
        service, _ = self.service(bot)
        self.assertEqual(service.execute(["Alice", "inventory"]).message,
                         "Endbot: Alice: inventory: 3: diamond_pickaxe x1, "
                         "36: custom:keeps_namespace x7, offhand: shield x1")
        snapshot = inventory_snapshot(bot)
        tool.amount = 2
        self.assertEqual(snapshot[0]["count"], 1)
        bot = player(equipment={"head": item("minecraft:iron_helmet", 1)})
        service, _ = self.service(bot)
        self.assertEqual(service.execute(["Alice", "inventory"]).message,
                         "Endbot: Alice: inventory: head: iron_helmet x1")

    def test_missing_human_or_replaced_bot_cannot_be_inspected(self):
        for bot in (None, SimpleNamespace(name="Alice", xuid="123"), SimpleNamespace(name="Bob", xuid="")):
            service, _ = self.service(bot)
            self.assertIn("inventory failed: Bot is not present in the world",
                          service.execute(["Alice", "inventory"]).message)

    def test_group_identity_pin_resolves_uuid_on_server(self):
        service, server = self.service(player())
        identity = "00000000-0000-4000-8000-000000000001"
        service.world.inventory("Alice", identity)
        server.get_player.assert_called_once_with(UUID(identity))

    def test_a_named_bot_is_pinned_to_its_runtime_profile_uuid(self):
        identity = "00000000-0000-4000-8000-000000000002"
        server = SimpleNamespace(get_player=Mock(return_value=player()))
        control = SimpleNamespace(request=Mock(return_value={"name": "Alice", "identityId": identity}))
        service = PlainCommandService(control, EndstoneWorld(server))
        self.assertIn("inventory is empty", service.execute(["Alice", "inventory"]).message)
        control.request.assert_called_once_with("status", name="Alice")
        server.get_player.assert_called_once_with(UUID(identity))

    def test_japanese_inventory_labels_and_empty_message(self):
        source = SimpleNamespace(language="ja")
        service, _ = self.service(player(equipment={"head": item("minecraft:iron_helmet", 1)}))
        self.assertEqual(service.execute(["Alice", "inventory"], source).message,
                         "Endbot: Alice: 持ち物: 頭: iron_helmet x1")
        service, _ = self.service(player())
        self.assertEqual(service.execute(["Alice", "inventory"], source).message, "Endbot: Alice: 持ち物は空です")
