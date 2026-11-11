"""Watches (BOT-022 step 4, EVENTS.md E5): filter validation, buffering, gaps, limits and the script host."""

import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

from endbot_core.events import outcome_text
from endbot_core.groups import GroupCommandService, GroupStore
from endbot_core.i18n import LocalizedError
from endbot_core.principal import Principal
from endbot_core.script_config import ScriptConfig
from endbot_core.script_inputs import OwnedInputs
from endbot_core.script_store import Script, ScriptStore
from endbot_core.scripting import ScriptService
from endbot_core.watches import BUFFER_LIMIT, MAX_WATCHES_PER_RUN, WatchBuffer, validate_filter
from endbotd.occurrences import OccurrenceHub
from test_command_service import FakeControl
from test_scripting import FakeRunner

AREA = "within 0 64 0 1 64 1"
OBSERVED = {"observe.kind": {
    "source": "runtime", "capability": "observe.chat", "visibility": "public", "filters": {}, "require": [],
    "payload": {}, "guarantee": {"class": "each", "ticks": None, "each": [], "record": "EVENTS.md#target"}}}


def change(x=0, y=64, z=0, **extra):
    return {"kind": "block.change", "x": x, "y": y, "z": z, "dimension": "overworld", "from": "minecraft:stone",
            "to": "minecraft:air", "cause": "poll", **extra}


class FilterTests(unittest.TestCase):
    def test_a_block_change_filter_is_canonical(self):
        kind, canonical = validate_filter("block.change", {"area": AREA, "to": "AIR", "from": "minecraft:stone",
                                                           "dimension": "nether"}, ())
        self.assertEqual(kind, "block.change")
        self.assertEqual((canonical["to"], canonical["from"], canonical["dimension"]),
                         ("minecraft:air", "minecraft:stone", "nether"))
        self.assertEqual(canonical["area"]["within"]["shape"], "box")

    def test_refusals_name_the_kind_field_or_capability(self):
        for kind, filter_, message in (
            ("block.nope", {"area": AREA}, "Unknown event kind block.nope"),
            (7, {}, "Unknown event kind 7"),
            ("block.change", {}, "needs a filter: area"),
            ("block.change", {"area": AREA, "colour": "red"}, "has no filter colour"),
            ("block.change", {"area": "0 64 0"}, "filter area must be an area"),
            ("block.change", {"area": "within 1 2"}, "filter area:"),
            ("block.change", {"area": AREA, "to": "not a block!"}, "filter to must be a block name"),
            ("block.change", {"area": AREA, "dimension": "moon"}, "filter dimension must be one of"),
            ("block.change", {"area": "within 0 64 0 3 67 3" + " except 100 100 100" * 17},
             "may remove at most 16 areas"),
            ("block.change", {"area": "within 0 64 0 3 67 3" + " except 100 100 100" * 60}, "too long"),
            ("block.change", ["area"], "must be a table"),
            ("block.change", {"area": "within ~ ~ ~"}, "needs absolute coordinates"),
            ("block.change", {"area": "within 3 of ~ ~-1 ~"}, "needs absolute coordinates"),
            ("block.change", {"area": "within 3"}, "needs absolute coordinates"),
            ("block.change", {"area": "within 0 64 0 3 67 3 except ~ ~ ~"}, "needs absolute coordinates"),
        ):
            with self.subTest(kind=kind, filter=filter_), self.assertRaises(LocalizedError) as raised:
                validate_filter(kind, filter_, ())
            self.assertIn(message, str(raised.exception))

    def test_a_declared_capability_is_needed_beyond_the_basic_set(self):
        with self.assertRaisesRegex(LocalizedError, r"needs observe.chat in requires"):
            validate_filter("observe.kind", {}, (), OBSERVED)
        self.assertEqual(validate_filter("observe.kind", None, ("observe.chat",), OBSERVED), ("observe.kind", {}))


class BufferTests(unittest.TestCase):
    def test_buffering_starts_at_creation_and_overflow_counts_missed_once(self):
        buffer = WatchBuffer("block.change", limit=3)
        for index in range(5):
            buffer.on_item(change(index))
        first = buffer.wait(0, lambda: None)
        self.assertEqual((first["ok"], first["data"]["x"], first["missed"]), (True, 2, 2))
        self.assertEqual(buffer.wait(0, lambda: None)["missed"], 0)
        self.assertEqual(buffer.wait(0, lambda: None)["data"]["x"], 4)
        self.assertEqual(buffer.wait(0, lambda: None), {"ok": False, "code": "timeout", "reason": "timeout",
                                                        "missed": 0})

    def test_a_gap_returns_once_then_waiting_continues(self):
        buffer = WatchBuffer("block.change")
        buffer.on_gap("source restarted")
        buffer.on_gap("source restarted")
        self.assertEqual(buffer.wait(0, lambda: None)["code"], "unavailable")
        threading.Timer(0.05, buffer.on_item, (change(),)).start()
        result = buffer.wait(2, lambda: None)
        self.assertEqual((result["ok"], result["reason"]), (True, "occurred"))

    def test_the_wait_is_pushed_and_alive_stops_it(self):
        buffer = WatchBuffer("block.change")
        started = time.monotonic()
        threading.Timer(0.05, buffer.on_item, (change(),)).start()
        self.assertTrue(buffer.wait(5, lambda: None)["ok"])
        self.assertLess(time.monotonic() - started, 1)

        def stopped():
            raise LocalizedError("Script stopped or time limit reached")

        with self.assertRaises(LocalizedError):
            buffer.wait(5, stopped)


class HostTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.config = ScriptConfig(enabled=True, wall_seconds=5)
        self.inputs = OwnedInputs(FakeControl())
        self.source = Principal("player", player_id="controller", name="Caller", grant_revision=1)
        groups = GroupStore(Path(self.directory.name) / "groups.json")
        self.service = GroupCommandService(self.inputs, None, groups, lambda source: True)
        self.service.limits = SimpleNamespace(max_watches_per_controller=32)
        self.store = ScriptStore(Path(self.directory.name) / "scripts.json", self.config)
        self.runner = FakeRunner([])
        self.host = ScriptService(self.config, self.store, self.service, self.inputs, Path(self.directory.name),
                                  lambda source: True, process_factory=lambda *args: self.runner,
                                  bootstrap="trusted")
        self.worker = ThreadPoolExecutor(max_workers=1)
        self.addCleanup(self.worker.shutdown)
        self.host.ordered = lambda callback: self.worker.submit(callback).result(timeout=3)
        self.host.notify = lambda *args, **kwargs: None
        self.host.publish = lambda event: self.ended.append(event) if event.kind == "run.ended" else None
        self.addCleanup(self.host.close)
        self.ended = []
        self.interests = []
        self.hub = OccurrenceHub(__import__("endbot_core.operations", fromlist=["EVENTS"]).EVENTS,
                                 self.interests.append, max_watched_blocks=600)
        self.hub.link_changed("epoch-1")
        self.host.occurrences = self.hub

    def outcome(self):
        return outcome_text(self.ended[-1].data, "en")

    def run_script(self, events, after_reply=None):
        self.runner = FakeRunner(events)
        self.runner.after_reply = after_reply
        self.host.start(Script("test", "params {}", "r1"), [], self.source)
        deadline = time.monotonic() + 5
        while self.host.active() and time.monotonic() < deadline:
            time.sleep(0.01)
        return self.runner.replies

    def test_a_run_watches_waits_and_its_watch_ends_with_it(self):
        def after(reply):
            if reply.get("id") == 2:  # the watch exists: an occurrence arrives before the wait
                self.hub.raise_occurrence("plugin", change())

        replies = self.run_script([
            {"type": "watch", "id": 2, "kind": "block.change", "filter": {"area": AREA, "to": "air"}},
            {"type": "watchWait", "id": 3, "watch": 1, "timeout": 40},
            {"type": "done"},
        ], after)
        watch = next(reply for reply in replies if reply["id"] == 2)
        self.assertTrue(watch["accepted"])
        waited = next(reply for reply in replies if reply["id"] == 3)
        self.assertEqual((waited["result"]["ok"], waited["result"]["data"]["x"]), (True, 0))
        self.assertEqual(self.interests[-1]["block"], [])  # the run ended: the watch was closed
        self.assertEqual(self.host._watch_owners, {})

    def test_the_wait_times_out_and_close_stops_delivery(self):
        replies = self.run_script([
            {"type": "watch", "id": 2, "kind": "block.change", "filter": {"area": AREA}},
            {"type": "watchWait", "id": 3, "watch": 1, "timeout": 2},
            {"type": "watchClose", "id": 4, "watch": 1.0},
            {"type": "done"},
        ])
        self.assertEqual(replies[2]["result"]["code"], "timeout")
        self.assertTrue(replies[3]["accepted"])
        self.assertEqual(self.interests[-1]["block"], [])

    def test_watch_refusals_end_the_run_with_the_reason(self):
        for filter_, reason in (({"area": "within 0 0 0 9 9 9"}, "at most 512 blocks"),
                                ({}, "needs a filter")):
            with self.subTest(filter=filter_):
                self.ended.clear()
                self.run_script([{"type": "watch", "id": 2, "kind": "block.change", "filter": filter_}])
                self.assertIn(reason, self.outcome())

    def test_the_per_run_and_per_controller_limits_are_named(self):
        events = [{"type": "watch", "id": index + 2, "kind": "block.change",
                   "filter": {"area": f"within {index} 64 0"}} for index in range(MAX_WATCHES_PER_RUN + 1)]
        self.run_script(events)
        self.assertIn(f"at most {MAX_WATCHES_PER_RUN} watches", self.outcome())
        self.service.limits = SimpleNamespace(max_watches_per_controller=2)
        self.run_script(events[:3])
        self.assertIn("max-watches-per-controller", self.outcome())
        self.assertEqual(self.host._watch_owners, {})

    def test_without_endbotd_watch_is_refused(self):
        self.host.occurrences = None
        self.run_script([{"type": "watch", "id": 2, "kind": "block.change", "filter": {"area": AREA}}])
        self.assertIn("Watches need endbotd", self.outcome())

    def test_a_refused_subscribe_leaves_no_owner_entry(self):
        self.run_script([{"type": "watch", "id": 2, "kind": "block.change",
                          "filter": {"area": "within 0 0 0 9 9 9"}}])
        self.assertEqual(self.host._watch_owners, {})

    def test_buffer_limit_is_sixty_four(self):
        self.assertEqual(BUFFER_LIMIT, 64)


if __name__ == "__main__":
    unittest.main()
