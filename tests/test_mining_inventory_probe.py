"""Offline product-command probe checks; BDS acceptance remains a live gate."""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.mining_probe import (
    EQUIPMENT,
    exercise_product_drops,
    exercise_product_inventory,
    expected_product_drop,
    expected_product_inventory,
    product_drop_cases,
    product_drop_matches,
    product_inventory_cases,
)


def fixture(items, identity="case"):
    state = {"id": identity, "slots": [None] * 36, "equipment": dict.fromkeys(EQUIPMENT),
             "selected": 0, "dropped": []}
    for label, name, count in items:
        container, key = (state["equipment"], label) if label in EQUIPMENT else (state["slots"], int(label) - 1)
        container[key] = {"name": name, "count": count, "nbt": "{custom:1L}"}
    return state


class MiningInventoryProbeTests(unittest.TestCase):
    def test_product_inventory_cases_cover_swaps_hotbar_priority_and_occupied_main_hold(self):
        cases = product_inventory_cases()
        self.assertEqual([case[1] for case in cases],
                         ["swap 10 2", "swap 1 36", "hold apple", "hold diamond_pickaxe"])
        for label, _, items, pair, selected in cases:
            before = fixture(items, label)
            original = copy.deepcopy(before)
            expected = expected_product_inventory(before, pair, selected)
            self.assertEqual(before, original)
            self.assertEqual(expected["equipment"], before["equipment"])
            self.assertEqual(expected["selected"], selected)
            self.assertTrue(product_drop_matches(expected, expected, []))
            self.assertFalse(product_drop_matches(before, expected, []))
            if pair:
                a, b = (slot - 1 for slot in pair)
                self.assertEqual(expected["slots"][a], before["slots"][b])
                self.assertEqual(expected["slots"][b], before["slots"][a])
            if label == "hold-main-occupied":
                self.assertEqual(expected["slots"][13]["name"], "minecraft:dirt")
                self.assertEqual(expected["slots"][0]["nbt"], "{custom:1L}")

    def run_inventory_probe(self, reject=False):
        current, commands, controls, requested = {}, [], [], {}
        cases = {f"product-inventory-{label}": (pair, selected)
                 for label, _, _, pair, selected in product_inventory_cases()}

        def request(data):
            requested.clear()
            requested.update(data)

        def wait(label, predicate, timeout):
            if label == "product inventory fixture setup":
                current.clear()
                current.update(fixture(requested["product_items"], requested["id"]))
            elif label == "product inventory authoritative state" and not reject:
                pair, selected = cases[current["id"]]
                current.update(expected_product_inventory(current, pair, selected))
            if not predicate():
                raise RuntimeError("BDS state did not match")

        with tempfile.TemporaryDirectory() as temporary, patch("scripts.mining_probe.time.sleep"):
            output, report = Path(temporary), {}
            if reject:
                with self.assertRaises(RuntimeError):
                    exercise_product_inventory(output, commands.append, controls.append, wait, report, request,
                                               lambda: current)
            else:
                exercise_product_inventory(output, commands.append, controls.append, wait, report, request,
                                           lambda: current)
            persisted = json.loads((output / "product-inventory-results.json").read_text(encoding="utf-8"))
            self.assertEqual(persisted, report["product_inventory"])
            return commands, controls, persisted

    def test_product_inventory_runner_records_commands_and_authoritative_evidence(self):
        commands, controls, records = self.run_inventory_probe()
        self.assertEqual(commands, [command for _, args, _, _, _ in product_inventory_cases()
                                    for command in ("bot RakNetSpike hotbar 1", f"bot RakNetSpike {args}")])
        self.assertEqual(controls, ["stop"] * 4)
        self.assertTrue(all(record["outcome"] == "accepted" for record in records.values()))
        self.assertEqual(records["hold-hotbar"]["after"]["selected"], 2)

    def test_product_inventory_runner_preserves_inconclusive_unchanged_evidence(self):
        _, _, records = self.run_inventory_probe(reject=True)
        self.assertEqual(records["swap-main-hotbar"]["outcome"], "inconclusive")
        self.assertEqual(records["swap-main-hotbar"]["after"], records["swap-main-hotbar"]["before"])

    def test_cases_cover_native_slot_item_count_equipment_and_every_slot(self):
        cases = {label: (arguments, items, requested) for label, arguments, items, requested in product_drop_cases()}
        self.assertEqual(cases["main-slot"][0], "12")
        self.assertEqual(cases["item-count"][0], "stone 7")
        self.assertEqual(cases["item-count"][2], {"2": 3, "12": 4})
        self.assertEqual(cases["item-shortfall"][0], "stone 99")
        self.assertEqual(cases["offhand"][0], "offhand")
        self.assertEqual(cases["head"][0], "head")
        self.assertEqual(cases["all"][0], "all")
        self.assertEqual(len(cases["all"][1]), 41)
        self.assertEqual(set(cases["all"][2]), set(map(str, range(1, 37))) | set(EQUIPMENT))

    def test_expected_partial_drop_preserves_nbt_untouched_slots_and_selected_slot(self):
        before = fixture([("2", "minecraft:stone", 3), ("12", "minecraft:stone", 6),
                          ("36", "minecraft:dirt", 2), ("head", "minecraft:iron_helmet", 1)])
        original = copy.deepcopy(before)
        expected, drops = expected_product_drop(before, {"2": 3, "12": 4})
        self.assertIsNone(expected["slots"][1])
        self.assertEqual(expected["slots"][11]["count"], 2)
        self.assertEqual(expected["slots"][11]["nbt"], "{custom:1L}")
        self.assertEqual(expected["slots"][35], before["slots"][35])
        self.assertEqual(expected["equipment"], before["equipment"])
        self.assertEqual([item["count"] for item in drops], [3, 4])
        self.assertEqual(before, original)
        for requested in ({"12": 7}, {"12": 0}, {"1": 1}):
            with self.assertRaises(ValueError):
                expected_product_drop(before, requested)

    def test_evaluator_requires_exact_authoritative_slots_equipment_nbt_and_ejections(self):
        before = fixture([("12", "minecraft:stone", 5), ("offhand", "minecraft:shield", 1),
                          ("head", "minecraft:iron_helmet", 1)])
        expected, drops = expected_product_drop(before, {"12": 5, "offhand": 1, "head": 1})
        accepted = {**expected, "dropped": drops}
        self.assertTrue(product_drop_matches(accepted, expected, drops))
        split = copy.deepcopy(accepted)
        split["dropped"] = [{**drops[0], "count": 2}, {**drops[0], "count": 3}, *drops[1:]]
        self.assertTrue(product_drop_matches(split, expected, drops))
        self.assertFalse(product_drop_matches(before, expected, drops))
        for field, value in (("id", "stale-fixture"), ("selected", 1), ("dropped", drops[:-1]),
                             ("equipment", before["equipment"]), ("slots", before["slots"])):
            self.assertFalse(product_drop_matches({**accepted, field: value}, expected, drops), field)
        changed = copy.deepcopy(accepted)
        changed["dropped"][0]["nbt"] = "{}"
        self.assertFalse(product_drop_matches(changed, expected, drops))
        changed = copy.deepcopy(accepted)
        changed["dropped"][0]["metadata"] = 7
        self.assertFalse(product_drop_matches(changed, expected, drops))
        self.assertFalse(product_drop_matches({**accepted, "dropped": [*drops, drops[0]]}, expected, drops))

    def run_product_probe(self, reject=False):
        current, commands, controls, request_data = {}, [], [], {}
        cases = {f"product-drop-{label}": requested for label, _, _, requested in product_drop_cases()}

        def request(data):
            request_data.clear()
            request_data.update(data)

        def state():
            return current

        def wait(label, predicate, timeout):
            if label == "product drop fixture setup":
                current.clear()
                current.update(fixture(request_data["product_items"], request_data["id"]))
            elif not reject:
                expected, drops = expected_product_drop(current, cases[current["id"]])
                current.update(expected)
                current["dropped"] = drops
            if not predicate():
                raise RuntimeError("BDS state did not match")

        with tempfile.TemporaryDirectory() as temporary, patch("time.sleep"):
            report, output = {}, Path(temporary)
            if reject:
                with self.assertRaises(RuntimeError):
                    exercise_product_drops(output, commands.append, controls.append, wait, report, request, state)
            else:
                exercise_product_drops(output, commands.append, controls.append, wait, report, request, state)
            persisted = json.loads((output / "product-drop-results.json").read_text(encoding="utf-8"))
            self.assertEqual(persisted, report["product_drops"])
            return commands, controls, persisted

    def test_runner_issues_product_commands_and_persists_independent_bds_evidence(self):
        commands, controls, records = self.run_product_probe()
        self.assertEqual(commands, [f"bot RakNetSpike drop {args}" for _, args, _, _ in product_drop_cases()])
        self.assertEqual(controls, ["stop"] * len(commands))
        self.assertTrue(all(record["outcome"] == "accepted" for record in records.values()))
        self.assertIn("head", records)
        self.assertIsNotNone(records["head"]["before"]["equipment"]["head"])
        self.assertIsNone(records["head"]["after"]["equipment"]["head"])

    def test_runner_never_calls_unchanged_bds_state_accepted(self):
        _, _, records = self.run_product_probe(reject=True)
        self.assertEqual(records["main-slot"]["outcome"], "inconclusive")
        self.assertIn("after", records["main-slot"])
