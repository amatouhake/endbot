"""Offline probe plumbing tests; these cannot establish BDS acceptance."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.inventory_stack_probe import EQUIPMENT, exercise_inventory_stack


class InventoryStackProbeTests(unittest.TestCase):
    def run_probe(self, status=None, error=None):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)

            def wait(label, predicate, timeout):
                if label == "stack fixture setup":
                    fixture = json.loads((output / "stack-fixture.json").read_text())
                    state = {"id": fixture["id"], "slots": [None] * 36,
                             "equipment": dict.fromkeys(EQUIPMENT), "selected": 0, "drops": []}
                    for slot, name, count in fixture["items"]:
                        (state["slots"] if isinstance(slot, int) else state["equipment"])[slot] = {
                            "name": name, "count": count,
                        }
                    (output / "stack-state.json").write_text(json.dumps(state))
                else:
                    request = json.loads((output / "stack-send.json").read_text())
                    wire = {"id": request["id"], "received": []}
                    if error:
                        wire["error"] = error
                    else:
                        wire["sent"] = {"packet": {"requests": [{"request_id": -1}]}}
                        if status is not None and request["operation"] != "legacy-drop":
                            wire["received"] = [{"name": "item_stack_response", "packet": {
                                "responses": [{"request_id": -1, "status": status}],
                            }}]
                    (output / "stack-wire.json").write_text(json.dumps(wire))
                self.assertTrue(predicate(), label)

            report = {}
            with patch("scripts.inventory_stack_probe.time.sleep"):
                exercise_inventory_stack(output, lambda _: None, lambda _: None, wait, report)
            persisted = json.loads((output / "inventory-stack-results.json").read_text())
            self.assertEqual(persisted, report["inventory_stack"])
            self.assertEqual(len(persisted), 16)
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
