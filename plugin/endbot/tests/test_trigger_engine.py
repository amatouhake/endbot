"""The trigger engine (BOT-022 step 5b, EVENTS.md E6 to E9): approval, starts, pauses, disables, restarts."""

import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from endbot_core.commands import reply
from endbot_core.events import render
from endbot_core.groups import GroupCommandService, GroupStore
from endbot_core.i18n import LocalizedError, tr
from endbot_core.operations import EVENTS
from endbot_core.principal import Principal
from endbot_core.script_config import ScriptConfig
from endbot_core.script_inputs import OwnedInputs
from endbot_core.script_store import FILE_OWNER, Script, ScriptStore
from endbot_core.scripting import Declaration, ScriptService
from endbot_core.trigger_store import TriggerStore
from endbotd import triggers as trigger_module
from endbotd.occurrences import OccurrenceHub
from endbotd.triggers import TriggerEngine, params_hash
from test_command_service import FakeControl
from test_scripting import FakeRunner

AREA = {"area": "within 0 64 0"}
EVENT_SCHEMA = [{"name": "event", "type": "event"}]
OWNER = Principal("player", name="Alice", xuid="2535400000000001", player_id="00000000-0000-4000-8000-0000000000a1",
                  grant_revision=3, role="scripter")
OTHER = Principal("player", name="Bob", xuid="2535400000000002", player_id="00000000-0000-4000-8000-0000000000b2",
                  grant_revision=1, role="scripter")


def change(x=0, y=64, z=0):
    return {"kind": "block.change", "x": x, "y": y, "z": z, "dimension": "overworld", "from": "minecraft:stone",
            "to": "minecraft:air", "cause": "poll"}


class TriggerStoreReload:
    def __init__(self, path):
        self.store = TriggerStore(path)

    def stats(self, owner, name):
        return self.store.get(owner, name).stats


class Clock:
    def __init__(self):
        self.mono, self.wall = 1000.0, 1_800_000_000.0

    def advance(self, seconds):
        self.mono += seconds
        self.wall += seconds


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.config = ScriptConfig(enabled=True, wall_seconds=5)
        self.inputs = OwnedInputs(FakeControl())
        groups = GroupStore(root / "groups.json")
        self.service = GroupCommandService(self.inputs, None, groups, lambda source: True)
        self.store = ScriptStore(root / "scripts.json", self.config)
        self.declaration = Declaration(list(EVENT_SCHEMA), ())
        self.started = []
        self.scripts = ScriptService(self.config, self.store, self.service, self.inputs, root,
                                     lambda source: self.allowed(source),
                                     process_factory=lambda *args: FakeRunner([{"type": "done"}],
                                                                              schema=self.declaration.schema),
                                     bootstrap="trusted")
        self.scripts.inspect = lambda script, source: self.declaration
        self.worker = ThreadPoolExecutor(max_workers=1)
        self.addCleanup(self.worker.shutdown)
        self.scripts.ordered = lambda callback: self.worker.submit(callback).result(timeout=3)
        self.scripts.notify = lambda *args, **kwargs: None
        self.events = []
        self.scripts.publish = self.events.append
        self.addCleanup(self.scripts.close)
        self.revoked = set()
        self.linked = True
        self.hub = OccurrenceHub(EVENTS, lambda interest: None)
        self.hub.link_changed("epoch-1")
        self.clock = Clock()
        self.trigger_store = TriggerStore(root / "triggers.json")
        self.engine = self.make_engine()
        self.save("guard", "params { param.event('event') }", OWNER)

    def allowed(self, source):
        return source.player_id not in self.revoked

    def make_engine(self, **overrides):
        options = {"authorize": self.allowed, "linked": lambda: self.linked, "publish": self.events.append,
                   "monotonic": lambda: self.clock.mono, "wall": lambda: self.clock.wall}
        options.update(overrides)
        engine = TriggerEngine(self.trigger_store, self.hub, self.scripts, **options)
        self.addCleanup(engine.close)
        return engine

    def save(self, name, source, owner):
        try:
            expected = self.store.get(name).revision
        except LocalizedError:
            expected = None
        self.store.save(name, source, expected, owner=self.scripts.owner_key(owner), owner_name=owner.name)

    def settle(self, condition=lambda: True, timeout=3):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if condition() and not self.scripts.active():
                return True
            time.sleep(0.02)
        return False

    def test_create_approves_and_an_occurrence_starts_a_run_with_the_event(self):
        self.engine.start()
        trigger = self.engine.create(OWNER, script="guard", kind="block.change", filter_=AREA)
        self.assertEqual((trigger.status, trigger.approved["declaration"], trigger.approved["grantRevision"]),
                         ("armed", [], 3))
        self.assertEqual(trigger.approved["paramsHash"], params_hash(EVENT_SCHEMA))
        self.hub.raise_occurrence("plugin", change())
        self.assertTrue(self.settle(lambda: self.trigger_store.get(trigger.owner, "guard").stats["starts"] == 1))
        ended = [event for event in self.events if event.kind == "run.ended"]
        self.assertEqual(len(ended), 1)

    def test_a_script_without_param_event_cannot_be_armed(self):
        self.declaration = Declaration([], ())
        with self.assertRaisesRegex(LocalizedError, "no param.event"):
            self.engine.create(OWNER, script="guard", kind="block.change", filter_=AREA)

    def test_only_someone_who_may_run_the_script_arms_it_and_limits_hold(self):
        with self.assertRaisesRegex(LocalizedError, "does not exist"):
            self.engine.create(OTHER, script="guard", kind="block.change", filter_=AREA)
        self.engine.max_triggers = 1
        self.engine.create(OWNER, script="guard", kind="block.change", filter_=AREA)
        with self.assertRaisesRegex(LocalizedError, "at most 1 armed triggers"):
            self.engine.create(OWNER, script="guard", kind="block.change", filter_=AREA, name="second")
        self.engine.max_triggers = 0
        with self.assertRaisesRegex(LocalizedError, "turned off"):
            self.engine.create(OWNER, script="guard", kind="block.change", filter_=AREA, name="third")

    def test_a_grown_declaration_or_changed_parameters_pause_until_enabled_again(self):
        self.engine.start()
        trigger = self.engine.create(OWNER, script="guard", kind="block.change", filter_=AREA)
        self.save("guard", "params { param.event('event') } -- r2", OWNER)
        self.declaration = Declaration(list(EVENT_SCHEMA), ("observe.chat",))
        self.hub.raise_occurrence("plugin", change())
        self.assertTrue(self.settle(lambda: self.trigger_store.get(trigger.owner, "guard").status == "paused"))
        self.assertEqual(self.trigger_store.get(trigger.owner, "guard").reason, "declaration grew")
        enabled = self.engine.enable(OWNER, "guard")
        self.assertEqual((enabled.status, enabled.approved["declaration"]), ("armed", ["observe.chat"]))
        self.save("guard", "params { param.event('event'), param.number('n') } -- r3", OWNER)
        self.declaration = Declaration([*EVENT_SCHEMA, {"name": "n", "type": "number"}], ("observe.chat",))
        self.hub.raise_occurrence("plugin", change())
        self.assertTrue(self.settle(lambda: self.trigger_store.get(trigger.owner, "guard").status == "paused"))
        self.assertEqual(self.trigger_store.get(trigger.owner, "guard").reason, "arguments no longer valid")

    def test_someone_elses_script_runs_only_at_the_approved_revision(self):
        self.save("shared", "params { param.event('event') }", OTHER)
        self.store.set_visibility("shared", "shared", self.store.get("shared").revision)
        self.engine.start()
        trigger = self.engine.create(OWNER, script="shared", kind="block.change", filter_=AREA)
        self.hub.raise_occurrence("plugin", change())
        self.assertTrue(self.settle(lambda: self.trigger_store.get(trigger.owner, "shared").stats["starts"] == 1))
        self.save("shared", "params { param.event('event') } -- r2", OTHER)  # same declaration, new code
        self.clock.advance(2)  # past the cooldown
        self.hub.raise_occurrence("plugin", change())
        self.assertTrue(self.settle(lambda: self.trigger_store.get(trigger.owner, "shared").status == "paused"))
        stored = self.trigger_store.get(trigger.owner, "shared")
        self.assertEqual((stored.reason, stored.stats["starts"]), ("script changed", 1))
        enabled = self.engine.enable(OWNER, "shared")
        self.assertEqual(enabled.approved["revision"], self.store.get("shared").revision)

    def test_the_owners_own_script_follows_its_current_revision(self):
        self.engine.start()
        trigger = self.engine.create(OWNER, script="guard", kind="block.change", filter_=AREA)
        self.save("guard", "params { param.event('event') } -- r2", OWNER)
        self.hub.raise_occurrence("plugin", change())
        self.assertTrue(self.settle(lambda: self.trigger_store.get(trigger.owner, "guard").stats["starts"] == 1))
        self.assertEqual(self.trigger_store.get(trigger.owner, "guard").status, "armed")

    def test_the_operators_script_files_follow_their_current_revision(self):
        def files(revision, source):
            return {"filed": Script("filed", source, revision, FILE_OWNER, None, "shared")}

        self.store.set_files(files("file:1", "params { param.event('event') }"))
        self.engine.start()
        trigger = self.engine.create(OWNER, script="filed", kind="block.change", filter_=AREA)
        self.store.set_files(files("file:2", "params { param.event('event') } -- fixed a typo"))
        self.hub.raise_occurrence("plugin", change())
        self.assertTrue(self.settle(lambda: self.trigger_store.get(trigger.owner, "filed").stats["starts"] == 1))
        self.assertEqual(self.trigger_store.get(trigger.owner, "filed").status, "armed")

    def test_a_pause_and_an_engine_disable_tell_the_owner(self):
        self.engine.start()
        trigger = self.engine.create(OWNER, script="guard", kind="block.change", filter_=AREA)
        self.declaration = Declaration(list(EVENT_SCHEMA), ("observe.chat",))
        self.save("guard", "params { param.event('event') } -- r2", OWNER)
        self.hub.raise_occurrence("plugin", change())
        self.assertTrue(self.settle(lambda: self.trigger_store.get(trigger.owner, "guard").status == "paused"))
        stopped = [event for event in self.events if event.kind == "trigger.stopped"]
        self.assertEqual([(event.data["status"], event.data["reason"]) for event in stopped],
                         [("paused", "declaration grew")])
        self.assertEqual(stopped[0].to.player_id, OWNER.player_id)
        self.engine.enable(OWNER, "guard")
        self.revoked.add(OWNER.player_id)
        self.engine.grants_changed()
        stopped = [event for event in self.events if event.kind == "trigger.stopped"]
        self.assertEqual((stopped[-1].data["status"], stopped[-1].data["reason"]), ("disabled", "owner revoked"))
        self.engine.disable(OWNER, "guard")  # the owner's own disable needs no notice
        self.assertEqual(len([event for event in self.events if event.kind == "trigger.stopped"]), 2)

    def test_a_refused_start_tells_the_owner_once_until_a_start_succeeds(self):
        self.engine.start()
        trigger = self.engine.create(OWNER, script="guard", kind="block.change", filter_=AREA,
                                     policy={"cooldown": 0})
        real = self.scripts.start
        refusals = [LocalizedError("A controller may run at most {limit} scripts at once", limit=2)] * 2

        def start(*args, **kwargs):
            if refusals:
                raise refusals.pop()
            return real(*args, **kwargs)

        with patch.object(self.scripts, "start", side_effect=start):
            for expected in (1, 2):
                self.hub.raise_occurrence("plugin", change())
                self.assertTrue(self.settle(lambda expected=expected: self.trigger_store.get(
                    trigger.owner, "guard").stats["skipped"] == expected))
            blocked = [event for event in self.events if event.kind == "trigger.blocked"]
            self.assertEqual(len(blocked), 1)
            self.assertEqual(blocked[0].data["reason"]["params"], {"limit": 2})
            self.hub.raise_occurrence("plugin", change())
            self.assertTrue(self.settle(lambda: self.trigger_store.get(trigger.owner, "guard").stats["starts"] == 1))
            refusals.append(LocalizedError("A controller may run at most {limit} scripts at once", limit=2))
            self.hub.raise_occurrence("plugin", change())
            self.assertTrue(self.settle(lambda: self.trigger_store.get(trigger.owner, "guard").stats["skipped"] == 3))
        self.assertEqual(len([event for event in self.events if event.kind == "trigger.blocked"]), 2)

    def test_revoking_the_owner_disables_and_a_regrant_does_not_rearm(self):
        self.engine.start()
        trigger = self.engine.create(OWNER, script="guard", kind="block.change", filter_=AREA)
        self.revoked.add(OWNER.player_id)
        self.engine.grants_changed()
        stored = self.trigger_store.get(trigger.owner, "guard")
        self.assertEqual((stored.status, stored.reason), ("disabled", "owner revoked"))
        self.revoked.clear()
        self.engine.grants_changed()
        self.assertEqual(self.trigger_store.get(trigger.owner, "guard").status, "disabled")

    def test_the_start_cap_throttles_and_tells_the_owner_once(self):
        self.engine.start()
        trigger = self.engine.create(OWNER, script="guard", kind="block.change", filter_=AREA,
                                     policy={"cooldown": 0, "startsPerMinute": 1, "concurrency": "parallel"})
        self.hub.raise_occurrence("plugin", change())
        self.assertTrue(self.settle(lambda: self.trigger_store.get(trigger.owner, "guard").stats["starts"] == 1))
        self.hub.raise_occurrence("plugin", change())
        self.hub.raise_occurrence("plugin", change())
        self.assertTrue(self.settle(lambda: self.trigger_store.get(trigger.owner, "guard").stats.get("missed") == 2))
        self.assertEqual(self.trigger_store.get(trigger.owner, "guard").status, "armed")
        notices = [event for event in self.events if event.kind == "trigger.throttled"]
        self.assertEqual(len(notices), 1)
        self.assertEqual((notices[0].data["limit"], notices[0].to.player_id), (1, OWNER.player_id))
        self.clock.advance(60)
        self.hub.raise_occurrence("plugin", change())
        self.assertTrue(self.settle(lambda: self.trigger_store.get(trigger.owner, "guard").stats["starts"] == 2))

    def test_a_notice_speaks_the_owners_language(self):
        # Found in the 0.7.0 release pass: the throttle notice reached a Japanese player in English, because the
        # trigger's run principal carried no language.
        languages = []
        self.engine = self.make_engine(language=lambda xuid: languages.append(xuid) or "ja")
        self.engine.start()
        trigger = self.engine.create(OWNER, script="guard", kind="block.change", filter_=AREA,
                                     policy={"cooldown": 0, "startsPerMinute": 1, "concurrency": "parallel"})
        for _ in range(3):
            self.hub.raise_occurrence("plugin", change())
        self.assertTrue(self.settle(lambda: any(event.kind == "trigger.throttled" for event in self.events)))
        notice = next(event for event in self.events if event.kind == "trigger.throttled")
        self.assertEqual(notice.to.language, "ja")
        self.assertIn(OWNER.xuid, languages)
        self.assertEqual(render(notice, "chat"),
                         (reply(tr("ja", "Trigger {name} reached {limit} starts per minute; occurrences are skipped "
                                         "until the minute allows more", name="guard", limit=1)),))
        self.assertNotEqual(render(notice, "chat"), render(notice, "chat", "en"))
        self.assertEqual(trigger.name, "guard")

    def test_a_run_that_ends_while_disabled_does_not_block_the_trigger_after_enable(self):
        trigger = self.engine.create(OWNER, script="guard", kind="block.change", filter_=AREA,
                                     policy={"cooldown": 0})
        key = (trigger.owner, trigger.name)
        self.engine._policies[key].started(self.clock.mono)  # a long run from this trigger is active
        self.engine.disable(OWNER, "guard")
        self.engine._ended(key)  # it ends while the trigger is disabled
        self.engine.enable(OWNER, "guard")
        self.engine.start()
        self.hub.raise_occurrence("plugin", change())
        self.assertTrue(self.settle(lambda: self.trigger_store.get(trigger.owner, "guard").stats.get("starts") == 1))
        self.assertEqual(self.trigger_store.get(trigger.owner, "guard").stats.get("missed", 0), 0)

    def test_missed_occurrences_do_not_rewrite_the_file_each_time(self):
        self.engine.start()
        trigger = self.engine.create(OWNER, script="guard", kind="block.change", filter_=AREA,
                                     policy={"cooldown": 72000})
        self.hub.raise_occurrence("plugin", change())
        self.assertTrue(self.settle(lambda: self.trigger_store.get(trigger.owner, "guard").stats["starts"] == 1))
        with patch.object(self.trigger_store, "_save", wraps=self.trigger_store._save) as save:
            for _ in range(200):
                self.hub.raise_occurrence("plugin", change())
            self.assertTrue(self.settle(
                lambda: self.trigger_store.get(trigger.owner, "guard").stats.get("missed") == 200))
            self.assertLessEqual(save.call_count, 5)
        self.engine.close()
        self.assertEqual(TriggerStoreReload(self.trigger_store.path).stats(trigger.owner, "guard")["missed"], 200)

    def test_occurrences_beyond_the_work_queue_bound_are_counted_as_missed(self):
        self.engine.start()
        trigger = self.engine.create(OWNER, script="guard", kind="block.change", filter_=AREA)
        with patch.object(trigger_module, "MAX_PENDING_OCCURRENCES", 0):  # the worker is "full"
            for _ in range(5):
                self.hub.raise_occurrence("plugin", change())
            self.assertTrue(self.settle(
                lambda: self.trigger_store.get(trigger.owner, "guard").stats.get("missed") == 5))
        self.assertEqual(self.trigger_store.get(trigger.owner, "guard").stats.get("starts", 0), 0)

    def test_queue_keeps_the_newest_occurrences_and_starts_them_in_order(self):
        self.engine.start()
        trigger = self.engine.create(OWNER, script="guard", kind="block.change",
                                     filter_={"area": "within 0 64 0 9 64 0"},
                                     policy={"concurrency": "queue", "queue": 2, "cooldown": 200})
        for x in range(4):
            self.hub.raise_occurrence("plugin", change(x))
        self.assertTrue(self.settle(lambda: self.trigger_store.get(trigger.owner, "guard").stats["starts"] == 1))
        self.assertTrue(self.settle(lambda: self.trigger_store.get(trigger.owner, "guard").stats["missed"] == 1))
        with self.engine._lock:
            queued = [item["x"] for item in self.engine._armed[(trigger.owner, "guard")]["queue"]]
        self.assertEqual(queued, [2, 3])  # x=1 was the oldest queued and was dropped
        self.clock.advance(11)
        self.assertTrue(self.settle(lambda: self.trigger_store.get(trigger.owner, "guard").stats["starts"] == 2))

    def test_no_start_while_the_plugin_is_unlinked(self):
        self.engine.start()
        trigger = self.engine.create(OWNER, script="guard", kind="block.change", filter_=AREA)
        self.linked = False
        self.hub.raise_occurrence("plugin", change())
        self.assertTrue(self.settle(lambda: self.trigger_store.get(trigger.owner, "guard").stats["skipped"] == 1))
        self.assertEqual(self.trigger_store.get(trigger.owner, "guard").stats["starts"], 0)

    def test_an_occurrence_while_unlinked_is_skipped_and_not_also_missed(self):
        """EVENTS.md S3: counted once, as skipped; the next start's a.event.missed does not include it."""
        self.engine.start()
        trigger = self.engine.create(OWNER, script="guard", kind="block.change", filter_=AREA)
        self.linked = False
        self.hub.raise_occurrence("plugin", change())
        self.assertTrue(self.settle(lambda: self.trigger_store.get(trigger.owner, "guard").stats["skipped"] == 1))
        self.linked = True
        events = []
        real = self.scripts.start

        def start(*args, **kwargs):
            events.append(kwargs["event"])
            return real(*args, **kwargs)

        with patch.object(self.scripts, "start", side_effect=start):
            self.hub.raise_occurrence("plugin", change())
            self.assertTrue(self.settle(lambda: self.trigger_store.get(trigger.owner, "guard").stats["starts"] == 1))
        self.assertEqual(events[0]["missed"], 0)
        self.assertEqual(self.trigger_store.get(trigger.owner, "guard").stats["missed"], 0)

    def test_a_failing_inspection_is_not_retried_on_every_occurrence(self):
        """A broken script under an armed trigger must not hold the single decision worker each time."""
        self.engine.start()
        trigger = self.engine.create(OWNER, script="guard", kind="block.change", filter_=AREA,
                                     policy={"cooldown": 0})
        self.save("guard", "params { param.event('event') } -- edited", OWNER)
        calls = []

        def broken(script, source):
            calls.append(script.revision)
            raise LocalizedError("Script inspection timed out")

        self.scripts.inspect = broken
        for expected in (1, 2, 3):
            self.hub.raise_occurrence("plugin", change())
            self.assertTrue(self.settle(lambda expected=expected: self.trigger_store.get(
                trigger.owner, "guard").stats["skipped"] == expected))
        self.assertEqual(len(calls), 1)
        self.clock.advance(61)
        self.hub.raise_occurrence("plugin", change())
        self.assertTrue(self.settle(lambda: self.trigger_store.get(trigger.owner, "guard").stats["skipped"] == 4))
        self.assertEqual(len(calls), 2)

    def test_a_burst_of_stats_changes_reaches_the_listener_once_and_then_its_latest(self):
        """An event storm must not become a push storm on an open trigger page."""
        self.engine.start()
        trigger = self.engine.create(OWNER, script="guard", kind="block.change", filter_=AREA)
        noticed = []
        self.engine.on_changed = lambda changed, removed=False: noticed.append(changed.stats["skipped"])
        key = (trigger.owner, trigger.name)
        for _ in range(5):
            self.engine._bump(key, "skipped")
        self.engine.flush_notices()
        self.assertEqual(noticed, [1])
        time.sleep(1.1)
        self.engine._stats_due()
        self.engine.flush_notices()
        self.assertEqual(noticed, [1, 5])

    def test_a_restart_keeps_state_and_stats_and_records_the_gap(self):
        self.engine.start()
        trigger = self.engine.create(OWNER, script="guard", kind="block.change", filter_=AREA,
                                     policy={"cooldown": 200})
        self.hub.raise_occurrence("plugin", change())
        self.assertTrue(self.settle(lambda: self.trigger_store.get(trigger.owner, "guard").stats["starts"] == 1))
        self.engine.close()
        restarted = self.make_engine()
        restarted.start()
        stored = self.trigger_store.get(trigger.owner, "guard")
        self.assertEqual((stored.status, stored.stats["starts"]), ("armed", 1))
        self.assertIsNotNone(stored.stats["lastGap"])
        self.hub.raise_occurrence("plugin", change())  # within the restored 10 s cooldown: missed
        self.assertTrue(self.settle(lambda: self.trigger_store.get(trigger.owner, "guard").stats["missed"] == 1))

    def test_a_player_trigger_starts_as_a_run_of_its_owner_and_survives_a_reload(self):
        seen = []
        self.engine = self.make_engine(authorize=lambda source: seen.append(source) or self.allowed(source))
        self.engine.start()
        trigger = self.engine.create(OWNER, script="guard", kind="block.change", filter_=AREA)
        reloaded = TriggerStore(self.trigger_store.path).get(trigger.owner, "guard")
        self.assertEqual((reloaded.approved["grantRevision"], reloaded.approved["playerId"]),
                         (3, OWNER.player_id))
        principal = self.engine._principal(reloaded)
        self.assertEqual((principal.kind, principal.player_id, principal.console), ("run", OWNER.player_id, False))
        self.hub.raise_occurrence("plugin", change())
        self.assertTrue(self.settle(lambda: self.trigger_store.get(trigger.owner, "guard").stats["starts"] == 1))

    def test_queued_occurrences_wait_while_unlinked_and_then_for_the_start_cap(self):
        self.engine.start()
        trigger = self.engine.create(OWNER, script="guard", kind="block.change", filter_=AREA,
                                     policy={"concurrency": "queue", "cooldown": 100, "startsPerMinute": 1})
        self.hub.raise_occurrence("plugin", change())
        self.assertTrue(self.settle(lambda: self.trigger_store.get(trigger.owner, "guard").stats["starts"] == 1))
        self.hub.raise_occurrence("plugin", change())  # queued: in cooldown
        time.sleep(0.3)
        self.linked = False
        self.clock.advance(6)
        time.sleep(0.6)
        self.assertEqual(self.trigger_store.get(trigger.owner, "guard").stats["starts"], 1)
        self.linked = True  # the queued start is now over the cap of one per minute: it waits, still armed
        self.assertTrue(self.settle(lambda: any(event.kind == "trigger.throttled" for event in self.events)))
        self.assertEqual(self.trigger_store.get(trigger.owner, "guard").status, "armed")
        self.assertEqual(self.trigger_store.get(trigger.owner, "guard").stats["starts"], 1)
        self.clock.advance(60)
        self.assertTrue(self.settle(lambda: self.trigger_store.get(trigger.owner, "guard").stats["starts"] == 2))

    def test_changing_the_policy_keeps_the_start_window_and_ignores_the_old_subscription(self):
        self.engine.start()
        trigger = self.engine.create(OWNER, script="guard", kind="block.change", filter_=AREA,
                                     policy={"cooldown": 0, "startsPerMinute": 1})
        self.hub.raise_occurrence("plugin", change())
        self.assertTrue(self.settle(lambda: self.trigger_store.get(trigger.owner, "guard").stats["starts"] == 1))
        self.engine.set_policy(OWNER, "guard", {"cooldown": 0})
        self.hub.raise_occurrence("plugin", change())
        self.assertTrue(self.settle(lambda: self.trigger_store.get(trigger.owner, "guard").stats.get("missed") == 1))
        self.assertEqual(self.trigger_store.get(trigger.owner, "guard").stats["starts"], 1)

    def test_a_limit_of_zero_arms_nothing_and_enable_respects_the_owners_limit(self):
        self.engine.create(OWNER, script="guard", kind="block.change", filter_=AREA)
        self.engine.close()
        off = self.make_engine(max_triggers=0)
        off.start()
        self.assertEqual(off._armed, {})
        with self.assertRaisesRegex(LocalizedError, "turned off"):
            off.enable(Principal("console"), "guard", self.engine.list(OWNER)[0].owner)

    def test_arguments_are_checked_when_the_trigger_is_armed(self):
        self.declaration = Declaration([*EVENT_SCHEMA, {"name": "n", "type": "number"}], ())
        with self.assertRaisesRegex(LocalizedError, "Invalid script argument"):
            self.engine.create(OWNER, script="guard", kind="block.change", filter_=AREA, arguments=["not-a-number"])
        self.assertEqual(self.engine.create(OWNER, script="guard", kind="block.change", filter_=AREA,
                                            arguments=["4"]).status, "armed")

    def test_visibility_and_ownership_of_operations(self):
        self.engine.create(OWNER, script="guard", kind="block.change", filter_=AREA)
        self.assertEqual(self.engine.list(OTHER), [])
        self.engine.set_visibility(OWNER, "guard", "shared")
        self.assertEqual([trigger.name for trigger in self.engine.list(OTHER)], ["guard"])
        owner = self.engine.list(OTHER)[0].owner
        with self.assertRaisesRegex(LocalizedError, "Only the owner"):
            self.engine.disable(OTHER, "guard", owner)
        console = Principal("console")
        self.assertEqual(self.engine.disable(console, "guard", owner).status, "disabled")
        self.engine.remove(OWNER, "guard")
        self.assertEqual(self.engine.list(console), [])


if __name__ == "__main__":
    unittest.main()
