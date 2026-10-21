"""Swap/hold grammar, request replies, forms and UUID-pinned batch parity."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from plain_service import PlainCommandService
from test_command_service import FakeControl, FakeWorld
from test_forms import FakeFormModule, FakePlayer, buttons
from test_groups import GroupControl, messages

from endstone_endbot import forms
from endstone_endbot.commands import parse_command, strip_colors
from endstone_endbot.control import RuntimeControlError
from endstone_endbot.form_adapter import FormPresenter
from endstone_endbot.groups import GroupCommandService, GroupStore
from endstone_endbot.i18n import JA, localize_error, tr


class MoveControl(FakeControl):
    source = 3
    found = True
    swap_error = None

    def request(self, operation, **params):
        result = super().request(operation, **params)
        if operation == "hold":
            return {**result, "found": self.found, "sourceSlot": self.source, "selectedHotbarSlot": 3}
        if operation == "swap" and self.swap_error:
            raise RuntimeControlError("invalid_request", self.swap_error)
        return result


class InventoryMoveTests(unittest.TestCase):
    def test_parser_preserves_item_spelling_and_inventory_slot_labels(self):
        for a, b in ((1, 36), (10, 2), (36, 1)):
            self.assertEqual(parse_command(["Alice", "swap", str(a), str(b)]).parameters, {"a": a, "b": b})
        for item in ("diamond_pickaxe", "minecraft:stone", "all", "head"):
            self.assertEqual(parse_command(["Alice", "hold", item]).parameters, {"item": item})

    def test_invalid_inputs_and_same_slot_are_localized_before_ipc(self):
        for tail in (("swap",), ("swap", "1"), ("swap", "1", "1"), ("swap", "0", "2"),
                     ("swap", "37", "1"), ("swap", "offhand", "2"), ("swap", "1.0", "2"),
                     ("swap", "01", "2"), ("swap", "1", "2", "3"), ("hold",),
                     ("hold", "stone", "dirt"), ("hold", "custom:stone"), ("hold", "Stone")):
            with self.subTest(tail=tail), self.assertRaises(ValueError) as caught:
                parse_command(["Alice", *tail])
            self.assertIn(caught.exception.template, JA)

    def test_replies_report_requests_and_hold_missing_names_the_requested_item(self):
        control = MoveControl()
        service = PlainCommandService(control, FakeWorld())
        service.execute(["Alice", "spawn"])
        self.assertIn("swap 10 2 requested", service.execute(["Alice", "swap", "10", "2"]).message)
        self.assertIn("hold diamond_pickaxe: selected hotbar 3 requested",
                      service.execute(["Alice", "hold", "diamond_pickaxe"]).message)
        control.source = 14
        self.assertIn("hold minecraft:stone: moved slot 14 to hotbar 3 requested",
                      service.execute(["Alice", "hold", "minecraft:stone"]).message)
        control.found = False
        missing = service.execute(["Alice", "hold", "diamond_pickaxe"]).message
        self.assertIn("Item diamond_pickaxe was not found", missing)
        japanese = service.execute(["Alice", "hold", "diamond_pickaxe"], SimpleNamespace(language="ja")).message
        self.assertIn(tr("ja", "Item {item} was not found", item="diamond_pickaxe"), japanese)

    def test_empty_and_same_slot_errors_have_japanese_catalog_entries(self):
        control = MoveControl()
        service = PlainCommandService(control, FakeWorld())
        service.execute(["Alice", "spawn"])
        control.swap_error = "Both inventory slots are empty"
        for language in ("en", "ja"):
            result = service.execute(["Alice", "swap", "1", "2"], SimpleNamespace(language=language))
            self.assertIn(tr(language, control.swap_error), result.message)
        error = ValueError("Swap requires two different slots")
        self.assertEqual(localize_error(error, "ja"), JA[str(error)])

    def test_inventory_menus_and_form_commands_match_typed_bot_and_group_inputs(self):
        for name in ("Alice", "#miners", "#miners[1]"):
            for lang in ("en", "ja"):
                screen = forms.bot_screen(forms.BotView(name, "summary", {"connectionState": "online"}),
                                          "Steve", lang=lang)
                actions = buttons(screen)
                hold = actions[tr(lang, "Hold item...")].screen
                swap = actions[tr(lang, "Swap slots...")].screen
                self.assertEqual(forms.modal_commands(hold, '["minecraft:stone"]'),
                                 ((name, "hold", "minecraft:stone"),))
                self.assertEqual(forms.modal_commands(swap, '[9, 1]'), ((name, "swap", "10", "2"),))
                for command in (*forms.modal_commands(hold, '["diamond_pickaxe"]'),
                                *forms.modal_commands(swap, '[0, 35]')):
                    parse_command(list(command))

    def test_forms_reject_invalid_responses_and_echo_typed_commands_with_guard(self):
        for modal, response in ((forms.hold_modal("Alice"), '["stone dirt"]'),
                                (forms.hold_modal("Alice"), '["custom:stone"]'),
                                (forms.swap_modal("Alice"), '[36, 0]'),
                                (forms.swap_modal("Alice"), '[0]')):
            with self.assertRaises(forms.FormResponseError):
                forms.modal_commands(modal, response)
        player, submitted, guard = FakePlayer(), [], object()
        presenter = FormPresenter(lambda player, argv, **kwargs: submitted.append((argv, kwargs)),
                                  form_module=FakeFormModule())
        for modal, response in ((forms.hold_modal("#miners"), '["minecraft:stone"]'),
                                (forms.swap_modal("#miners"), '[9, 1]')):
            rendered = presenter.render(modal, guard=guard)
            rendered.kwargs["on_submit"](player, response)
            argv, kwargs = submitted[-1]
            self.assertEqual(strip_colors(player.messages[-1]), "/bot " + " ".join(argv))
            self.assertIs(kwargs["group_guard"], guard)


class InventoryMoveGroupTests(unittest.TestCase):
    def test_swap_and_hold_are_mutating_batches_with_pinned_identity_and_partial_failures(self):
        class Control(GroupControl):
            def request(self, operation, **params):
                result = super().request(operation, **params)
                if operation == "hold":
                    return {**result, "found": params["name"] == "Alice", "sourceSlot": 14, "selectedHotbarSlot": 1}
                return result

        with tempfile.TemporaryDirectory() as temporary:
            store, control = GroupStore(Path(temporary) / "groups.json"), Control()
            service = GroupCommandService(control, FakeWorld(), store, lambda _: True)
            for name in ("Alice", "Bob"):
                service.execute([name, "spawn"])
            service.execute_group(["create", "#miners"])
            for name in ("Alice", "Bob"):
                service.execute_group(["add", "#miners", name])
            for tail in (("swap", "10", "2"), ("hold", "diamond_pickaxe")):
                control.calls.clear()
                result = service.execute(["#miners", *tail])
                self.assertIn("accepted 2, failed 0" if tail[0] == "swap" else "accepted 1, failed 1", messages(result))
                self.assertTrue(result.log_messages)
                batch = [params for method, params in control.calls if method == "groupInputBatch"]
                self.assertEqual(len(batch), 1)
                expected = parse_command(["Alice", *tail]).parameters
                for request, identity in zip(batch[0]["requests"], store.get("miners").members, strict=True):
                    self.assertEqual(request["method"], tail[0])
                    self.assertEqual(request["params"]["expectedIdentityId"], identity)
                    self.assertEqual({key: request["params"][key] for key in expected}, expected)
                rows = service.results.get(None, store.get("miners")).rows
                self.assertIn("requested", rows[0].detail)
                if tail[0] == "hold":
                    self.assertIn("diamond_pickaxe", rows[1].detail)
                self.assertIn("requested", messages(service.execute_group(["result", "#miners"])))
