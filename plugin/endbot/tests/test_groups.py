import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from endbot_core.commands import strip_colors
from endbot_core.control import RuntimeControlError
from endbot_core.groups import GroupCommandService, GroupStore
from test_command_service import FakeControl, FakeWorld


def messages(result):
    return "\n".join(strip_colors(message) for message in result.messages)


class GroupControl(FakeControl):
    def request(self, operation, **parameters):
        if "expectedIdentityId" in parameters:
            profile = self.bots.get(parameters["name"])
            if profile is None or profile["identityId"] != parameters["expectedIdentityId"]:
                raise RuntimeControlError("identity_changed", "Bot identity changed; group operation canceled")
        if operation == "capabilities":
            return {"identityPinnedRequests": True, "groupInputBatch": True}
        if operation == "groupInputBatch":
            self.calls.append((operation, parameters))
            results = []
            for request in parameters["requests"]:
                try:
                    results.append({"ok": True, "result": self.request(request["method"], **request["params"])})
                except RuntimeControlError as error:
                    results.append({"ok": False, "error": {"code": error.code, "message": str(error)}})
            return {"results": results}
        return super().request(operation, **parameters)


class GroupStoreTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "groups.json"
        self.store = GroupStore(self.path)

    def test_atomic_save_failure_preserves_memory_and_disk(self):
        self.store.create("miners")
        before = self.path.read_bytes()
        with (
            patch("endbot_core.groups.os.replace", side_effect=OSError("disk unavailable")),
            self.assertRaises(OSError),
        ):
            self.store.create("farmers")
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual([group.name for group in self.store.groups], ["miners"])
        self.assertEqual(list(self.path.parent.glob(".groups-*.tmp")), [])

    def test_registration_order_roundtrips_and_removal_readdition_appends(self):
        self.store.create("miners")
        alice, bob = "00000000-0000-4000-8000-000000000001", "00000000-0000-4000-8000-000000000002"
        self.store.add("miners", alice)
        self.store.add("miners", bob)
        self.store.remove("miners", alice)
        self.store.add("miners", alice)
        restored = GroupStore(self.path)
        self.assertEqual(restored.get("MINERS").members, (bob, alice))
        self.assertEqual(restored.select("#miners[1]")[1], ((1, bob),))

    def test_rejects_ambiguous_names_indexes_and_corrupt_state(self):
        self.store.create("miners")
        for name in ("MINERS", "../miners", "#miners", "123", "with space"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.store.create(name)
        for selector in ("#miners[0]", "#miners[-1]", "#miners[1+1]", "#miners[*]", "#miners[1]", "#absent"):
            with self.subTest(selector=selector), self.assertRaises(ValueError):
                self.store.select(selector)
        self.path.write_text(json.dumps({"version": 2, "groups": []}), encoding="utf-8")
        with self.assertRaises(ValueError):
            GroupStore(self.path)
        for entry in ({"name": "miners", "members": []}, {"identityId": 7, "name": "miners", "members": []}):
            self.path.write_text(json.dumps({"version": 1, "groups": [entry]}), encoding="utf-8")
            with self.subTest(entry=entry), self.assertRaises(ValueError):
                GroupStore(self.path)


class GroupServiceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.store = GroupStore(Path(temporary.name) / "groups.json")
        self.control, self.world = GroupControl(), FakeWorld()
        self.allowed = True
        self.service = GroupCommandService(self.control, self.world, self.store, lambda _: self.allowed)
        for name in ("Alice", "Bob", "Carol"):
            self.service.execute([name, "spawn"])
        self.manage("create", "#miners")
        self.manage("add", "#miners", "Alice")
        self.manage("add", "#miners", "Bob")
        self.control.calls.clear()

    def manage(self, *arguments):
        return self.service.execute_group(list(arguments))

    def test_member_lookup_uses_light_profiles_not_full_status(self):
        self.service.execute(["#miners", "stop"])
        self.assertIn("profiles", [op for op, _ in self.control.calls])
        self.assertNotIn("list", [op for op, _ in self.control.calls])

    def test_huge_indexes_uppercase_uuids_and_unwritable_files_fail_cleanly(self):
        for target in ("#miners[" + "9" * 5000 + "]", "#miners[1000000000]"):
            with self.subTest(target=target[:20]):
                self.assertIn("Group target syntax", messages(self.service.execute([target, "stop"])))
        self.assertIn("positive integer", messages(self.manage("result", "#miners", "9" * 5000)))
        identity = self.control.bots["Alice"]["identityId"]
        self.store.remove("miners", identity.upper())
        self.assertNotIn(identity, self.store.get("miners").members)
        with patch.object(self.store, "_save", side_effect=PermissionError(13, "denied", r"C:\secret\groups.json")), \
                self.assertLogs("endbot_core.groups", level="ERROR") as logs:
            text = messages(self.manage("create", "#other"))
        self.assertIn("Groups could not be saved", text)
        self.assertNotIn("secret", text)
        self.assertIn("secret", "\n".join(logs.output))

    def test_index_and_rename_follow_uuid_after_service_restart(self):
        identity = self.control.bots["Alice"]["identityId"]
        result = self.service.execute(["#miners[1]", "rename", "Alicia"])
        self.assertIn("Alicia", messages(result))
        self.assertEqual(self.store.get("miners").members[0], identity)
        service = GroupCommandService(self.control, self.world, GroupStore(self.store.path), lambda _: True)
        result = service.execute(["#miners[1]", "stop"])
        self.assertIn("Alicia", messages(result))
        self.assertEqual(self.control.calls[-1], ("stop", {"name": "Alicia", "expectedIdentityId": identity}))

    def test_missing_profile_retains_its_index_and_does_not_spawn_a_replacement(self):
        del self.control.bots["Alice"]
        result = self.service.execute(["#miners", "spawn"])
        self.assertIn("missing Bot profile 1", messages(result))
        self.assertEqual((result.accepted, result.code), (False, "not_found"))
        self.assertIn("Bob", messages(self.manage("result", "#miners")))
        self.assertNotIn("Alice", self.control.bots)
        self.assertEqual(len(self.store.get("miners").members), 2)
        removed = self.manage("remove", "#miners", "#miners[1]")
        self.assertIn("remove", messages(removed))
        self.assertEqual(len(self.store.get("miners").members), 1)

    def test_unexpected_member_failure_keeps_earlier_results(self):
        original = self.control.request
        def request(operation, **parameters):
            if operation == "spawn" and parameters["name"] == "Bob":
                raise KeyError("connectionState")
            return original(operation, **parameters)
        self.control.request = request
        result = self.service.execute(["#miners", "spawn"])
        text = messages(result)
        self.assertIn("accepted 1", text)
        self.assertIn("unknown 1", text)
        self.assertEqual(result.code, "unavailable")
        history = messages(self.manage("result", "#miners"))
        self.assertIn("Alice: accepted", history)
        self.assertIn("Bob: acceptance unknown", history)

    def test_profiles_are_read_once_per_group_operation(self):
        self.service.execute(["#miners", "spawn"])
        self.assertEqual(sum(op == "profiles" for op, _ in self.control.calls), 1)

    def test_management_replies_are_localized(self):
        source = SimpleNamespace(language="ja")
        missing = self.service.execute_group(["delete", "#absent"], source)
        self.assertIn("グループ absent はありません", messages(missing))
        self.assertIn("グループを作成しました", messages(self.service.execute_group(["create", "#ja"], source)))

    def test_unloadable_store_disables_groups_but_not_bots(self):
        service = GroupCommandService(self.control, self.world, None, lambda _: True)
        self.assertIn("groups file could not be loaded", messages(service.execute_group(["list"])))
        self.assertIn("groups file could not be loaded", messages(service.execute(["#miners", "stop"])))
        self.assertIn("Alice", messages(service.execute(["Alice", "stop"])))

    def test_batch_snapshot_excludes_members_added_during_execution(self):
        original = self.control.request
        def request(operation, **parameters):
            if operation == "stop" and parameters["name"] == "Alice":
                self.store.add("miners", self.control.bots["Carol"]["identityId"])
            return original(operation, **parameters)
        self.control.request = request
        self.service.execute(["#miners", "stop"])
        self.assertEqual([params["name"] for op, params in self.control.calls if op == "stop"], ["Alice", "Bob"])

    def test_member_failure_does_not_skip_later_members(self):
        original = self.control.request
        def request(operation, **parameters):
            if operation == "stop" and parameters["name"] == "Alice":
                raise RuntimeControlError("offline", "Bot is offline")
            return original(operation, **parameters)
        self.control.request = request
        result = self.service.execute(["#miners", "stop"])
        self.assertIn("accepted 1, failed 1", messages(result))
        detail = messages(self.manage("result", "#miners"))
        self.assertIn("Alice: failed - Bot is offline", detail)
        self.assertIn("Bob: accepted - movement and actions stopped", detail)

    def test_revocation_cancels_rest_of_batch_and_keeps_already_completed_result(self):
        original = self.control.request
        def request(operation, **parameters):
            result = original(operation, **parameters)
            if operation == "despawn":
                self.allowed = False
            return result
        self.control.request = request
        result = self.service.execute(["#miners", "despawn"])
        self.assertEqual([params["name"] for op, params in self.control.calls if op == "despawn"], ["Alice"])
        self.assertIn("accepted 1, failed 0, not run 1", messages(result))
        rows = self.service.results.get(None, self.store.get("miners")).rows
        self.assertIn("despawn requested", rows[0].detail)
        self.assertEqual(rows[1].outcome, "skipped")

    def test_revocation_during_lookup_prevents_persistent_membership_change(self):
        original = self.control.request
        def request(operation, **parameters):
            result = original(operation, **parameters)
            if operation == "profiles":
                self.allowed = False
            return result
        self.control.request = request
        result = self.manage("add", "#miners", "Carol")
        self.assertIn("permission was revoked", messages(result))
        self.assertEqual(len(self.store.get("miners").members), 2)

    def test_broadcast_identity_changes_are_rejected_before_any_runtime_request(self):
        for arguments in (["#miners", "rename", "Oops"], ["#miners", "forget"]):
            result = self.service.execute(arguments)
            self.assertIn("requires one Bot", messages(result))
        self.assertEqual(self.control.calls, [])

    def test_group_inputs_use_one_snapshot_and_one_pinned_batch_with_ordinary_replies(self):
        for arguments in (["jump"], ["move", "forward", "for", "20"], ["hotbar", "2"],
                          ["look", "at", "1", "64", "2"], ["sprint", "on"], ["drop"]):
            self.control.calls.clear()
            result = self.service.execute(["#miners", *arguments])
            self.assertIn("accepted 2, failed 0", messages(result))
            self.assertEqual([row.name for row in self.service.results.get(None, self.store.get("miners")).rows],
                             ["Alice", "Bob"])
            batches = [p for op, p in self.control.calls if op == "groupInputBatch"]
            self.assertEqual(len(batches), 1)
            self.assertEqual(sum(op == "profiles" for op, _ in self.control.calls), 1)
            entries = batches[0]["requests"]
            self.assertEqual([entry["params"]["expectedIdentityId"] for entry in entries],
                             list(self.store.get("miners").members))
        self.assertEqual(len(self.world.look_targets), 2)

    def test_revocation_or_shutdown_during_preparation_prevents_all_inputs(self):
        original = self.world.look_at
        for cancel in (lambda: setattr(self, "allowed", False), self.service.cancel_groups):
            self.allowed, self.service._groups_open = True, True
            self.control.calls.clear()
            def look_at(*args, cancel=cancel):
                cancel()
                return original(*args)
            self.world.look_at = look_at
            result = self.service.execute(["#miners", "look", "at", "1", "64", "2"])
            self.assertIn("accepted 0, failed 0, not run 2", messages(result))
            self.assertFalse(any(op == "groupInputBatch" for op, _ in self.control.calls))

    def test_missing_member_is_reported_in_order_and_does_not_retarget_by_name(self):
        del self.control.bots["Alice"]
        result = self.service.execute(["#miners", "jump"])
        self.assertIn("missing Bot profile 1", messages(result))
        self.assertIn("Bob: accepted - jump once requested", messages(self.manage("result", "#miners")))
        batch = next(p for op, p in self.control.calls if op == "groupInputBatch")
        self.assertEqual(len(batch["requests"]), 1)

    def test_old_group_runtime_cannot_fall_back_to_staggered_inputs(self):
        original = self.control.request
        self.control.request = lambda op, **p: ({"identityPinnedRequests": True} if op == "capabilities"
                                                 else original(op, **p))
        result = self.service.execute(["#miners", "jump"])
        self.assertIn("not run 2", messages(result))
        self.assertIn("matching updated Endbot runtime", messages(self.manage("result", "#miners")))
        self.assertEqual(self.control.calls, [])

    def test_batched_inputs_keep_the_controllers_reply_language(self):
        result = self.service.execute(["#miners", "jump"], SimpleNamespace(language="ja"))
        self.assertIn("受付 2, 失敗 0", messages(result))
        self.assertTrue(all("accepted" in strip_colors(line) for line in result.log_messages))
        details = self.service.execute_group(["result", "#miners"], SimpleNamespace(language="ja"))
        self.assertIn("Alice: 受付 - jump once を要求しました", messages(details))

    def test_delete_only_removes_the_group_and_shutdown_prevents_further_work(self):
        self.manage("delete", "#miners")
        self.assertEqual(set(self.control.bots), {"Alice", "Bob", "Carol"})
        self.service.cancel_groups()
        result = self.manage("create", "#miners")
        self.assertIn("command canceled", messages(result))
        self.assertEqual(self.store.groups, ())

    def test_old_runtime_is_rejected_before_any_bot_operation(self):
        self.control.request = lambda *args, **kwargs: {"identityPinnedRequests": False}
        result = self.service.execute(["#miners", "stop"])
        self.assertIn("not run 2", messages(result))
        self.assertIn("matching updated Endbot runtime", messages(self.manage("result", "#miners")))
        self.assertEqual(self.control.calls, [])

    def test_list_scopes_to_members_and_existing_saved_groups_need_no_migration(self):
        self.service.groups = GroupStore(self.store.path)
        self.assertEqual(messages(self.manage("list")), "Endbot: #miners (2)")
        result = messages(self.manage("list", "#MINERS"))
        self.assertIn("#miners[1]: Alice", result)
        self.assertIn("#miners[2]: Bob", result)
        self.assertEqual([row["name"] for row in json.loads(self.store.path.read_text())["groups"]], ["miners"])
        self.manage("create", "#empty")
        self.assertIn("#empty: no members", messages(self.manage("list", "#empty")))

    def test_unprefixed_or_indexed_group_arguments_and_members_are_rejected_without_effects(self):
        before = self.store.path.read_bytes()
        for command in (["create", "new"], ["delete", "miners"], ["list", "miners"],
                        ["add", "miners", "Carol"], ["remove", "miners", "Alice"],
                        ["list", "#miners[1]"], ["create", "##new"], ["create", "#"],
                        ["list", "#miners", "extra"]):
            with self.subTest(command=command):
                text = messages(self.manage(*command))
                self.assertTrue("Group syntax" in text or "Invalid group command" in text, text)
                self.assertEqual(self.store.path.read_bytes(), before)
        self.assertIn("Invalid group command", messages(self.manage("members", "#miners")))
        self.assertEqual(self.control.calls, [])

    def test_help_and_mutation_flow_use_the_same_prefixed_group_references(self):
        help_text = messages(self.manage("help"))
        self.assertIn("list [#group]", help_text)
        self.assertNotIn("members <", help_text)
        self.assertIn("remove: Alice", messages(self.manage("remove", "#miners", "Alice")))
        self.assertIn("add: Carol", messages(self.manage("add", "#miners", "Carol")))
        self.assertEqual([identity for _, identity in self.store.select("#miners")[1]],
                         [self.control.bots[name]["identityId"] for name in ("Bob", "Carol")])
        self.assertIn("group deleted; Bots retained", messages(self.manage("delete", "#miners")))
        self.assertIn("no groups", messages(self.manage("list")))


if __name__ == "__main__":
    unittest.main()
