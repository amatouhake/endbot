"""The trigger engine (BOT-022 step 5b, EVENTS.md E6 to E9): approval, starts, pauses, disables, restarts."""

import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

from endbot_core.groups import GroupCommandService, GroupStore
from endbot_core.i18n import LocalizedError
from endbot_core.operations import EVENTS
from endbot_core.principal import Principal
from endbot_core.script_config import ScriptConfig
from endbot_core.script_inputs import OwnedInputs
from endbot_core.script_store import ScriptStore
from endbot_core.scripting import Declaration, ScriptService
from endbot_core.trigger_store import TriggerStore
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

    def test_the_start_cap_disables_and_tells_the_owner(self):
        self.engine.start()
        trigger = self.engine.create(OWNER, script="guard", kind="block.change", filter_=AREA,
                                     policy={"cooldown": 0, "startsPerMinute": 1, "concurrency": "parallel"})
        self.hub.raise_occurrence("plugin", change())
        self.assertTrue(self.settle(lambda: self.trigger_store.get(trigger.owner, "guard").stats["starts"] == 1))
        self.hub.raise_occurrence("plugin", change())
        self.assertTrue(self.settle(lambda: self.trigger_store.get(trigger.owner, "guard").status == "disabled"))
        self.assertEqual(self.trigger_store.get(trigger.owner, "guard").reason, "rate limit")
        notices = [event for event in self.events if event.kind == "trigger.disabled"]
        self.assertEqual(notices[0].data["reason"], "rate limit")
        self.assertEqual(notices[0].to.player_id, OWNER.player_id)

    def test_no_start_while_the_plugin_is_unlinked(self):
        self.engine.start()
        trigger = self.engine.create(OWNER, script="guard", kind="block.change", filter_=AREA)
        self.linked = False
        self.hub.raise_occurrence("plugin", change())
        self.assertTrue(self.settle(lambda: self.trigger_store.get(trigger.owner, "guard").stats["skipped"] == 1))
        self.assertEqual(self.trigger_store.get(trigger.owner, "guard").stats["starts"], 0)

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
