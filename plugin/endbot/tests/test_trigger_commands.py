"""The /botscript trigger commands (BOT-022 step 5c): parse, call the engine and reply."""

import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

from endbot_core import areas
from endbot_core.commands import strip_colors
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
from endbotd.triggers import TriggerEngine
from test_command_service import FakeControl
from test_scripting import FakeRunner

AREA = "within 0 64 0"
EVENT_SCHEMA = [{"name": "event", "type": "event"}]
OWNER = Principal("player", name="Alice", xuid="2535400000000001", player_id="00000000-0000-4000-8000-0000000000a1",
                  grant_revision=3, role="scripter")
OTHER = Principal("player", name="Bob", xuid="2535400000000002", player_id="00000000-0000-4000-8000-0000000000b2",
                  grant_revision=1, role="scripter")
CONSOLE = Principal("console")


class TriggerCommandTests(unittest.TestCase):
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
        self.scripts = ScriptService(
            self.config, self.store, self.service, self.inputs, root, lambda source: True,
            process_factory=lambda *args: FakeRunner([{"type": "done"}], schema=self.declaration.schema),
            bootstrap="trusted")
        self.scripts.inspect = lambda script, source: self.declaration
        self.worker = ThreadPoolExecutor(max_workers=1)
        self.addCleanup(self.worker.shutdown)
        self.scripts.ordered = lambda callback: self.worker.submit(callback).result(timeout=3)
        self.scripts.notify = lambda *args, **kwargs: None
        self.scripts.publish = lambda event: None
        self.addCleanup(self.scripts.close)
        self.hub = OccurrenceHub(EVENTS, lambda interest: None)
        self.hub.link_changed("epoch-1")
        self.trigger_store = TriggerStore(root / "triggers.json")
        self.engine = TriggerEngine(self.trigger_store, self.hub, self.scripts, authorize=lambda source: True)
        self.addCleanup(self.engine.close)
        self.scripts.triggers = self.engine
        self.save("guard", "params { param.event('event') }", OWNER)

    def save(self, name, source, owner):
        try:
            expected = self.store.get(name).revision
        except LocalizedError:
            expected = None
        self.store.save(name, source, expected, owner=self.scripts.owner_key(owner), owner_name=owner.name)

    def command(self, *argv, source=None):
        return self.scripts.execute(list(argv), source or OWNER)

    def messages(self, result):
        return tuple(strip_colors(message) for message in result.messages)

    def owner_key(self, principal=OWNER):
        return self.scripts.owner_key(principal)

    def stored(self, name="guard", owner=OWNER):
        return self.trigger_store.get(self.owner_key(owner), name)

    def test_add_arms_the_trigger_with_the_canonical_filter(self):
        result = self.command("trigger", "add", "guard", "block.change", 'area="100 64 200"', "to=air")
        self.assertTrue(result.accepted)
        self.assertEqual(self.messages(result), ("Endbot: Trigger guard armed for block.change",))
        trigger = self.stored()
        self.assertEqual((trigger.script, trigger.kind, trigger.visibility, trigger.arguments),
                         ("guard", "block.change", "private", []))
        self.assertEqual(areas.area_text(trigger.filter["area"]), "within 100 64 200")
        self.assertEqual(trigger.filter["to"], "minecraft:air")

    def test_area_except_arguments_and_a_name(self):
        # The stored arguments are checked against the script's parameters when the trigger is armed.
        self.declaration = Declaration([*EVENT_SCHEMA, {"name": "n", "type": "number"},
                                        {"name": "label", "type": "string"}], ())
        result = self.command("trigger", "add", "guard", "block.change", 'area="0 64 0 except 5 0 0"',
                          "name=watch", "--", "3", '"x y z"')
        self.assertTrue(result.accepted)
        trigger = self.stored("watch")
        self.assertEqual(areas.area_text(trigger.filter["area"]), "within 0 64 0 except 5 0 0")
        self.assertEqual(trigger.arguments, ["3", "x y z"])

    def test_list_and_show_state_the_fields_the_screen_states(self):
        self.assertEqual(self.messages(self.command("trigger", "list")), ("Endbot: No triggers",))
        self.command("trigger", "add", "guard", "block.change", 'area="0 64 0"', "name=first")
        self.command("trigger", "add", "guard", "block.change", 'area="1 64 1"', "name=second")
        listed = self.messages(self.command("trigger", "list"))
        self.assertEqual(len(listed), 2)
        self.assertEqual(listed[0], "Endbot: first (Alice) block.change -> guard: armed")
        self.assertEqual(listed[1], "Endbot: second (Alice) block.change -> guard: armed")
        shown = self.messages(self.command("trigger", "show", "second"))
        self.assertEqual(shown, (
            "Endbot: Trigger second",
            "Endbot: Owner: Alice",
            "Endbot: Kind: block.change",
            "Endbot: Filter: area within 1 64 1",
            "Endbot: Script: guard",
            "Endbot: Arguments: none",
            "Endbot: Status: armed",
            "Endbot: Policy: concurrency skip, cooldown 20, startsPerMinute 10, queue 4",
            "Endbot: Stats: 0 starts, 0 missed, 0 skipped, last start never",
        ))
        self.engine.pause_for((self.owner_key(), "second"), "declaration grew")
        self.assertIn("Endbot: Status: paused (declaration grew)",
                      self.messages(self.command("trigger", "show", "second")))

    def test_enable_disable_and_remove(self):
        self.command("trigger", "add", "guard", "block.change", 'area="0 64 0"')
        self.assertEqual(self.messages(self.command("trigger", "disable", "guard")),
                         ("Endbot: Trigger guard disabled",))
        self.assertEqual(self.stored().status, "disabled")
        self.assertEqual(self.messages(self.command("trigger", "enable", "guard")),
                         ("Endbot: Trigger guard armed; it runs guard with: basic only",))
        self.assertEqual(self.stored().status, "armed")
        self.assertEqual(self.messages(self.command("trigger", "remove", "guard")), ("Endbot: Trigger guard removed",))
        self.assertIsNone(self.stored())

    def test_enable_shows_the_approved_declaration(self):
        self.declaration = Declaration(list(EVENT_SCHEMA), ("observe.chat",))
        self.command("trigger", "add", "guard", "block.change", 'area="0 64 0"')
        self.command("trigger", "disable", "guard")
        self.assertEqual(self.messages(self.command("trigger", "enable", "guard")),
                         ("Endbot: Trigger guard armed; it runs guard with: observe.chat",))

    def test_set_policy_and_visibility(self):
        self.command("trigger", "add", "guard", "block.change", 'area="0 64 0"')
        result = self.command("trigger", "set", "guard", "cooldown=40", "queue=2")
        self.assertEqual(self.messages(result),
                         ("Endbot: Trigger guard: concurrency skip, cooldown 40, startsPerMinute 10, queue 2",))
        self.assertEqual((self.stored().policy["cooldown"], self.stored().policy["queue"]), (40, 2))
        self.assertEqual(self.messages(self.command("trigger", "visibility", "guard", "shared")),
                         ("Endbot: Trigger guard is now shared",))
        self.assertEqual(self.stored().visibility, "shared")
        refused = self.command("trigger", "set", "guard", "cooldown=soon")
        self.assertFalse(refused.accepted)
        self.assertIn("must be an integer", self.messages(refused)[0])
        self.assertIn("Unknown trigger policy", self.messages(self.command("trigger", "set", "guard", "speed=2"))[0])
        self.assertFalse(self.command("trigger", "visibility", "guard", "public").accepted)

    def test_the_console_addresses_an_owner_by_display_name_or_key(self):
        self.command("trigger", "add", "guard", "block.change", 'area="0 64 0"')
        self.assertIn("Endbot: Trigger guard",
                      self.messages(self.command("trigger", "show", "Alice/guard", source=CONSOLE)))
        self.assertEqual(self.messages(self.command("trigger", "disable", f"{self.owner_key()}/guard", source=CONSOLE)),
                         ("Endbot: Trigger guard disabled",))
        self.assertIn("Endbot: guard (Alice) block.change -> guard: disabled (disabled by its owner)",
                      self.messages(self.command("trigger", "list", source=CONSOLE)))
        self.assertEqual(self.messages(self.command("trigger", "show", "alice/guard", source=CONSOLE))[0],
                         "Endbot: Trigger guard")
        self.assertEqual(self.messages(self.command("trigger", "show", "Nobody/guard", source=CONSOLE)),
                         ("Endbot: Trigger guard does not exist",))

    def test_a_shared_trigger_is_visible_but_only_its_owner_changes_it(self):
        self.command("trigger", "add", "guard", "block.change", 'area="0 64 0"')
        self.assertEqual(self.messages(self.command("trigger", "show", "guard", source=OTHER)),
                         ("Endbot: Trigger guard does not exist",))
        self.command("trigger", "visibility", "guard", "shared")
        self.assertIn("Endbot: Trigger guard",
                      self.messages(self.command("trigger", "show", "Alice/guard", source=OTHER)))
        refused = self.command("trigger", "disable", "Alice/guard", source=OTHER)
        self.assertFalse(refused.accepted)
        self.assertEqual(self.messages(refused), ("Endbot: Only the owner Alice may change trigger guard",))

    def test_engine_refusals_are_returned_as_the_other_botscript_refusals(self):
        self.declaration = Declaration([], ())
        self.save("plain", "params {}", OWNER)
        refused = self.command("trigger", "add", "plain", "block.change", 'area="0 64 0"')
        self.assertFalse(refused.accepted)
        self.assertIn("has no param.event", self.messages(refused)[0])
        self.declaration = Declaration(list(EVENT_SCHEMA), ())
        refused = self.command("trigger", "add", "guard", "block.nope", 'area="0 64 0"')
        self.assertIn("Unknown event kind block.nope", self.messages(refused)[0])
        refused = self.command("trigger", "add", "guard", "block.change", 'area="0 0 0 9 9 9"')
        self.assertIn("at most 512 blocks", self.messages(refused)[0])
        self.engine.max_triggers = 1
        self.command("trigger", "add", "guard", "block.change", 'area="0 64 0"')
        refused = self.command("trigger", "add", "guard", "block.change", 'area="1 64 1"', "name=second")
        self.assertIn("at most 1 armed triggers", self.messages(refused)[0])

    def test_without_endbotd_every_trigger_command_says_so(self):
        self.scripts.triggers = None
        for argv in (("trigger", "list"), ("trigger", "show", "guard"), ("trigger", "add", "guard", "block.change")):
            with self.subTest(argv=argv):
                self.assertEqual(self.messages(self.command(*argv)),
                                 ("Endbot: Triggers need endbotd; they are not available here",))

    def test_a_japanese_reply(self):
        source = replace(OWNER, language="ja")
        result = self.command("trigger", "add", "guard", "block.change", 'area="0 64 0"', source=source)
        self.assertEqual(self.messages(result), ("Endbot: トリガー guard を block.change で有効にしました",))
        self.assertEqual(self.messages(self.command("trigger", "list", source=source))[0],
                         "Endbot: guard (Alice) block.change -> guard: 作動中")


if __name__ == "__main__":
    unittest.main()

