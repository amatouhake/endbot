"""Every /botscript menu outcome is data; menu_views builds the same old screen."""

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from endbot_core import forms, menu_views, script_forms
from endbot_core.commands import ERROR, error_code, reply
from endbot_core.groups import Group
from endbot_core.i18n import localize_error
from endbot_core.menu_views import menu_view, script_view
from endbot_core.principal import Principal
from endbot_core.script_books import pages_to_source
from endbot_core.script_config import ScriptConfig
from endbot_core.script_store import FILE_OWNER, Script, ScriptStore
from endbot_core.scripting import Declaration, ScriptService
from endbot_core.wire_views import decode_view, encode_view


class FakeInputs:
    def __init__(self, bots):
        self.bots = list(bots)

    def request(self, method, **params):
        if method == "profiles":
            return self.bots
        raise AssertionError(f"unexpected input request: {method}")


class FakeWorld:
    def __init__(self, pages):
        self.pages = list(pages)

    def held_book_pages(self, player_id, writable=False):
        return list(self.pages)


class ScriptMenuViewTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.config = ScriptConfig(enabled=True, book_export=True, ui_pack_editor=True)
        self.store = ScriptStore(Path(directory.name) / "scripts.json", self.config)
        first = self.store.save("mine", "params {}\n-- v1\n", None, "xuid:111", "Alice")
        self.store.save("mine", "params {}\n-- v2\n", first.revision, "xuid:111", "Alice")
        self.world = FakeWorld(["params {}\n-- book\n"])
        self.inputs = FakeInputs([{"name": "Botty", "identityId": "bot-id", "connectionState": "online"}])
        self.host = ScriptService(
            self.config, self.store, SimpleNamespace(world=self.world, groups=None), self.inputs,
            Path(directory.name), lambda source: True, process_factory=lambda *args: None, bootstrap="trusted")
        self.alice = Principal("player", player_id="a-uuid", xuid="111", name="Alice", grant_revision=0)
        self.bob = Principal("player", player_id="b-uuid", xuid="222", name="Bob", grant_revision=0)

    def screen(self, result, source=None):
        self.assertIsNone(result.view)
        view = menu_view(result, source or self.alice)
        # The outcome crosses as data; script screens are built where forms are rendered.
        self.assertIsInstance(view, forms.ScriptMenuView)
        return script_view(view.data)

    def script(self, name="mine"):
        return self.store.get(name)

    def guard(self, **fields):
        return script_forms.ScriptGuard(self.script().revision, "mine", **fields)

    def test_list_and_source_outcomes_build_the_old_screens(self):
        result = self.host.execute(["menu"], self.alice)
        self.assertEqual(result.data["menu"], "scripts")
        self.assertEqual(
            self.screen(result),
            script_forms.list_screen(self.store.list(), "en",
                                     own=lambda script: script.owner == self.host.owner_key(self.alice)))

        result = self.host.execute(["menu", "mine"], self.alice)
        self.assertEqual(result.data["menu"], "script")
        self.assertEqual(
            self.screen(result),
            script_forms.script_screen(self.script(), "en", True, self.config.book_export))

        # A shared script shows the non-owner screen.
        revision = self.script().revision
        self.store.set_visibility("mine", "shared", revision)
        result = self.host.execute(["menu", "mine"], self.bob)
        self.assertEqual(result.data["menu"], "script")
        self.assertEqual(
            self.screen(result, self.bob),
            script_forms.script_screen(self.script(), "en", False, self.config.book_export))

    def test_edit_and_save_outcomes_build_the_old_screens(self):
        result = self.host.execute(["edit", "mine"], self.alice)
        self.assertEqual(result.data["menu"], "scriptLines")
        self.assertEqual(
            self.screen(result),
            script_forms.lines_screen(self.script(), 1, "en", self.config.ui_pack_editor))

        self.host.inspect = lambda script, source: Declaration([], ())
        result = self.host.execute(["save", "new", "params {}\n"],
                                   replace(self.alice, group_guard=script_forms.ScriptGuard(edit=True)))
        self.assertEqual(result.data["menu"], "script")
        self.assertEqual(
            self.screen(result),
            script_forms.script_screen(self.script("new"), "en", True, self.config.book_export))

        result = self.host.execute(["save", "mine", "params {}\n-- v3\n"],
                                   replace(self.alice, group_guard=self.guard(edit=True, page=1)))
        self.assertEqual(result.data["menu"], "scriptLines")
        self.assertEqual(
            self.screen(result),
            script_forms.lines_screen(self.script(), 1, "en", self.config.ui_pack_editor))

    def test_parameters_outcome_builds_the_old_run_form(self):
        schema = [{"name": "bot", "type": "bot"}, {"name": "text", "type": "string", "default": "x"}]
        self.host.inspect = lambda script, source: Declaration(schema, ())
        result = self.host.execute(["parameters", "mine"], self.alice)
        self.assertEqual(result.data["menu"], "scriptParameters")
        expected = script_forms.run_screen(self.script(), schema, (), self.inputs.bots, None, "en")
        actual = self.screen(result)
        self.assertEqual(actual, expected)
        response = json.dumps([0, "hello world"])
        self.assertEqual(forms.modal_commands(actual, response), forms.modal_commands(expected, response))
        self.assertEqual(actual.guard.targets, {"bot": ("bot", "Botty", "bot-id")})
        self.assertEqual(actual.guard.requires, ())

    def test_a_run_from_the_parameter_form_is_held_to_the_declaration_the_form_read(self):
        schema = [{"name": "text", "type": "string", "default": "x"}]
        self.host.inspect = lambda script, source: Declaration(schema, ("observe.chat",))
        form = self.screen(self.host.execute(["parameters", "mine"], self.alice))
        self.assertEqual(form.guard.requires, ("observe.chat",))
        # The guard crosses the endbotd link with the declaration intact.
        self.assertEqual(decode_view(encode_view(form.guard)), form.guard)
        started = []
        self.host.start = lambda script, values, source, targets, requires=None: started.append(requires) or "r"
        (argv,) = forms.modal_commands(form, json.dumps([None, "hello"]))
        self.assertEqual(argv[:2], ("run", "mine"))
        # A client that leaves labels out of its response submits the same command.
        self.assertEqual(forms.modal_commands(form, json.dumps(["hello"])), (argv,))
        self.host.execute(list(argv), replace(self.alice, group_guard=form.guard))
        self.assertEqual(started, [("observe.chat",)])
        # A typed run has no inspection to match.
        self.host.execute(["run", "mine", "text=hello"], self.alice)
        self.assertEqual(started[-1], None)

    def test_a_declaration_is_the_first_line_of_the_run_form(self):
        schema = [{"name": "text", "type": "string", "default": "x"}]
        self.host.inspect = lambda script, source: Declaration(schema, ("observe.chat",))
        form = self.screen(self.host.execute(["parameters", "mine"], self.alice))
        self.assertIsInstance(form.controls[0], forms.Label)
        self.assertIn("observe.chat", form.controls[0].text)
        # A script with no declaration keeps the form exactly as today: no label.
        self.host.inspect = lambda script, source: Declaration(schema, ())
        form = self.screen(self.host.execute(["parameters", "mine"], self.alice))
        self.assertFalse(any(isinstance(control, forms.Label) for control in form.controls))

    def test_a_declaration_label_is_japanese(self):
        schema = [{"name": "text", "type": "string", "default": "x"}]
        self.host.inspect = lambda script, source: Declaration(schema, ("observe.chat",))
        source = replace(self.alice, language="ja")
        form = self.screen(self.host.execute(["parameters", "mine"], source), source)
        self.assertEqual(form.controls[0], forms.Label("このスクリプトは次の機能も使います: observe.chat"))

    def test_parameters_keep_group_members_and_origin(self):
        group = Group("group-id", "miners", ("bot-uuid",))
        self.host.service.groups = SimpleNamespace(groups=(group,))
        self.host.service.visible_groups = lambda source: (group,)
        schema = [{"name": "group", "type": "group"},
                  {"name": "origin", "type": "pos", "default": "~ ~ ~"}]
        origin = {"x": 1, "y": 64, "z": 2}
        source = replace(self.alice, origin=origin)
        self.host.inspect = lambda script, source: Declaration(schema, ())
        result = self.host.execute(["parameters", "mine"], source)
        expected = script_forms.run_screen(self.script(), schema, (group,), self.inputs.bots, origin, "en")
        actual = self.screen(result, source)
        self.assertEqual(actual, expected)
        response = json.dumps([0, "1 64 2"])
        self.assertEqual(forms.modal_commands(actual, response), forms.modal_commands(expected, response))
        self.assertEqual(actual.guard.targets,
                         {"group": ("group", "#miners", "group-id", ("bot-uuid",))})
        self.assertEqual(actual.guard.targets, expected.guard.targets)
        self.assertEqual(actual.controls[0].options, ("#miners",))
        self.assertEqual(actual.controls[1].default_value, "1 64 2")

    def test_import_and_export_outcomes_build_the_old_confirmations(self):
        result = self.host.execute(["import", "mine"], replace(self.alice, group_guard=self.guard()))
        self.assertEqual(result.data["menu"], "scriptImport")
        text = pages_to_source(self.world.pages)
        self.assertEqual(
            self.screen(result),
            script_forms.import_confirm_screen(self.script(), text, "en"))

        result = self.host.execute(["export", "mine"], replace(self.alice, group_guard=self.guard()))
        self.assertEqual(result.data["menu"], "scriptExport")
        self.assertEqual(
            self.screen(result),
            script_forms.export_confirm_screen(
                self.script(), self.world.pages, pages_to_source(self.world.pages), "en"))

    def test_history_outcome_builds_the_old_screens(self):
        result = self.host.execute(["history", "mine"], replace(self.alice, group_guard=self.guard()))
        self.assertEqual(result.data["menu"], "scriptHistory")
        self.assertEqual(self.screen(result), script_forms.history_screen(self.script(), True, "en"))

        revision = self.script().revision
        self.store.set_visibility("mine", "shared", revision)
        result = self.host.execute(["history", "mine"], replace(self.bob, group_guard=self.guard()))
        self.assertEqual(result.data["menu"], "scriptHistory")
        self.assertEqual(self.screen(result, self.bob), script_forms.history_screen(self.script(), False, "en"))

    def test_parameters_without_targets_fail_in_the_service(self):
        self.inputs.bots = []
        for schema in ([{"name": "bot", "type": "bot"}], [{"name": "group", "type": "group"}]):
            with self.subTest(schema=schema):
                with self.assertRaises(forms.FormResponseError) as raised:
                    script_forms.run_screen(self.script(), schema, (), [], None, "en")
                self.host.inspect = lambda script, source, schema=schema: Declaration(schema, ())
                result = self.host.execute(["parameters", "mine"], self.alice)
                self.assertFalse(result.accepted)
                self.assertEqual(result.code, error_code(raised.exception))
                self.assertEqual(result.messages, (reply(localize_error(raised.exception, "en"), color=ERROR),))
                self.assertIsNone(result.view)
                self.assertIsNone(menu_view(result, self.alice))

    def test_a_non_english_language_reaches_the_view(self):
        source = replace(self.alice, language="ja")
        result = self.host.execute(["menu"], source)
        self.assertEqual(result.data["lang"], "ja")
        expected = script_forms.list_screen(
            self.store.list(), "ja", own=lambda script: script.owner == self.host.owner_key(source))
        self.assertEqual(self.screen(result, source), expected)
        self.assertNotEqual(expected.title, "Endbot: scripts")

    def test_list_labels_file_and_unowned_scripts(self):
        self.store.set_files({
            "patrol": Script("patrol", "params {}\n", "file:r", FILE_OWNER, None, "shared", ()),
        })
        legacy = self.store.save("legacy", "params {}\n", None, None, None)
        self.store.set_visibility("legacy", "shared", legacy.revision)
        result = self.host.execute(["menu"], self.alice)
        expected = script_forms.list_screen(
            self.store.list(), "en", own=lambda script: script.owner == self.host.owner_key(self.alice))
        screen = self.screen(result)
        self.assertEqual(screen, expected)
        texts = [item.text for item in screen.items]
        self.assertIn("patrol (file, shared)", texts)
        self.assertIn("legacy (unowned, shared)", texts)

    def test_every_script_outcome_crosses_to_the_plugin_as_data(self):
        # A script screen carries ModalScreen.build, a function, so it cannot cross the endbotd
        # link (encode_view refuses it). The outcome crosses as data and the plugin builds the
        # screen; this test is the guard for the whole set, not one menu.
        schema = [{"name": "bot", "type": "bot"}, {"name": "text", "type": "string", "default": "x"}]
        self.host.inspect = lambda script, source: Declaration(schema, ())
        outcomes = {
            "scripts": self.host.execute(["menu"], self.alice),
            "script": self.host.execute(["menu", "mine"], self.alice),
            "scriptLines": self.host.execute(["edit", "mine"], self.alice),
            "scriptImport": self.host.execute(["import", "mine"], replace(self.alice, group_guard=self.guard())),
            "scriptExport": self.host.execute(["export", "mine"], replace(self.alice, group_guard=self.guard())),
            "scriptHistory": self.host.execute(["history", "mine"],
                                               replace(self.alice, group_guard=self.guard(edit=True, page=1))),
            "scriptParameters": self.host.execute(["parameters", "mine"], self.alice),
        }
        self.assertEqual(set(outcomes), set(menu_views.SCRIPT_MENUS))
        for menu, result in outcomes.items():
            with self.subTest(menu=menu):
                self.assertIsNone(result.view)
                view = menu_view(result, self.alice)
                self.assertEqual(view, forms.ScriptMenuView(result.data))
                self.assertEqual(decode_view(json.loads(json.dumps(encode_view(view)))), view)
                self.assertIsNotNone(script_view(view.data))

    def test_unchanged_book_result_with_a_script_guard_has_no_view(self):
        # Pre-existing path: the import action carries a ScriptGuard and an
        # unchanged book returns a message-only result, which delivery still
        # passes to menu_view.
        self.world.pages = ["params {}", "-- v2"]
        source = replace(self.alice, group_guard=self.guard())
        result = self.host.execute(["import", "mine"], source)
        self.assertTrue(result.accepted)
        self.assertIsNone(result.data)
        self.assertIsNone(result.view)
        self.assertIsNone(menu_view(result, source))


if __name__ == "__main__":
    unittest.main()
