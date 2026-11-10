"""The stored triggers (BOT-022 5a, EVENTS E6); synthetic identities only."""

import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from endbot_core.trigger_store import (
    DEFAULT_POLICY,
    Trigger,
    TriggerStore,
    load_triggers,
    policy_from,
)

OWNER = "xuid:2535400000000001"
OTHER_OWNER = "player:0000000a-0000-4000-8000-00000000000b"
PLAYER = "0000000a-0000-4000-8000-00000000000b"
TRIGGER_FIELDS = {
    "name", "owner", "ownerName", "visibility", "kind", "filter", "script", "arguments",
    "approved", "status", "reason", "policy", "stats",
}


def trigger(**changes) -> Trigger:
    values = {
        "name": "digger",
        "owner": OWNER,
        "owner_name": "Alice",
        "visibility": "private",
        "kind": "block.change",
        "filter": {"to": "minecraft:air", "area": {"x": 1, "y": 2, "z": 3}},
        "script": "digger",
        "arguments": ["--fast"],
        "approved": {"revision": "rev-1", "declaration": ["act.bot"], "paramsHash": "hash-1"},
        "status": "armed",
        "reason": None,
        "policy": dict(DEFAULT_POLICY),
        "stats": {
            "starts": 2,
            "missed": 1,
            "skipped": 0,
            "lastStart": 1700000000.5,
            "lastGap": 0.25,
            "lastError": None,
        },
    }
    values.update(changes)
    return Trigger(**values)


def trigger_row(**changes) -> dict:
    values = {
        "name": "digger",
        "owner": OWNER,
        "ownerName": "Alice",
        "visibility": "private",
        "kind": "block.change",
        "filter": {"to": "minecraft:air"},
        "script": "digger",
        "arguments": ["--fast"],
        "approved": {"revision": "rev-1", "declaration": ["act.bot"], "paramsHash": "hash-1"},
        "status": "armed",
        "reason": None,
        "policy": dict(DEFAULT_POLICY),
        "stats": {
            "starts": 2,
            "missed": 1,
            "skipped": 0,
            "lastStart": 1700000000.5,
            "lastGap": 0.25,
            "lastError": None,
        },
    }
    values.update(changes)
    return values


def document(*rows) -> dict:
    return {"schemaVersion": 1, "triggers": list(rows)}


class TriggerStoreTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.path = self.root / "state" / "triggers.json"
        self.store = TriggerStore(self.path)

    def write_document(self, value) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(value), encoding="utf-8")

    def test_round_trip_uses_camel_case_sorted_keys_and_lf(self):
        first = trigger(
            name="beta",
            owner_name="Alice ☃",
            filter={"to": "雪", "nested": [1, 2.5, None, True]},
            status="paused",
            reason="declaration grew",
            stats={
                "starts": 0,
                "missed": 0,
                "skipped": 0,
                "lastStart": None,
                "lastGap": None,
                "lastError": "last run failed",
            },
        )
        second = trigger(name="alpha", owner=OTHER_OWNER, owner_name=None)
        third = trigger(name="alpha")
        for entry in (first, second, third):
            self.store.add(entry)
        self.assertEqual(
            [(entry.owner, entry.name) for entry in self.store.list()],
            [(OTHER_OWNER, "alpha"), (OWNER, "alpha"), (OWNER, "beta")],
        )

        raw = self.path.read_bytes()
        self.assertNotIn(b"\r\n", raw)
        self.assertTrue(raw.endswith(b"\n"))
        text = raw.decode("utf-8")
        self.assertIn("雪", text)  # ensure_ascii=False
        file = json.loads(text)
        self.assertEqual(set(file), {"schemaVersion", "triggers"})
        self.assertEqual(file["schemaVersion"], 1)
        self.assertEqual([row["name"] for row in file["triggers"]], ["alpha", "alpha", "beta"])
        for row in file["triggers"]:
            self.assertEqual(set(row), TRIGGER_FIELDS)
            self.assertIn("ownerName", row)
            self.assertIn("lastStart", row["stats"])
            self.assertNotIn("owner_name", row)
        self.assertEqual(file["triggers"][2]["filter"], {"to": "雪", "nested": [1, 2.5, None, True]})
        self.assertEqual(file["triggers"][2]["ownerName"], "Alice ☃")
        self.assertEqual(file["triggers"][2]["status"], "paused")
        self.assertEqual(file["triggers"][2]["reason"], "declaration grew")
        self.assertEqual(file["triggers"][2]["stats"]["lastError"], "last run failed")

        self.assertEqual(load_triggers(self.path), self.store.list())
        self.assertEqual(TriggerStore(self.path).list(), self.store.list())
        self.assertEqual(list(self.path.parent.glob(".triggers-*")), [])

    def test_missing_file_is_an_empty_store(self):
        self.assertEqual(load_triggers(self.path), [])
        self.assertEqual(self.store.list(), [])
        self.assertFalse(self.path.exists())

    def test_add_get_duplicate_and_count_armed(self):
        self.store.add(trigger(name="one"))
        self.store.add(trigger(name="two", status="paused", reason="kind unavailable"))
        self.store.add(trigger(name="three", owner=OTHER_OWNER))
        self.assertEqual(self.store.count_armed(OWNER), 1)
        self.assertEqual(self.store.count_armed(OTHER_OWNER), 1)
        self.assertEqual(self.store.count_armed("console"), 0)
        self.assertEqual(self.store.get(OWNER, "two").status, "paused")
        self.assertIsNone(self.store.get(OWNER, "missing"))
        with self.assertRaises(ValueError):
            self.store.add(trigger(name="one"))
        self.store.add(trigger(name="one", owner=OTHER_OWNER))

    def test_a_memory_only_update_reaches_the_file_on_flush_or_the_next_durable_change(self):
        self.store.add(trigger(name="one"))
        base = self.store.get(OWNER, "one").stats
        self.store.update(OWNER, "one", durable=False, stats={**base, "starts": 3})
        self.assertEqual(self.store.get(OWNER, "one").stats["starts"], 3)
        self.assertNotEqual(load_triggers(self.path)[0].stats.get("starts"), 3)
        self.store.flush()
        self.assertEqual(load_triggers(self.path)[0].stats["starts"], 3)
        self.store.update(OWNER, "one", durable=False, stats={**base, "starts": 4})
        self.store.update(OWNER, "one", status="paused", reason="declaration grew")
        stored = load_triggers(self.path)[0]
        self.assertEqual((stored.status, stored.stats["starts"]), ("paused", 4))
        with patch.object(self.store, "_save") as save:
            self.store.flush()  # nothing changed since: no write
        save.assert_not_called()

    def test_replace_requires_an_existing_trigger(self):
        with self.assertRaises(ValueError):
            self.store.replace(trigger(name="missing"))
        self.store.add(trigger())
        self.store.replace(trigger(stats={**trigger().stats, "starts": 9}))
        self.assertEqual(self.store.get(OWNER, "digger").stats["starts"], 9)
        self.assertEqual(TriggerStore(self.path).get(OWNER, "digger").stats["starts"], 9)

    def test_remove_returns_whether_it_removed(self):
        self.store.add(trigger())
        self.assertTrue(self.store.remove(OWNER, "digger"))
        self.assertFalse(self.store.remove(OWNER, "digger"))
        self.assertEqual(self.store.list(), [])
        self.assertEqual(load_triggers(self.path), [])

    def test_update_returns_the_new_trigger_and_can_rename(self):
        self.store.add(trigger())
        updated = self.store.update(
            OWNER, "digger", status="disabled", reason="owner revoked", policy={**DEFAULT_POLICY, "cooldown": 0}
        )
        self.assertEqual(updated.status, "disabled")
        self.assertEqual(updated.reason, "owner revoked")
        self.assertEqual(self.store.get(OWNER, "digger"), updated)
        renamed = self.store.update(OWNER, "digger", name="miner")
        self.assertEqual(renamed.name, "miner")
        self.assertIsNone(self.store.get(OWNER, "digger"))
        self.assertEqual(TriggerStore(self.path).get(OWNER, "miner"), renamed)
        with self.assertRaises(ValueError):
            self.store.update(OWNER, "missing", status="paused")
        with self.assertRaises(ValueError):
            self.store.update(OWNER, "miner", name="bad name")
        self.assertEqual(self.store.get(OWNER, "miner"), renamed)

    def test_update_refuses_a_rename_onto_an_existing_trigger(self):
        self.store.add(trigger(name="one"))
        self.store.add(trigger(name="two"))
        self.store.add(trigger(name="three", owner=OTHER_OWNER))
        with self.assertRaises(ValueError):
            self.store.update(OWNER, "one", name="two")
        with self.assertRaises(ValueError):
            self.store.update(OWNER, "one", owner=OTHER_OWNER, name="three")
        self.assertEqual(self.store.get(OWNER, "one").name, "one")

    def test_every_validation_rule_has_a_failing_case(self):
        bad = {
            "name empty": {"name": ""},
            "name long": {"name": "x" * 33},
            "name characters": {"name": "bad name"},
            "name type": {"name": 1},
            "owner empty": {"owner": ""},
            "owner xuid empty": {"owner": "xuid:"},
            "owner xuid not digits": {"owner": "xuid:abc"},
            "owner player not uuid": {"owner": "player:not-a-uuid"},
            "owner prefix": {"owner": "uuid:1"},
            "owner long": {"owner": "xuid:" + "1" * 124},
            "owner type": {"owner": 1},
            "ownerName long": {"owner_name": "x" * 65},
            "ownerName type": {"owner_name": 1},
            "visibility": {"visibility": "public"},
            "visibility type": {"visibility": None},
            "kind shape": {"kind": "block"},
            "kind category": {"kind": "Block.change"},
            "kind action": {"kind": "block.Change"},
            "kind punctuation": {"kind": "block.bad-action"},
            "kind type": {"kind": 1},
            "filter type": {"filter": []},
            "filter key": {"filter": {1: "x"}},
            "filter value": {"filter": {"x": {1, 2}}},
            "filter nan": {"filter": {"x": math.nan}},
            "script empty": {"script": ""},
            "script long": {"script": "x" * 33},
            "script type": {"script": 1},
            "arguments type": {"arguments": "x"},
            "arguments count": {"arguments": ["x"] * 33},
            "arguments item type": {"arguments": [1]},
            "arguments item long": {"arguments": ["x" * 257]},
            "approved type": {"approved": []},
            "approved empty": {"approved": {}},
            "approved extra": {"approved": {**trigger().approved, "extra": 1}},
            "approved revision": {"approved": {**trigger().approved, "revision": 1}},
            "approved declaration type": {"approved": {**trigger().approved, "declaration": "act.bot"}},
            "approved declaration item": {"approved": {**trigger().approved, "declaration": [1]}},
            "approved hash": {"approved": {**trigger().approved, "paramsHash": 1}},
            "status": {"status": "on"},
            "status type": {"status": None},
            "reason long": {"reason": "x" * 129},
            "reason type": {"reason": 1},
            "reason while armed": {"reason": "why"},
            "policy type": {"policy": []},
            "policy empty": {"policy": {}},
            "policy extra": {"policy": {**DEFAULT_POLICY, "extra": 1}},
            "policy concurrency": {"policy": {**DEFAULT_POLICY, "concurrency": "serial"}},
            "policy cooldown low": {"policy": {**DEFAULT_POLICY, "cooldown": -1}},
            "policy cooldown high": {"policy": {**DEFAULT_POLICY, "cooldown": 72001}},
            "policy cooldown type": {"policy": {**DEFAULT_POLICY, "cooldown": "20"}},
            "policy cooldown bool": {"policy": {**DEFAULT_POLICY, "cooldown": True}},
            "policy minute low": {"policy": {**DEFAULT_POLICY, "startsPerMinute": 0}},
            "policy minute high": {"policy": {**DEFAULT_POLICY, "startsPerMinute": 61}},
            "policy queue low": {"policy": {**DEFAULT_POLICY, "queue": 0}},
            "policy queue high": {"policy": {**DEFAULT_POLICY, "queue": 17}},
            "stats type": {"stats": []},
            "stats empty": {"stats": {}},
            "stats extra": {"stats": {**trigger().stats, "extra": 0}},
            "stats starts": {"stats": {**trigger().stats, "starts": -1}},
            "stats starts bool": {"stats": {**trigger().stats, "starts": True}},
            "stats missed": {"stats": {**trigger().stats, "missed": -1}},
            "stats skipped": {"stats": {**trigger().stats, "skipped": -1}},
            "stats lastStart type": {"stats": {**trigger().stats, "lastStart": "now"}},
            "stats lastStart nan": {"stats": {**trigger().stats, "lastStart": math.nan}},
            "stats lastGap inf": {"stats": {**trigger().stats, "lastGap": math.inf}},
            "stats lastError long": {"stats": {**trigger().stats, "lastError": "x" * 257}},
            "stats lastError type": {"stats": {**trigger().stats, "lastError": 1}},
        }
        for label, changes in bad.items():
            with self.subTest(label=label), self.assertRaises(ValueError):
                self.store.add(trigger(**changes))
        self.assertEqual(self.store.list(), [])
        self.assertFalse(self.path.exists())

    def test_valid_console_owner_and_null_optional_values(self):
        self.store.add(
            trigger(
                owner="console",
                owner_name=None,
                visibility="shared",
                status="disabled",
                reason=None,
                arguments=[],
                stats={
                    "starts": 0,
                    "missed": 0,
                    "skipped": 0,
                    "lastStart": None,
                    "lastGap": None,
                    "lastError": None,
                },
            )
        )
        self.assertEqual(self.store.get("console", "digger").owner, "console")

    def test_file_validation_refuses_every_bad_document(self):
        valid = document()
        corrupt = {
            "empty": "",
            "not json": "{not json",
            "array": "[]",
            "schema version": json.dumps({**valid, "schemaVersion": 2}),
            "schema bool": json.dumps({**valid, "schemaVersion": True}),
            "schema string": json.dumps({**valid, "schemaVersion": "1"}),
            "schema missing": json.dumps({"triggers": []}),
            "triggers missing": json.dumps({"schemaVersion": 1}),
            "triggers object": json.dumps({**valid, "triggers": {}}),
            "unknown top field": json.dumps({**valid, "extra": True}),
            "row type": json.dumps({"schemaVersion": 1, "triggers": [1]}),
            "row not object": json.dumps({"schemaVersion": 1, "triggers": [None]}),
            "row unknown field": json.dumps(document({**trigger_row(), "extra": 1})),
            "row missing field": json.dumps(document({key: value for key, value in trigger_row().items()
                                                      if key != "reason"})),
            "row invalid field": json.dumps(document(trigger_row(status="on"))),
            "row invalid policy": json.dumps(document(trigger_row(policy={**DEFAULT_POLICY, "queue": 0}))),
            "duplicate": json.dumps(document(trigger_row(), trigger_row())),
        }
        for label, text in corrupt.items():
            with self.subTest(label=label):
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text(text, encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_triggers(self.path)
                with self.assertRaises(ValueError):
                    TriggerStore(self.path)

    def test_unknown_keys_in_a_trigger_are_refused(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text(
            json.dumps(document({**trigger_row(), "owner_name": "Alice"})), encoding="utf-8"
        )
        with self.assertRaises(ValueError):
            load_triggers(self.path)

    def test_symlink_and_directory_are_refused(self):
        self.path.parent.mkdir(parents=True)
        target = self.path.parent / "target.json"
        target.write_text(json.dumps(document()), encoding="utf-8")
        try:
            self.path.symlink_to(target)
        except (OSError, NotImplementedError):
            self.path.unlink(missing_ok=True)
            self.path.mkdir()
        with self.assertRaises(ValueError):
            load_triggers(self.path)
        with self.assertRaises(ValueError):
            TriggerStore(self.path)

    def test_a_failed_write_keeps_the_old_file_and_the_old_memory(self):
        self.store.add(trigger(name="one"))
        before = self.path.read_bytes()
        with (
            patch("endbot_core.trigger_store.os.replace", side_effect=OSError("disk failure")),
            self.assertRaises(OSError),
        ):
            self.store.add(trigger(name="two"))
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual([entry.name for entry in self.store.list()], ["one"])
        self.assertEqual([entry.name for entry in TriggerStore(self.path).list()], ["one"])
        self.assertEqual(list(self.path.parent.glob(".triggers-*")), [])

    def test_memory_only_store_writes_nothing(self):
        store = TriggerStore()
        store.add(trigger())
        self.assertEqual(store.get(OWNER, "digger").name, "digger")
        self.assertTrue(store.remove(OWNER, "digger"))
        self.assertEqual(list(self.root.rglob("*")), [])

    def test_policy_from_merges_a_partial_policy_and_names_bad_fields(self):
        self.assertEqual(policy_from({}), DEFAULT_POLICY)
        self.assertIsNot(policy_from({}), DEFAULT_POLICY)
        self.assertEqual(
            policy_from({"concurrency": "queue", "queue": 8}),
            {"concurrency": "queue", "cooldown": 20, "startsPerMinute": 10, "queue": 8},
        )
        cases = (
            ({"concurrency": "serial"}, "concurrency"),
            ({"cooldown": -1}, "cooldown"),
            ({"cooldown": 72001}, "cooldown"),
            ({"cooldown": "20"}, "cooldown"),
            ({"startsPerMinute": 0}, "startsPerMinute"),
            ({"startsPerMinute": 61}, "startsPerMinute"),
            ({"queue": 0}, "queue"),
            ({"queue": 17}, "queue"),
            ({"extra": 1}, "extra"),
        )
        for values, field in cases:
            with self.subTest(values=values):
                with self.assertRaises(ValueError) as raised:
                    policy_from(values)
                self.assertIn(field, str(raised.exception))
        for values, bounds in (("cooldown", ("0", "72000")), ("startsPerMinute", ("1", "60")),
                               ("queue", ("1", "16"))):
            with self.assertRaises(ValueError) as raised:
                policy_from({values: 99991})
            self.assertIn(values, str(raised.exception))
            self.assertIn(bounds[0], str(raised.exception))
            self.assertIn(bounds[1], str(raised.exception))
        with self.assertRaises(ValueError):
            policy_from(None)


if __name__ == "__main__":
    unittest.main()
