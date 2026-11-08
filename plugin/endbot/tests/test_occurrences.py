"""endbotd's event fan-out (BOT-022, EVENTS.md E4): interest set, filters, limits and gaps."""

import unittest

from endbot_core import areas
from endbot_core.i18n import LocalizedError
from endbotd.occurrences import OccurrenceHub

KINDS = {
    "block.change": {
        "source": "plugin", "capability": "read.world", "visibility": "public",
        "filters": {"area": {"type": "area", "maxBlocks": 512},
                    "dimension": {"type": "dimension", "default": "overworld"},
                    "from": {"type": "block"}, "to": {"type": "block"}},
        "require": [["area"]], "payload": {},
        "guarantee": {"class": "persist", "ticks": 4, "each": [], "record": "EVENTS.md#target"},
    },
    "bot.online": {
        "source": "runtime", "capability": "read.world", "visibility": "public",
        "filters": {"bot": {"type": "bot"}}, "require": [], "payload": {},
        "guarantee": {"class": "each", "ticks": None, "each": [], "record": "EVENTS.md#target"},
    },
}


def box(*corners):
    return areas.parse_area(" ".join(["within", *map(str, corners)]))


def change(x, y, z, **extra):
    return {"kind": "block.change", "x": x, "y": y, "z": z, "dimension": "overworld", **extra}


class OccurrenceHubTests(unittest.TestCase):
    def setUp(self):
        self.sent = []
        self.logs = []
        self.hub = OccurrenceHub(KINDS, self.sent.append, max_watched_blocks=600,
                                 log=lambda level, message: self.logs.append(message))

    def subscribe(self, kind="block.change", filter_=None, items=None, gaps=None):
        items = [] if items is None else items
        gaps = [] if gaps is None else gaps
        subscription = self.hub.subscribe(kind, {"area": box(0, 64, 0, 1, 64, 1)} if filter_ is None else filter_,
                                          items.append, gaps.append)
        return subscription, items, gaps

    def test_the_interest_set_is_the_union_and_follows_every_change(self):
        first, _, _ = self.subscribe()
        self.assertEqual(self.sent, [])  # no plugin linked: nothing to send, the link gets the set
        self.hub.link_changed("epoch-1")
        second, _, _ = self.subscribe(filter_={"area": box(1, 64, 1, 2, 64, 1)})
        self.assertEqual([interest["revision"] for interest in self.sent], [1, 2])
        self.assertEqual(self.sent[-1]["kinds"], ["block.change"])
        self.assertEqual(self.sent[-1]["block"], [{"dimension": "overworld", "cells": [
            [0, 64, 0], [0, 64, 1], [1, 64, 0], [1, 64, 1], [2, 64, 1]]}])
        self.hub.unsubscribe(first)
        self.hub.unsubscribe(first)  # a second unsubscribe changes nothing
        self.assertEqual(self.sent[-1]["block"][0]["cells"], [[1, 64, 1], [2, 64, 1]])
        self.hub.unsubscribe(second)
        self.assertEqual(self.sent[-1], {"revision": 4, "block": [], "kinds": []})
        # Runtime-sourced kinds never reach the plugin's set.
        self.subscribe("bot.online", {})
        self.assertEqual(len(self.sent), 4)

    def test_occurrences_reach_only_matching_subscriptions_as_copies(self):
        _, near, _ = self.subscribe(filter_={"area": box(0, 64, 0, 1, 64, 1), "to": "minecraft:air"})
        _, nether, _ = self.subscribe(filter_={"area": box(0, 64, 0, 1, 64, 1), "dimension": "nether"})
        delivered = self.hub.accept({"revision": 1, "epoch": "e", "tick": 5, "dropped": 0, "items": [
            change(0, 64, 0, to="minecraft:air"), change(0, 64, 0, to="minecraft:stone"),
            change(5, 64, 5, to="minecraft:air"), {"kind": "block.change", "x": "0", "y": 64, "z": 0},
        ]})
        self.assertEqual(delivered, 1)
        self.assertEqual(near, [change(0, 64, 0, to="minecraft:air")])
        near[0]["x"] = 99
        self.assertEqual(nether, [])
        self.hub.accept({"revision": 1, "epoch": "e", "tick": 6, "dropped": 0,
                         "items": [{**change(1, 64, 1), "dimension": "nether"}]})
        self.assertEqual(len(nether), 1)

    def test_unknown_or_misattributed_kinds_are_dropped_and_logged_once(self):
        _, items, _ = self.subscribe("bot.online", {})
        for _ in range(2):
            self.hub.accept({"revision": 1, "epoch": "e", "tick": 1, "dropped": 0,
                             "items": [{"kind": "bot.online", "bot": "A"}, {"kind": "nope"}]})
        self.assertEqual(items, [])
        self.assertEqual(len(self.logs), 2)
        self.assertEqual(self.hub.raise_occurrence("runtime", {"kind": "bot.online", "bot": "A"}), 1)

    def test_filters_are_bounded_absolute_and_counted_against_the_server_limit(self):
        with self.assertRaises(LocalizedError):
            self.hub.subscribe("nope", {}, print, print)
        with self.assertRaises(LocalizedError) as raised:
            self.subscribe(filter_={"area": box(0, 0, 0, 9, 9, 9)})  # 1000 cells over maxBlocks 512
        self.assertIn("512", str(raised.exception))
        with self.assertRaises(LocalizedError):
            self.subscribe(filter_={"area": areas.parse_area("within ~ ~ ~")})
        with self.assertRaises(LocalizedError):
            self.subscribe(filter_={"area": areas.parse_area("within 3")})  # around the Bot
        self.subscribe(filter_={"area": box(0, 0, 0, 7, 7, 7)})  # 512
        self.subscribe(filter_={"area": box(0, 0, 0, 7, 7, 7)})  # the same cells count once
        with self.assertRaises(LocalizedError) as raised:
            self.subscribe(filter_={"area": box(20, 0, 0, 27, 1, 7)})  # +128 > 600
        self.assertIn("600", str(raised.exception))
        self.assertEqual(self.hub.watched_blocks(), 512)
        _, items, _ = self.subscribe(filter_={"area": areas.parse_area("within 0 64 0 2 64 0 except 1 64 0")})
        self.hub.accept({"revision": 1, "epoch": "e", "tick": 1, "dropped": 0,
                         "items": [change(1, 64, 0), change(2, 64, 0)]})
        self.assertEqual(items, [change(2, 64, 0)])

    def test_area_and_required_filters_fail_closed(self):
        for filter_ in ({}, {"area": None}, {"to": "minecraft:air"}, {"area": "within 0 0 0"}):
            with self.subTest(filter=filter_), self.assertRaises(LocalizedError):
                self.subscribe(filter_=filter_)
        self.assertEqual(self.hub.watched_blocks(), 0)

    def test_untrusted_occurrence_fields_never_match_or_break_delivery(self):
        _, items, _ = self.subscribe(filter_={"area": areas.parse_area("within 3 of 0 64 0")})
        _, nether, _ = self.subscribe(filter_={"area": box(0, 64, 0, 1, 64, 1), "dimension": "nether"})
        self.hub.accept({"revision": 1, "epoch": "e", "tick": 1, "dropped": 0, "items": [
            {"kind": "block.change", "x": 10 ** 400, "y": 64, "z": 0, "dimension": "overworld"},
            {"kind": "block.change", "x": 0, "y": 64, "z": 0},  # no dimension: matches no dimension filter
            change(0, 64, 0),
        ]})
        self.assertEqual(items, [change(0, 64, 0)])
        self.assertEqual(nether, [])

    def test_a_failing_subscriber_does_not_starve_the_others(self):
        def broken(value):
            raise RuntimeError("subscriber bug")

        self.hub.subscribe("block.change", {"area": box(0, 64, 0, 1, 64, 1)}, broken, broken)
        _, items, gaps = self.subscribe()
        self.assertEqual(self.hub.accept({"revision": 1, "epoch": "e", "tick": 1, "dropped": 0,
                                          "items": [change(0, 64, 0)]}), 1)
        self.hub.gap("plugin", "source restarted")
        self.assertEqual((len(items), gaps), (1, ["source restarted"]))
        self.assertTrue(any("subscriber failed" in message for message in self.logs))

    def test_unknown_kinds_are_remembered_only_up_to_a_bound(self):
        for index in range(100):
            self.hub.raise_occurrence("plugin", {"kind": f"made.up{index}" + "x" * 500})
        self.assertLessEqual(len(self.logs), 32)
        self.assertTrue(all(len(message) < 200 for message in self.logs))

    def test_sets_are_sent_in_order_after_the_gap_and_a_late_close_is_ignored(self):
        _, _, gaps = self.subscribe()
        opened = []

        def on_gap(reason):
            opened.append(self.subscribe(filter_={"area": box(5, 64, 5, 5, 64, 5)})[0])

        self.hub.subscribe("block.change", {"area": box(0, 64, 0, 0, 64, 0)}, print, on_gap)
        self.hub.link_changed("epoch-1")
        self.hub.link_changed(None, "epoch-1")
        self.hub.link_changed("epoch-2")
        revisions = [interest["revision"] for interest in self.sent]
        self.assertEqual(revisions, sorted(revisions))
        self.assertIn([5, 64, 5], self.sent[-1]["block"][0]["cells"])  # the watch the gap opened is in the last set
        before = len(gaps)
        self.hub.link_changed(None, "epoch-1")  # the old link's close arrives after the new link: no gap
        self.assertEqual(len(gaps), before)

    def test_a_kind_the_operator_has_not_turned_on_is_refused_with_the_setting_named(self):
        hub = OccurrenceHub(KINDS, self.sent.append, turned_off=("bot.online",))
        with self.assertRaisesRegex(LocalizedError, r"turned off on this server.*\[events\]"):
            hub.subscribe("bot.online", {}, print, print)

    def test_gaps_reach_subscriptions_of_their_source_once_per_cause(self):
        _, _, block_gaps = self.subscribe()
        _, _, bot_gaps = self.subscribe("bot.online", {})
        self.hub.link_changed("epoch-1")
        self.assertEqual(block_gaps, [])  # the first link is no gap
        self.assertEqual(self.sent[-1]["revision"], 1)  # but it gets the set
        self.hub.link_changed(None)
        self.hub.link_changed("epoch-2")
        self.assertEqual(block_gaps, ["source restarted"])
        self.hub.link_changed("epoch-3")  # replaced without a drop
        self.hub.accept({"revision": 1, "epoch": "epoch-3", "tick": 1, "dropped": 3, "items": []})
        self.assertEqual(block_gaps, ["source restarted", "source restarted", "occurrences dropped"])
        self.hub.gap("runtime", "source restarted")
        self.assertEqual(bot_gaps, ["source restarted"])
        self.assertEqual(len(block_gaps), 3)


if __name__ == "__main__":
    unittest.main()
