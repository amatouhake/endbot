"""The Triggers forms (BOT-022 step 5c): they resolve to the same /botscript trigger commands."""

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from endbot_core import areas, forms
from endbot_core.groups import GroupCommandService, GroupStore
from endbot_core.menu_views import menu_view, script_view
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

SCHEMA = [{"name": "event", "type": "event"}, {"name": "count", "type": "number", "default": 2}]
ALICE = Principal("player", name="Alice", xuid="2535400000000001", player_id="00000000-0000-4000-8000-0000000000a1",
                  grant_revision=3, role="scripter")
BOB = Principal("player", name="Bob", xuid="2535400000000002", player_id="00000000-0000-4000-8000-0000000000b2",
                grant_revision=1, role="scripter")


class TriggerFormTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.config = ScriptConfig(enabled=True)
        self.inputs = OwnedInputs(FakeControl())
        groups = GroupStore(root / "groups.json")
        service = GroupCommandService(self.inputs, None, groups, lambda source: True)
        self.store = ScriptStore(root / "scripts.json", self.config)
        self.store.save("guard", "params { param.event('event'), param.number('count', 2) }\n", None,
                        "xuid:2535400000000001", "Alice")
        self.host = ScriptService(self.config, self.store, service, self.inputs, root, lambda source: True,
                                  process_factory=lambda *args: FakeRunner([{"type": "done"}], schema=SCHEMA),
                                  bootstrap="trusted")
        self.host.inspect = lambda script, source: Declaration(list(SCHEMA), ("observe.chat",))
        self.host.ordered = lambda callback: callback()
        self.host.notify = lambda *args, **kwargs: None
        self.host.publish = lambda event: None
        self.addCleanup(self.host.close)
        self.interests = []
        self.hub = OccurrenceHub(EVENTS, self.interests.append)
        self.hub.link_changed("epoch-1")
        self.engine = TriggerEngine(TriggerStore(root / "triggers.json"), self.hub, self.host,
                                    authorize=lambda source: True)
        self.addCleanup(self.engine.close)
        self.host.triggers = self.engine
        self.engine.create(ALICE, script="guard", kind="block.change", filter_={"area": "within 0 64 0"},
                           name="watch")

    def screen(self, result, source=None):
        self.assertIsNone(result.view)
        view = menu_view(result, source or ALICE)
        self.assertIsInstance(view, forms.ScriptMenuView)
        return script_view(view.data)

    def test_the_script_menu_opens_the_trigger_list_and_a_trigger(self):
        listed = self.screen(self.host.execute(["menu"], ALICE))
        self.assertIn("Triggers...", [item.text for item in listed.items])
        trigger_button = next(item for item in listed.items if item.text == "Triggers...")
        self.assertEqual(trigger_button.action, forms.Run((("trigger", "menu"),), command="botscript"))

        result = self.host.execute(["trigger", "menu"], ALICE)
        self.assertEqual(result.data["menu"], "scriptTriggers")
        screen = self.screen(result)
        self.assertEqual([item.text for item in screen.items],
                         ["watch: block.change -> guard (armed)", "+ New trigger...", "Back to scripts"])
        self.assertEqual(screen.items[1].action, forms.Run((("trigger", "new"),), command="botscript"))

        result = self.host.execute(["trigger", "menu", "watch"], ALICE)
        self.assertEqual(result.data["menu"], "scriptTrigger")
        screen = self.screen(result)
        self.assertIn("Trigger watch", screen.content)
        self.assertIn("Owner: Alice", screen.content)
        self.assertIn("Filter: area within 0 64 0", screen.content)
        self.assertEqual([item.text for item in screen.items], ["Enable", "Disable", "Remove", "Back to triggers"])

    def test_a_shared_trigger_shows_no_owner_actions(self):
        self.engine.set_visibility(ALICE, "watch", "shared")
        screen = self.screen(self.host.execute(["trigger", "menu", "Alice/watch"], BOB), BOB)
        self.assertEqual([item.text for item in screen.items], ["Back to triggers"])

    def test_the_add_form_has_the_kind_filters_parameters_and_declaration(self):
        result = self.host.execute(["trigger", "new"], ALICE)
        self.assertEqual(result.data["menu"], "scriptTriggerScripts")
        screen = self.screen(result)
        self.assertEqual([item.text for item in screen.items], ["guard (yours, private)", "Back to triggers"])
        script_button = screen.items[0]
        self.assertEqual(script_button.action.commands, (("trigger", "new", "guard"),))
        kinds = self.screen(self.host.execute(list(script_button.action.commands[0]),
                                              replace(ALICE, group_guard=script_button.action.guard)))
        self.assertEqual(kinds.controls[0].label, "Event kind")
        self.assertEqual(kinds.controls[0].options, tuple(EVENTS))
        (kind_command,) = forms.modal_commands(kinds, json.dumps([0]))
        self.assertEqual(kind_command, ("trigger", "new", "guard", "block.change"))
        form = self.screen(self.host.execute(list(kind_command),
                                             replace(ALICE, group_guard=kinds.guard)))
        self.assertIsInstance(form.controls[0], forms.Label)
        self.assertEqual(form.controls[0].text, "This script also uses: observe.chat")
        labels = [control.label for control in form.controls[1:]]
        self.assertEqual(labels, ["area (area)", "dimension (dimension)", "from (block)", "to (block)",
                                  "count (number)"])
        self.assertFalse(any("event" in control.label for control in form.controls
                             if isinstance(control, forms.TextInput)))
        self.assertEqual(form.controls[2].default_value, "overworld")
        self.assertEqual(form.controls[-1].default_value, "2")

    def test_a_submitted_form_is_the_same_trigger_command(self):
        kinds = self.screen(self.host.execute(["trigger", "new", "guard"], ALICE))
        (kind_command,) = forms.modal_commands(kinds, json.dumps([0]))
        form = self.screen(self.host.execute(list(kind_command), replace(ALICE, group_guard=kinds.guard)))
        (command,) = forms.modal_commands(form, json.dumps(["0 64 0", "overworld", "stone", "air", "3"]))
        self.assertEqual(command, (
            "trigger", "add", "guard", "block.change",
            'area="within 0 64 0"', "dimension=overworld", "from=stone", "to=air",
            "--", "count=3",
        ))
        result = self.host.execute(list(command), replace(ALICE, group_guard=form.guard))
        self.assertTrue(result.accepted)
        trigger = next(trigger for trigger in self.engine.list(ALICE) if trigger.name == "guard")
        self.assertEqual(areas.area_text(trigger.filter["area"]), "within 0 64 0")
        self.assertEqual((trigger.filter["from"], trigger.filter["to"]),
                         ("minecraft:stone", "minecraft:air"))
        self.assertEqual(trigger.arguments, ["count=3"])


if __name__ == "__main__":
    unittest.main()
