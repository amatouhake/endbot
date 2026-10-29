"""Product drop grammar, replies, forms and batch parity (no Endstone required)."""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from endbot_core import forms
from endbot_core.commands import parse_command, strip_colors
from endbot_core.groups import GroupCommandService, GroupStore
from endbot_core.i18n import JA, tr
from plain_service import PlainCommandService
from test_command_service import FakeControl, FakeWorld
from test_forms import FakeFormModule, FakePlayer
from test_groups import GroupControl, messages

from endstone_endbot.form_adapter import FormPresenter


def drop_result(selector):
    return {"droppedSlots": 41 if selector == "all" else 1, "droppedCount": 5,
            "drops": [{"slot": selector, "item": "minecraft:stone", "count": 5}]}


class DropControl(FakeControl):
    def request(self, operation, **parameters):
        result = super().request(operation, **parameters)
        return {**result, **drop_result(parameters.get("selector", "selected"))} if operation == "drop" else result


class DropTests(unittest.TestCase):
    def test_parser_preserves_selected_grammar_and_resolves_ambiguous_tokens(self):
        self.assertEqual(parse_command(["Alice", "drop"]).parameters, {"stack": False})
        self.assertEqual(parse_command(["Alice", "drop", "stack"]).parameters, {"stack": True})
        for selector in (*map(str, range(1, 37)), "offhand", "head", "chest", "legs", "feet", "all"):
            self.assertEqual(parse_command(["Alice", "drop", selector]).parameters, {"selector": selector})
        for token in ("0", "37", "99999"):
            # A number outside the slots is a typo, never an item called "37".
            with self.assertRaisesRegex(ValueError, "Drop slots are 1 to 36"):
                parse_command(["Alice", "drop", token])
        for token in ("stone", "minecraft:stone", "minecraft:head", "minecraft:all", "minecraft:37"):
            selector = token if ":" in token else f"minecraft:{token}"
            self.assertEqual(parse_command(["Alice", "drop", token]).parameters, {"selector": selector})
            self.assertEqual(parse_command(["Alice", "drop", token, "7"]).parameters,
                             {"selector": selector, "count": 7})
        self.assertEqual(parse_command(["Alice", "drop", "stone", "10455"]).parameters["count"], 10455)

    def test_parser_rejects_invalid_counts_extra_arguments_and_identifiers(self):
        for count in ("0", "-1", "1.5", "10456", "99999999999999999", "true", "+2", "２"):
            with self.subTest(count=count), self.assertRaisesRegex(ValueError, "Drop count"):
                parse_command(["Alice", "drop", "stone", count])
        for arguments in (("head", "2"), ("all", "2"), ("stack", "2"), ("12", "2"),
                          ("stone", "1", "2"), ("custom:stone",), ("Stone",), ("bad!",)):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                parse_command(["Alice", "drop", *arguments])

    def test_replies_describe_requests_and_shortfall_in_both_languages(self):
        control = DropControl()
        service = PlainCommandService(control, FakeWorld())
        service.execute(["Alice", "spawn"])
        cases = (
            (("12",), "drop slot 12 stone x5 requested"),
            (("all",), "drop all requested for 41 slots"),
            (("stone", "99"), "drop stone x5 requested (asked for 99)"),
            (("stone",), "drop stone x5 requested from 1 slots"),
        )
        for arguments, expected in cases:
            result = service.execute(["Alice", "drop", *arguments])
            self.assertIn(expected, result.message)
            self.assertNotIn("completed", result.message)
        japanese = service.execute(["Alice", "drop", "stone", "99"], SimpleNamespace(language="ja"))
        self.assertIn("指定個数 99", japanese.message)
        self.assertIsNone(japanese.log_level)  # Ordinary single-Bot replies retain their existing logging policy.
        original = control.request
        def empty_request(operation, **params):
            result = original(operation, **params)
            return {**result, "drops": [], "droppedSlots": 0, "droppedCount": 0} if operation == "drop" else result

        control.request = empty_request
        self.assertIn("slot is empty", service.execute(["Alice", "drop", "12"]).message)
        self.assertIn("No stone in the inventory", service.execute(["Alice", "drop", "stone", "99"]).message)

    def test_form_commands_match_typed_inputs_and_drop_all_requires_confirmation(self):
        for name in ("Alice", "#miners", "#miners[1]"):
            screen = forms.drop_screen(name)
            actions = {item.text: item.action for item in screen.items}
            slot = actions["A slot..."].screen
            self.assertEqual(forms.modal_commands(slot, "[11]"), ((name, "drop", "12"),))
            self.assertEqual(forms.modal_commands(slot, "[37]"), ((name, "drop", "head"),))
            item = actions["An item..."].screen
            for count in ("", "7"):
                commands = forms.modal_commands(item, json.dumps(["stone", count]))
                self.assertEqual(commands, ((name, "drop", "stone", *((count,) if count else ())),))
                parse_command(list(commands[0]))
            with self.assertRaises(forms.FormResponseError):
                forms.modal_commands(item, '["stone dirt", "7"]')
            for reserved in ("all", "stack", "head", "12"):
                commands = forms.modal_commands(item, json.dumps([reserved, ""]))
                self.assertEqual(commands, ((name, "drop", f"minecraft:{reserved}"),))
                self.assertEqual(parse_command(list(commands[0])).parameters["selector"], f"minecraft:{reserved}")
            with self.assertRaises(forms.FormResponseError):
                forms.modal_commands(item, json.dumps(['"all"', ""]))
            confirmation = actions["Drop all..."].screen
            self.assertIsInstance(confirmation, forms.ConfirmScreen)
            self.assertEqual(confirmation.action.commands, ((name, "drop", "all"),))

    def test_presenter_echoes_only_confirmed_drop_all_and_preserves_group_guard(self):
        player, submitted = FakePlayer(), []
        guard = object()
        presenter = FormPresenter(lambda player, argv, **kwargs: submitted.append((argv, kwargs)),
                                  form_module=FakeFormModule())
        screen = forms.drop_screen("#miners")
        confirmation = screen.items[-1].action.screen
        rendered = presenter.render(confirmation, parent=screen, guard=guard)
        buttons = {text: callback for kind, text, callback in rendered.items if kind == "button"}
        self.assertEqual(submitted, [])
        buttons["Cancel"](player)
        self.assertEqual(submitted, [])
        buttons["Drop all"](player)
        self.assertEqual(submitted[0][0], ["#miners", "drop", "all"])
        self.assertIs(submitted[0][1]["group_guard"], guard)
        self.assertEqual(strip_colors(player.messages[-1]), "/bot #miners drop all")

    def test_japanese_drop_screen_and_count_errors_use_catalog(self):
        screen = forms.drop_screen("Alice", "ja")
        self.assertEqual(screen.content, JA["BDS validates each drop request."])
        self.assertEqual(screen.items[-1].text, JA["Drop all..."])
        self.assertEqual(screen.items[-1].action.screen.confirm, JA["Drop all"])
        with self.assertRaises(ValueError) as caught:
            parse_command(["Alice", "drop", "stone", "0"])
        self.assertEqual(tr("ja", caught.exception.template), JA["Drop count must be an integer from 1 to 10455"])


class DropGroupTests(unittest.TestCase):
    def test_new_selectors_use_pinned_batch_bounded_summary_audit_and_history(self):
        class Control(GroupControl):
            def request(self, operation, **parameters):
                result = super().request(operation, **parameters)
                return {**result, **drop_result(parameters.get("selector"))} if operation == "drop" else result

        with tempfile.TemporaryDirectory() as temporary:
            store, control = GroupStore(Path(temporary) / "groups.json"), Control()
            service = GroupCommandService(control, FakeWorld(), store, lambda _: True)
            for name in ("Alice", "Bob"):
                service.execute([name, "spawn"])
            service.execute_group(["create", "#miners"])
            for name in ("Alice", "Bob"):
                service.execute_group(["add", "#miners", name])
            for tail in (("12",), ("stone", "99"), ("all",)):
                control.calls.clear()
                result = service.execute(["#miners", "drop", *tail])
                self.assertIn("accepted 2, failed 0", messages(result))
                self.assertTrue(result.log_messages)
                batch = [params for operation, params in control.calls if operation == "groupInputBatch"]
                self.assertEqual(len(batch), 1)
                expected = parse_command(["Alice", "drop", *tail]).parameters
                for request, identity in zip(batch[0]["requests"], store.get("miners").members, strict=True):
                    self.assertEqual(request["method"], "drop")
                    self.assertEqual(request["params"]["expectedIdentityId"], identity)
                    self.assertEqual({key: request["params"][key] for key in expected}, expected)
                rows = service.results.get(None, store.get("miners")).rows
                self.assertEqual(len(rows), 2)
                self.assertTrue(all("requested" in row.detail for row in rows))
                self.assertIn("requested", messages(service.execute_group(["result", "#miners"])))
