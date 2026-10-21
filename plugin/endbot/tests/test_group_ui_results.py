import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from test_command_service import FakeWorld
from test_forms import FakeFormModule, FakePlayer, buttons
from test_groups import GroupControl, messages

from endstone_endbot import forms
from endstone_endbot.commands import strip_colors
from endstone_endbot.control import RuntimeControlError
from endstone_endbot.form_adapter import FormPresenter
from endstone_endbot.forms import BotView
from endstone_endbot.group_forms import GroupListView, GroupView, ResultView
from endstone_endbot.group_results import CACHE_SIZE, HISTORY_SIZE, GroupResult, MemberResult, ResultStore
from endstone_endbot.groups import Group, GroupCommandService, GroupStore


class GroupUiResultTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.store = GroupStore(Path(temporary.name) / "groups.json")
        self.control = GroupControl()
        self.allowed = True
        self.service = GroupCommandService(self.control, FakeWorld(), self.store, lambda _: self.allowed)
        self.source = SimpleNamespace(player_id="controller-one", language="en", name="Tester", group_guard=None)
        for name in ("Alice", "Bob", "Carol"):
            self.service.execute([name, "spawn"], self.source)
            self.control.bots[name]["connectionState"] = "online"
        self.service.execute_group(["create", "#miners"], self.source)
        for name in ("Alice", "Bob"):
            self.service.execute_group(["add", "#miners", name], self.source)
        self.control.calls.clear()
        self.player, self.submissions = FakePlayer(), []
        def submit(player, argv, **options):
            source = SimpleNamespace(**{**vars(self.source), "group_guard": options.get("group_guard")})
            execute = self.service.execute_group if options.get("command") == "botgroup" else self.service.execute
            result = execute(argv, source)
            self.submissions.append((argv, options, result))
            if result.view is not None:
                self.presenter.show_view(player, result.view)
        self.presenter = FormPresenter(submit, form_module=FakeFormModule())

    def manage(self, *argv, source=None):
        return self.service.execute_group(list(argv), source or self.source)

    def click(self, text):
        form = self.player.forms[-1]
        next(callback for kind, label, callback in form.items if kind == "button" and label == text)(self.player)

    def open_group(self):
        result = self.manage("menu", "#miners")
        self.assertIsInstance(result.view, GroupView)
        self.presenter.show_view(self.player, result.view)
        return result.view

    def test_normal_output_and_audit_remain_two_lines_with_one_hundred_failures(self):
        group = self.store.get("miners")
        self.store._replace(replace(group, members=tuple(f"00000000-0000-4000-8000-{i:012d}"
                                                       for i in range(1000, 1100))))
        result = self.service.execute(["#miners", "jump"], self.source)
        self.assertEqual(len(result.messages), 2)
        self.assertEqual(len(result.log_messages), 2)
        self.assertIn("failed 100", messages(result))
        self.assertIn("missing Bot profile 100", messages(result))
        self.assertIn("Tester:", " ".join(result.log_messages))
        detail = self.manage("result", "#miners")
        entries = [line for line in detail.messages if "?: failed" in line]
        self.assertEqual(len(entries), 100)
        self.assertIn("#miners[1]", strip_colors(entries[0]))
        self.assertIn("#miners[100]", strip_colors(entries[-1]))
        self.assertIsNone(detail.log_level)
        for number in ("0", "-1", "2", "1.5", "１"):
            self.assertEqual(len(self.manage("result", "#miners", number).messages), 1)

    def test_result_number_selects_an_operation_instead_of_a_member_page(self):
        self.service.execute(["#miners", "jump"], self.source)
        self.service.execute(["#miners", "stop"], self.source)
        latest = messages(self.manage("result", "#miners"))
        previous = messages(self.manage("result", "#miners", "2"))
        self.assertIn("stop - history 1/2", latest)
        self.assertIn("jump - history 2/2", previous)
        self.assertIn("#miners[1] Alice: accepted - jump once requested", previous)
        self.assertIn("#miners[2] Bob: accepted - jump once requested", previous)

    def test_bare_botgroup_opens_the_same_menu_as_menu(self):
        bare, explicit = self.manage(), self.manage("menu")
        self.assertIsInstance(bare.view, GroupListView)
        self.assertEqual(bare, explicit)
        self.assertIn("menu is available only to players", messages(
            self.service.execute_group([], None)))

    def test_results_are_private_frozen_and_not_overwritten_by_menu_or_other_controller(self):
        self.service.execute(["#miners", "jump"], self.source)
        first = self.service.results.get(self.source, self.store.get("miners"))
        other = SimpleNamespace(player_id="other", name="Other", language="en", group_guard=None)
        self.assertIn("No recent", messages(self.manage("result", "#miners", source=other)))
        self.service.execute(["#miners", "stop"], other)
        self.open_group()
        self.assertIs(self.service.results.get(self.source, self.store.get("miners")), first)
        self.control.request("rename", name="Alice", newName="Alicia")
        self.assertIn("Alice: accepted - jump once requested", messages(self.manage("result", "#miners")))
        fresh = GroupCommandService(self.control, FakeWorld(), self.store, lambda _: True)
        self.assertIn("No recent", messages(fresh.execute_group(["result", "#miners"], self.source)))

    def test_revoked_controller_cannot_read_results_or_open_group_menus(self):
        self.service.execute(["#miners", "jump"], self.source)
        self.allowed = False
        self.assertIn("permission was revoked", messages(self.manage("result", "#miners")))
        self.assertIsNone(self.service.execute(["#miners", "menu"], self.source).view)

    def test_lost_batch_response_is_unknown_not_failed_or_not_run(self):
        original = self.control.request
        def request(method, **params):
            response = original(method, **params)
            if method == "groupInputBatch":
                raise RuntimeControlError("unavailable", "response lost")
            return response
        self.control.request = request
        result = self.service.execute(["#miners", "jump"], self.source)
        self.assertIn("unknown 2", messages(result))
        self.assertIn("failed 0, not run 0", messages(result))

    def test_result_menu_and_text_command_use_identical_snapshot_and_pin_history_navigation(self):
        self.service.execute(["#miners", "jump"], self.source)
        self.service.execute(["#miners", "stop"], self.source)
        view = self.manage("menu", "#miners", "result").view
        self.assertIsInstance(view, ResultView)
        self.presenter.show_view(self.player, view)
        content = self.player.forms[-1].kwargs["content"]
        self.assertEqual(content, "\n".join(strip_colors(line).removeprefix("Endbot: ")
                                            for line in self.manage("result", "#miners").messages))
        self.service.execute(["#miners", "look", "45", "0"], self.source)
        self.click("Older result")
        self.assertIn("jump - history 3/3", self.player.forms[-1].kwargs["content"])
        self.click("Newer result")
        self.assertIn("stop - history 2/3", self.player.forms[-1].kwargs["content"])

    def test_evicted_result_form_does_not_silently_open_another_operation(self):
        self.service.execute(["#miners", "jump"], self.source)
        self.service.execute(["#miners", "stop"], self.source)
        self.presenter.show_view(self.player, self.manage("menu", "#miners", "result").view)
        for _ in range(HISTORY_SIZE):
            self.service.execute(["#miners", "stop"], self.source)
        self.click("Older result")
        self.assertIn("no longer retained", messages(self.submissions[-1][2]))

    def test_group_management_creation_uses_botgroup_and_echoes_its_actual_command(self):
        self.presenter.show_view(self.player, self.manage("menu").view)
        self.click("+ New group...")
        modal = self.player.forms[-1]
        modal.kwargs["on_submit"](self.player, json.dumps(["#builders"]))
        self.assertEqual(self.submissions[-1][0], ["create", "#builders"])
        self.assertEqual(self.submissions[-1][1], {"command": "botgroup"})
        self.assertIn("/botgroup create #builders", strip_colors(self.player.messages[-1]))
        self.assertEqual(self.store.get("builders").members, ())

    def test_bot_list_opens_group_operations_directly_with_the_shared_bot_layout(self):
        self.presenter.show_view(self.player, self.service.execute(["menu"], self.source).view)
        self.click("#miners (2)")
        argv, options, result = self.submissions[-1]
        self.assertEqual(argv, ["#miners", "menu"])
        self.assertNotIn("command", options)
        self.assertIsInstance(result.view, BotView)
        group = forms.bot_screen(result.view, "Steve")
        bot = forms.bot_screen(self.service.execute(["Alice", "menu"], self.source).view, "Steve")
        common = ["Stop everything", "Movement...", "Look...", "Teleport...", "Gameplay...", "Inventory...", "Bot..."]
        self.assertEqual([item.text for item in group.items[:7]], common)
        self.assertEqual([item.text for item in bot.items[:7]], common)
        self.assertNotIn("Group operations...", buttons(group, 0))
        self.assertNotIn("Delete group...", buttons(group, 0))
        self.assertNotIn("Profile...", buttons(group))
        self.click("Back to Bot list")
        self.assertIn("#miners (2)", [label for kind, label, _ in self.player.forms[-1].items if kind == "button"])

    def test_management_operations_and_member_menus_return_to_management(self):
        self.open_group()
        self.assertNotEqual(self.player.forms[-1].items[0][1], "Group operations...")
        self.click("Group operations...")
        self.click("Movement...")
        self.click("Back to group operations")
        self.click("Back to group management")
        self.assertIsInstance(self.submissions[-1][2].view, GroupView)
        self.click("1: Alice [online]")
        self.click("Movement...")
        self.click("Back to Bot")
        self.click("Back to group management")
        self.assertIsInstance(self.submissions[-1][2].view, GroupView)

    def test_operation_menu_links_to_current_group_management_and_rejects_a_replacement(self):
        self.presenter.show_view(self.player, self.service.execute(["#miners", "menu"], self.source).view)
        self.store.add("miners", self.control.bots["Carol"]["identityId"])
        self.click("Group management...")
        argv, options, result = self.submissions[-1]
        self.assertEqual(argv, ["menu", "#miners"])
        self.assertEqual(options["command"], "botgroup")
        self.assertIsInstance(result.view, GroupView)
        self.assertEqual(len(result.view.members), 3)
        self.click("Group operations...")
        self.assertNotIn("Group management...", [label for _, label, _ in self.player.forms[-1].items])
        self.click("Back to group management")
        self.presenter.show_view(self.player, self.service.execute(["#miners", "menu"], self.source).view)
        self.store.delete("miners")
        self.store.create("miners")
        self.click("Group management...")
        self.assertIn("Group changed", messages(self.submissions[-1][2]))

    def test_operation_result_navigation_returns_to_its_operation_entry(self):
        for management in (False, True):
            with self.subTest(management=management):
                if management:
                    self.open_group()
                    self.click("Group operations...")
                else:
                    self.presenter.show_view(self.player, self.service.execute(["#miners", "menu"], self.source).view)
                self.click("Stop everything")
                self.click("Recent operation result...")
                self.click("Back to group operations")
                view = self.submissions[-1][2].view
                self.assertIsInstance(view, BotView)
                self.assertEqual(view.guard.return_to_management, management)

    def test_group_teleport_modal_uses_the_typed_command_and_pins_each_bot(self):
        teleports = []
        def teleport(identity, parameters, source):
            teleports.append((identity, parameters, source))
            return "Overworld 10 64 20"
        self.service.world.teleport = teleport
        self.presenter.show_view(self.player, self.service.execute(["#miners", "menu"], self.source).view)
        self.click("Teleport...")
        self.click("Teleport...")
        # No player list in this fixture: index zero selects explicit coordinates.
        self.player.forms[-1].kwargs["on_submit"](self.player, json.dumps([0, "~1 64 ~2"]))
        self.assertEqual(self.submissions[-1][0], ["#miners", "tp", "~1", "64", "~2"])
        self.assertEqual([entry[0] for entry in teleports], list(self.store.get("miners").members))
        self.assertTrue(all(entry[1]["coordinates"] == [("relative", 1.0), 64.0, ("relative", 2.0)]
                            for entry in teleports))
        self.assertTrue(all(entry[2].player_id == self.source.player_id for entry in teleports))
        self.assertIn("accepted 2", messages(self.submissions[-1][2]))

    def test_mixed_and_empty_groups_keep_controls_and_stale_modals_do_not_act(self):
        self.control.bots["Bob"]["connectionState"] = "offline"
        self.presenter.show_view(self.player, self.service.execute(["#miners", "menu"], self.source).view)
        self.assertIn("Members 2, online 1", self.player.forms[-1].kwargs["content"])
        self.click("Bot...")
        self.click("Chunk radius...")
        self.assertIn("per Bot", self.player.forms[-1].kwargs["controls"][1].args[0])
        self.player.forms[-1].kwargs["on_submit"](self.player, json.dumps([False, 12]))
        for name in ("Alice", "Bob"):
            self.assertEqual(self.control.bots[name]["chunkRadius"], 12)
        self.presenter.show_view(self.player, self.service.execute(["#miners", "menu"], self.source).view)
        self.click("Movement...")
        self.click("Move...")
        self.store.remove("miners", self.control.bots["Bob"]["identityId"])
        self.player.forms[-1].kwargs["on_submit"](self.player, json.dumps([0, "20"]))
        self.assertIn("Group changed", messages(self.submissions[-1][2]))
        self.assertFalse(any(method == "groupInputBatch" for method, _ in self.control.calls))
        self.manage("create", "#empty")
        view = self.service.execute(["#empty", "menu"], self.source).view
        self.assertIsInstance(view, BotView)
        self.assertIn("Members 0, online 0", view.summary)

    def test_old_bot_list_group_target_cannot_open_a_replacement_group(self):
        self.presenter.show_view(self.player, self.service.execute(["menu"], self.source).view)
        self.store.delete("miners")
        self.store.create("miners")
        self.click("#miners (2)")
        self.assertIn("Group changed", messages(self.submissions[-1][2]))

    def test_group_forms_submit_the_same_bulk_command_with_identity_guard(self):
        self.open_group()
        self.click("Group operations...")
        self.click("Movement...")
        self.click("Jump...")
        self.click("once")
        argv, options, result = self.submissions[-1]
        self.assertEqual(argv, ["#miners", "jump", "once"])
        self.assertEqual(options["group_guard"].identity, self.store.get("miners").identity)
        self.assertIn("accepted 2", messages(result))
        self.assertEqual(len([1 for method, _ in self.control.calls if method == "groupInputBatch"]), 1)

    def test_management_modals_and_delete_confirmation_preserve_bots(self):
        self.open_group()
        self.click("Add member...")
        self.assertEqual(self.player.forms[-1].kwargs["title"], "Add a member to #miners")
        self.assertEqual(self.player.forms[-1].kwargs["submit_button"], "Add")
        self.player.forms[-1].kwargs["on_submit"](self.player, json.dumps([0]))
        self.assertEqual(self.submissions[-1][0], ["add", "#miners", "Carol"])
        self.open_group()
        self.click("Remove member...")
        self.assertEqual(self.player.forms[-1].kwargs["title"], "Remove a member from #miners")
        self.assertEqual(self.player.forms[-1].kwargs["submit_button"], "Remove")
        self.player.forms[-1].kwargs["on_submit"](self.player, json.dumps([1]))
        self.assertEqual(self.submissions[-1][0], ["remove", "#miners", "#miners[2]"])
        self.open_group()
        self.click("Delete group...")
        confirm = self.player.forms[-1]
        self.assertEqual(confirm.kwargs["title"], "Delete #miners")
        self.assertIn("Delete #miners? Its Bots stay registered.", confirm.kwargs["content"])
        self.click("Cancel")
        self.assertEqual(len(self.store.groups), 1)
        self.click("Delete group...")
        self.click("Delete #miners")
        self.assertEqual(self.store.groups, ())
        self.assertEqual(set(self.control.bots), {"Alice", "Bob", "Carol"})

    def test_old_group_and_member_forms_cannot_target_recreated_group_or_reordered_member(self):
        self.open_group()
        form = self.player.forms[-1]
        self.store.remove("miners", self.control.bots["Alice"]["identityId"])
        callback = next(callback for kind, label, callback in form.items
                        if kind == "button" and label.startswith("1:"))
        callback(self.player)
        self.assertIn("Group changed", messages(self.submissions[-1][2]))
        self.open_group()
        self.click("Delete group...")
        confirm = self.player.forms[-1]
        self.store.delete("miners")
        replacement = self.store.create("miners")
        next(callback for kind, label, callback in confirm.items if label == "Delete #miners")(self.player)
        self.assertIn("Group changed", messages(self.submissions[-1][2]))
        self.assertEqual(self.store.get("miners").identity, replacement.identity)

    def test_add_member_form_cannot_add_a_replacement_using_the_selected_name(self):
        self.open_group()
        self.click("Add member...")
        self.control.bots["Carol"] = {**self.control.bots["Carol"],
                                      "identityId": "00000000-0000-4000-8000-000000000099"}
        self.player.forms[-1].kwargs["on_submit"](self.player, json.dumps([0]))
        self.assertIn("Bot changed", messages(self.submissions[-1][2]))
        self.assertEqual(len(self.store.get("miners").members), 2)

    def test_member_bot_view_keeps_uuid_guard_after_navigation_and_name_reuse(self):
        view = self.service.execute(["#miners[1]", "menu"], self.source).view
        self.presenter.show_view(self.player, view)
        self.click("Movement...")
        self.click("Back to Bot")
        self.assertEqual(self.player.forms[-1].kwargs["title"], "Endbot: Alice")
        self.control.request("rename", name="Alice", newName="Alicia")
        self.control.request("spawn", name="Alice")
        self.click("Stop everything")
        self.assertIn("identity changed", messages(self.submissions[-1][2]))
        self.assertFalse(any(method == "stop" for method, _ in self.control.calls))


class ResultBoundsTests(unittest.TestCase):
    def test_reason_categories_are_bounded_even_with_unique_per_bot_errors(self):
        reasons = ["not_online", "offline", "missing_profile", "identity_changed", "canceled", "unavailable", "oops"]
        rows = tuple(MemberResult(i, str(i), str(i), "failed", reason, f"unique {i}")
                     for i, reason in enumerate(reasons))
        result = GroupResult("id", "group", "miners", "#miners", "jump", rows)
        self.assertEqual(len(result.summary()), 2)
        self.assertIn("offline 2", result.summary()[1])
        self.assertIn("other 3", result.summary()[1])

    def test_result_cache_retains_history_per_owner_group_and_has_global_bound(self):
        store = ResultStore()
        for index in range(CACHE_SIZE + 1):
            store.save(None, Group(str(index), "miners"), "#miners", "jump", [])
        self.assertEqual(len(store.records), CACHE_SIZE)
        with self.assertRaisesRegex(ValueError, "No recent"):
            store.get(None, Group("0", "miners"))
        group = Group("last", "miners")
        first = store.save(None, group, "#miners", "jump", [])
        second = store.save(None, group, "#miners", "stop", [])
        self.assertNotEqual(first.identity, second.identity)
        self.assertIs(store.get(None, group), second)
        self.assertIs(store.get(None, group, 2), first)
        for _ in range(HISTORY_SIZE):
            store.save(None, group, "#miners", "jump", [])
        self.assertEqual(len(store.history(None, group)), HISTORY_SIZE)
        with self.assertRaisesRegex(ValueError, "no longer retained"):
            store.find(None, group, first.identity)
        other = SimpleNamespace(player_id="other")
        with self.assertRaisesRegex(ValueError, "No recent"):
            store.find(other, group, second.identity)
