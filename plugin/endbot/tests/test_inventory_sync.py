import json
import unittest
from types import SimpleNamespace

from endstone_endbot.inventory_sync import (
    EVENT_NAME,
    PACKET_ID,
    HotbarPublisher,
    hotbar_snapshot,
    publish_hotbar,
    sync_settings,
    typed_nbt,
)


def tag(kind, value):
    return type(kind, (), {"value": value})()


class CompoundTag(dict):
    pass


class ListTag(list):
    pass


class ByteArrayTag(list):
    pass


def string_at(payload, offset=0):
    size, shift = 0, 0
    while True:
        value = payload[offset]
        offset += 1
        size |= (value & 127) << shift
        if value < 128:
            break
        shift += 7
    return payload[offset : offset + size].decode(), offset + size


class InventorySyncTests(unittest.TestCase):
    def test_changes_send_immediately_and_unchanged_state_is_resent_for_rejected_actions(self):
        from unittest.mock import Mock

        item = SimpleNamespace(type=SimpleNamespace(id="minecraft:apple"), amount=5, data=0, nbt=CompoundTag())
        player = SimpleNamespace(
            unique_id="bot",
            inventory=SimpleNamespace(get_item=lambda slot: item if slot == 0 else None),
            send_packet=Mock(),
        )
        server = SimpleNamespace(create_block_data=Mock(side_effect=ValueError("not a block")))
        publisher = HotbarPublisher(20)
        self.assertTrue(publisher.publish(player, server, 4))
        for tick in range(8, 24, 4):
            self.assertFalse(publisher.publish(player, server, tick))
        self.assertTrue(publisher.publish(player, server, 24))
        item.amount = 4
        self.assertTrue(publisher.publish(player, server, 28))
        self.assertEqual(player.send_packet.call_count, 3)
        server.create_block_data.assert_called_once_with("minecraft:apple")
        publisher.retain(set())
        self.assertEqual(publisher.previous, {})
        self.assertTrue(publisher.publish(player, server, 32))

    def test_sync_settings_validate_manual_plugin_installs(self):
        self.assertEqual(sync_settings({}), (4, 20))
        self.assertEqual(sync_settings({"hotbar-sync-interval-ticks": 10}), (10, 20))
        for key, maximum in (("hotbar-sync-interval-ticks", 1200), ("hotbar-sync-resend-ticks", 72000)):
            self.assertIn(maximum, sync_settings({key: maximum}))
            for value in (0, maximum + 1, True, "4", 4.5):
                with self.subTest(key=key, value=value), self.assertRaisesRegex(ValueError, key):
                    sync_settings({key: value})

    def test_snapshot_is_read_only_and_packet_preserves_unicode_nbt_and_counts(self):
        nbt = CompoundTag(
            {
                "display": CompoundTag({"Name": tag("StringTag", "剣")}),
                "map_uuid": tag("LongTag", 123456789123456789),
                "ench": ListTag([CompoundTag({"id": tag("ShortTag", 9), "lvl": tag("ShortTag", 2)})]),
                "bytes": ByteArrayTag([1, 255]),
            }
        )
        item = SimpleNamespace(type=SimpleNamespace(id="minecraft:diamond_sword"), amount=1, data=4, nbt=nbt)
        slots = [None, item, *([None] * 7)]
        packets = []

        def non_block(name):
            raise ValueError("not a block")

        player = SimpleNamespace(
            unique_id="bot-id",
            inventory=SimpleNamespace(get_item=lambda slot: slots[slot]),
            send_packet=lambda *packet: packets.append(packet),
        )
        server = SimpleNamespace(create_block_data=non_block)
        publish_hotbar(player, server)
        packet_id, payload = packets[0]
        name, offset = string_at(payload)
        data, end = string_at(payload, offset)
        self.assertEqual((packet_id, name, end), (PACKET_ID, EVENT_NAME, len(payload)))
        snapshot = json.loads(data)
        self.assertEqual(snapshot["identityId"], "bot-id")
        self.assertEqual(snapshot["slots"][1]["nbt"]["value"]["map_uuid"]["value"], "123456789123456789")
        self.assertEqual(snapshot["slots"][1]["nbt"]["value"]["bytes"]["value"], [1, -1])
        self.assertEqual(snapshot["slots"][1]["metadata"], 4)
        self.assertEqual(slots[1].amount, 1)
        self.assertIs(slots[1].nbt, nbt)

    def test_block_palette_is_read_from_server_and_all_nine_slots_are_present(self):
        item = SimpleNamespace(type=SimpleNamespace(id="minecraft:dirt"), amount=6, data=0, nbt=CompoundTag())
        player = SimpleNamespace(
            unique_id="bot-id", inventory=SimpleNamespace(get_item=lambda slot: item if slot == 8 else None)
        )
        server = SimpleNamespace(create_block_data=lambda name: SimpleNamespace(runtime_id=941))
        snapshot = hotbar_snapshot(player, server)
        self.assertEqual(snapshot["slots"][8]["blockRuntimeId"], 941)
        self.assertEqual(snapshot["slots"][:8], [None] * 8)

    def test_unknown_nbt_is_refused_instead_of_discarding_item_data(self):
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            typed_nbt(object())
