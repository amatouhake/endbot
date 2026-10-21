import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from test_command_service import FakeWorld
from test_forms import FakeFormModule, FakePlayer
from test_groups import GroupControl, messages

from endstone_endbot.commands import strip_colors
from endstone_endbot.control import RuntimeControlError
from endstone_endbot.form_adapter import FormPresenter
from endstone_endbot.group_forms import GroupGuard, GroupView, ResultView
from endstone_endbot.group_results import CACHE_SIZE, GroupResult, MemberResult, ResultStore
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
        result = self.service.execute(["#miners", "menu"], self.source)
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
        page = self.manage("result", "#miners", "2")
        entries = [line for line in page.messages if "?: failed" in line]
        self.assertEqual(len(entries), 10)
        self.assertIn("#miners[11]", strip_colors(entries[0]))
        self.assertIn("#miners[20]", strip_colors(entries[-1]))
        self.assertIsNone(page.log_level)
        for number in ("0", "-1", "11", "1.5"):
            self.assertEqual(len(self.manage("result", "#miners", number).messages), 1)

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

    def test_result_menu_and_text_command_use_identical_snapshot_and_reject_stale_pages(self):
        self.service.execute(["#miners", "jump"], self.source)
        view = self.manage("menu", "#miners", "result").view
        self.assertIsInstance(view, ResultView)
        self.presenter.show_view(self.player, view)
        content = self.player.forms[-1].kwargs["content"]
        self.assertEqual(content, "\n".join(strip_colors(line).removeprefix("Endbot: ")
                                            for line in self.manage("result", "#miners").messages))
        source = SimpleNamespace(**{**vars(self.source), "group_guard": GroupGuard(
            view.result.group_id, view.result.group_name, result_id=view.result.identity)})
        self.service.execute(["#miners", "stop"], self.source)
        self.assertIn("Recent result changed", messages(self.manage("menu", "#miners", "result", source=source)))

    def test_bot_list_group_entry_and_creation_use_botgroup_and_echo_its_actual_command(self):
        self.presenter.show_view(self.player, self.service.execute(["menu"], self.source).view)
        self.click("Groups...")
        self.click("+ New group...")
        modal = self.player.forms[-1]
        modal.kwargs["on_submit"](self.player, json.dumps(["#builders"]))
        self.assertEqual(self.submissions[-1][0], ["create", "#builders"])
        self.assertEqual(self.submissions[-1][1], {"command": "botgroup"})
        self.assertIn("/botgroup create #builders", strip_colors(self.player.messages[-1]))
        self.assertEqual(self.store.get("builders").members, ())

    def test_group_forms_submit_the_same_bulk_command_with_identity_guard(self):
        self.open_group()
        self.click("Group operations...")
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
        self.player.forms[-1].kwargs["on_submit"](self.player, json.dumps([0]))
        self.assertEqual(self.submissions[-1][0], ["add", "#miners", "Carol"])
        self.open_group()
        self.click("Remove member...")
        self.player.forms[-1].kwargs["on_submit"](self.player, json.dumps([1]))
        self.assertEqual(self.submissions[-1][0], ["remove", "#miners", "#miners[2]"])
        self.open_group()
        self.click("Delete group...")
        confirm = self.player.forms[-1]
        confirm.kwargs["on_submit"](self.player, 1)
        self.assertEqual(len(self.store.groups), 1)
        self.click("Delete group...")
        self.player.forms[-1].kwargs["on_submit"](self.player, 0)
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
        confirm.kwargs["on_submit"](self.player, 0)
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

    def test_result_cache_retains_only_latest_per_owner_group_and_has_global_bound(self):
        store = ResultStore()
        for index in range(CACHE_SIZE + 1):
            store.save(None, Group(str(index), "miners"), "#miners", "jump", [])
        self.assertEqual(len(store.records), CACHE_SIZE)
        self.assertNotIn(("console", "0"), store.records)
        group = Group("last", "miners")
        first = store.save(None, group, "#miners", "jump", [])
        second = store.save(None, group, "#miners", "stop", [])
        self.assertNotEqual(first.identity, second.identity)
        self.assertIs(store.get(None, group), second)
