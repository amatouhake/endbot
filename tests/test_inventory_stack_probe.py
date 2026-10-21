"""Offline probe plumbing tests; these cannot establish BDS acceptance."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts.inventory_stack_probe import (
    EQUIPMENT,
    classify_inventory_case,
    exercise_inventory_stack,
    expected_inventory,
    inventory_stack_cases,
    probe_inventory_snapshot,
    reconnect_inventory_bot,
)


class InventoryStackProbeTests(unittest.TestCase):
    def run_probe(self, status=None, error=None, disconnect=False, disconnect_setup=False):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)

            def wait(label, predicate, timeout):
                if label == "stack fixture setup":
                    fixture = json.loads((output / "stack-fixture.json").read_text())
                    if disconnect_setup and fixture["id"] == "take-10-to-1":
                        connection[0] = "offline"
                        self.assertTrue(predicate())
                        return
                    state = {"id": fixture["id"], "slots": [None] * 36,
                             "equipment": dict.fromkeys(EQUIPMENT), "selected": 0, "drops": []}
                    for slot, name, count in fixture["items"]:
                        (state["slots"] if isinstance(slot, int) else state["equipment"])[slot] = {
                            "name": name, "count": count,
                        }
                    (output / "stack-state.json").write_text(json.dumps(state))
                else:
                    request = json.loads((output / "stack-send.json").read_text())
                    wire = {"id": request["id"], "received": [], "complete": True}
                    if error:
                        wire["error"] = error
                    else:
                        wire["sent"] = {"packet": {"requests": [{"request_id": -1}]}}
                        if status is not None and not request["operation"].startswith("legacy-"):
                            wire["received"] = [{"name": "item_stack_response", "packet": {
                                "responses": [{"request_id": -1, "status": status}],
                            }}]
                    (output / "stack-wire.json").write_text(json.dumps(wire))
                    if disconnect and request["operation"] == "drop" and request["source"]["container"] == "armor":
                        wire["disconnected"] = True
                        (output / "stack-wire.json").write_text(json.dumps(wire))
                        connection[0] = "offline"
                self.assertTrue(predicate(), label)

            report = {}
            connection = ["online"]
            reconnects = []

            def control(method):
                if method == "status":
                    return {"connectionState": connection[0]}
                if method == "reconnect":
                    reconnects.append(True)
                    connection[0] = "online"

            original_wait = wait

            def recovering_wait(label, predicate, timeout):
                if label == "inventory Bot reconnect":
                    self.assertTrue(predicate())
                else:
                    original_wait(label, predicate, timeout)

            with patch("scripts.inventory_stack_probe.time.sleep"):
                exercise_inventory_stack(output, lambda _: None, control, recovering_wait, report)
            persisted = json.loads((output / "inventory-stack-results.json").read_text())
            self.assertEqual(json.loads((output / "stack-fixture.json").read_text()), {"active": False})
            self.assertEqual(persisted, json.loads(json.dumps(report["inventory_stack"])))
            self.assertEqual(len(persisted), 31)
            self.assertEqual(len(reconnects), (4 if disconnect else 0) + int(disconnect_setup))
            return persisted

    def test_missing_native_stack_ids_are_not_sent(self):
        results = self.run_probe(error="Native stack ID unavailable")
        self.assertEqual({case["outcome"] for case in results.values()}, {"not-sent"})

    def test_silence_and_ok_without_inventory_change_are_inconclusive(self):
        for status in (None, "ok"):
            with self.subTest(status=status):
                results = self.run_probe(status=status)
                self.assertEqual({case["outcome"] for case in results.values()}, {"inconclusive"})

    def test_rejected_codes_remain_visible_and_legacy_requires_inventory_evidence(self):
        results = self.run_probe(status=5)
        for name, case in results.items():
            self.assertEqual(case["outcome"], "inconclusive" if name.startswith("legacy-") else "rejected")
            if not name.startswith("legacy-"):
                self.assertEqual(case["wire"]["received"][0]["packet"]["responses"][0]["status"], 5)

    def test_disconnects_are_recorded_and_reconnected_before_all_remaining_cases(self):
        results = self.run_probe(disconnect=True)
        self.assertEqual(len(results), 31)
        for result in list(results.values())[-4:]:
            self.assertEqual(result["outcome"], "inconclusive")
            self.assertTrue(result["wire"]["disconnected"])
            self.assertTrue(result["reconnected"])

    def test_disconnect_during_fixture_setup_is_inconclusive_and_next_case_runs(self):
        results = self.run_probe(disconnect_setup=True)
        first = results["take-10-to-1"]
        self.assertEqual(first["outcome"], "inconclusive")
        self.assertEqual(first["expected"], {})
        self.assertTrue(first["reconnected"])
        self.assertIn("fixture setup", first["wire"]["runnerError"])
        self.assertEqual(len(results), 31)

    def test_independent_open_and_legacy_cases_and_native_armor_last(self):
        cases = inventory_stack_cases()
        self.assertEqual(len({case["id"] for case in cases}), 31)
        opened = [case for case in cases if case["openInventory"]]
        self.assertEqual({case["id"] for case in opened}, {
            "open-take-10-to-1", "open-place-10-to-1", "open-swap-10-2",
            "open-drop-main-stack", "open-drop-main-partial", "open-drop-item_in_off_hand",
        })
        for case in opened:
            if case["destination"]:
                self.assertEqual(case["source"], {"container": "inventory", "slot": 9})
                self.assertEqual(case["destination"]["container"], "hotbar")
        legacy = [case for case in cases if case["operation"] == "legacy-drop"]
        self.assertEqual(len(legacy), 7)
        self.assertEqual({case["source"]["slot"] for case in legacy if case["source"]["container"] == "armor"}, {0, 1, 2, 3})
        self.assertTrue(all(case["operation"] == "drop" and case["source"]["container"] == "armor" for case in cases[-4:]))

    def test_four_legacy_exchanges_have_independent_exact_expectations(self):
        cases = [case for case in inventory_stack_cases() if case["operation"] == "legacy-swap"]
        self.assertEqual([(case["source"]["slot"], case["destination"]["slot"]) for case in cases],
                         [(9, 0), (9, 1), (0, 35), (9, 1)])
        self.assertEqual(cases[-1]["items"], [(9, "minecraft:stone", 5), (1, "minecraft:stone", 3)])
        for case in cases:
            before = {"id": case["id"], "slots": [None] * 36, "equipment": dict.fromkeys(EQUIPMENT),
                      "selected": 0, "drops": []}
            for slot, name, count in case["items"]:
                before["slots"][slot] = {"name": name, "count": count, "nbt": "exact"}
            expected = expected_inventory(before, case)
            wire = {"sent": {"name": "inventory_transaction", "packet": {}}, "received": []}
            self.assertEqual(classify_inventory_case(case, wire, expected, expected)["outcome"], "accepted")
            self.assertEqual(classify_inventory_case(case, wire, before, expected)["outcome"], "inconclusive")
            for event in ("packet_violation_warning", "disconnect", "kick", "close"):
                wire["received"] = [{"name": event, "packet": {}}]
                self.assertEqual(classify_inventory_case(case, wire, expected, expected)["outcome"], "inconclusive")

    def test_reconnect_waits_for_online_and_does_not_reconnect_online_bot(self):
        calls = []
        state = ["offline"]

        def control(method):
            calls.append(method)
            return {"connectionState": state[0]}

        def wait(label, predicate, timeout):
            self.assertEqual(label, "inventory Bot reconnect")
            self.assertEqual(timeout, 45)
            self.assertFalse(predicate())
            state[0] = "online"
            self.assertTrue(predicate())

        self.assertTrue(reconnect_inventory_bot(control, wait))
        self.assertEqual(calls.count("reconnect"), 1)
        self.assertFalse(reconnect_inventory_bot(control, wait))
        self.assertEqual(calls.count("reconnect"), 1)

    def test_full_fixture_observation_reuses_item_encoding_without_mutation(self):
        class CompoundTag(dict):
            pass

        item = SimpleNamespace(type=SimpleNamespace(id="minecraft:stone"), amount=5, data=7, nbt=CompoundTag())
        slots = [None] * 36
        slots[9] = slots[35] = item
        inventory = SimpleNamespace(get_item=slots.__getitem__, **dict.fromkeys(EQUIPMENT, item))
        player = SimpleNamespace(unique_id="probe-bot", inventory=inventory)
        server = SimpleNamespace(create_block_data=lambda _: SimpleNamespace(runtime_id=23))
        plugin_src = str(Path(__file__).resolve().parents[1] / "plugin/endbot/src")
        with patch.object(sys, "path", [plugin_src, *sys.path]):
            snapshot = probe_inventory_snapshot(player, server, {})
        self.assertEqual(len(snapshot["slots"]), 36)
        self.assertEqual(len(snapshot["equipment"]), 5)
        expected = {"name": "minecraft:stone", "count": 5, "metadata": 7, "blockRuntimeId": 23,
                    "nbt": {"type": "compound", "value": {}}}
        self.assertEqual(snapshot["slots"][9], expected)
        self.assertEqual(snapshot["slots"][35], expected)
        self.assertEqual(snapshot["equipment"], [expected] * 5)
        self.assertIsNone(snapshot["slots"][0])
        self.assertIs(slots[9], item)
        self.assertEqual(item.amount, 5)

    def test_expected_changes_preserve_full_item_data_and_selected_slot(self):
        for case in inventory_stack_cases():
            with self.subTest(case=case["id"]):
                before = {"id": case["id"], "slots": [None] * 36, "equipment": dict.fromkeys(EQUIPMENT),
                          "drops": [], "selected": 0,
                          "observation": {"identityId": "bot", "slots": [None] * 36, "equipment": [None] * 5}}
                for slot, name, count in case["items"]:
                    if isinstance(slot, int):
                        target, observed, index = before["slots"], before["observation"]["slots"], slot
                    else:
                        target, observed, index = before["equipment"], before["observation"]["equipment"], EQUIPMENT.index(slot)
                    target[slot] = {"name": name, "count": count}
                    observed[index] = {"name": name, "count": count, "metadata": 7, "nbt": {"typed": "value"}}
                original = json.loads(json.dumps(before))
                expected = expected_inventory(before, case)
                self.assertEqual(before, original)
                self.assertEqual(expected["selected"], 0)
                if case["destination"]:
                    self.assertEqual(expected["observation"]["slots"][case["destination"]["slot"]]["metadata"], 7)
                else:
                    self.assertEqual(sum(drop["count"] for drop in expected["drops"]), case["count"] or case["items"][0][2])
                    if case["count"]:
                        self.assertEqual(expected["observation"]["slots"][9]["count"], 4)
                    else:
                        self.assertFalse(any(expected["observation"]["slots"]))
                        self.assertFalse(any(expected["observation"]["equipment"]))


class InventoryClassificationTests(unittest.TestCase):
    def setUp(self):
        self.case = {"id": "drop", "operation": "drop"}
        self.expected = {"id": "drop", "slots": [None] * 36,
                         "equipment": dict.fromkeys(EQUIPMENT), "drops": [{"name": "minecraft:stone", "count": 5}],
                         "selected": 0}
        self.wire = {"sent": {"packet": {"requests": [{"request_id": -3}]}}, "received": []}

    def classify(self, status=None, *, matches=True, response_id=-3):
        if status is not None:
            self.wire["received"] = [{"name": "item_stack_response", "packet": {
                "responses": [{"request_id": response_id, "status": status}]}}]
        after = json.loads(json.dumps(self.expected))
        if not matches:
            after["drops"] = []
        return classify_inventory_case(self.case, self.wire, after, self.expected)["outcome"]

    def test_stack_acceptance_needs_matching_state_and_correlated_ok(self):
        self.assertEqual(self.classify(), "inconclusive")
        self.assertEqual(self.classify("ok", matches=False), "inconclusive")
        self.assertEqual(self.classify("ok", response_id=-99), "inconclusive")
        self.assertEqual(self.classify("ok"), "accepted")
        self.assertEqual(self.classify(0), "accepted")
        self.assertEqual(self.classify(49), "rejected")

    def test_legacy_needs_both_inventory_and_ejected_items_and_a_send(self):
        self.case["operation"] = "legacy-drop"
        self.assertEqual(self.classify(matches=False), "inconclusive")
        self.assertEqual(self.classify(), "accepted")
        after = json.loads(json.dumps(self.expected))
        after["slots"][9] = {"name": "minecraft:stone", "count": 5}
        self.assertEqual(classify_inventory_case(self.case, self.wire, after, self.expected)["outcome"], "inconclusive")
        self.expected["observation"] = {"metadata": 7}
        after = json.loads(json.dumps(self.expected))
        after["observation"]["metadata"] = 8
        self.assertEqual(classify_inventory_case(self.case, self.wire, after, self.expected)["outcome"], "inconclusive")
        self.wire.pop("sent")
        self.assertEqual(self.classify(), "inconclusive")

    def test_disconnect_or_stale_fixture_never_implies_acceptance(self):
        self.wire["disconnected"] = True
        self.assertEqual(self.classify("ok"), "inconclusive")
        self.wire.pop("disconnected")
        self.case["id"] = "other"
        self.assertEqual(self.classify("ok"), "inconclusive")

    def test_error_after_send_is_not_classified_as_not_sent(self):
        self.wire["error"] = "late error"
        self.assertEqual(self.classify(), "inconclusive")
        self.wire.pop("sent")
        self.assertEqual(self.classify(), "not-sent")
