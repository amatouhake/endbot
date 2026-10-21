import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_command_service import FakeControl, FakeWorld

from endstone_endbot.commands import strip_colors
from endstone_endbot.control import RuntimeControlError
from endstone_endbot.groups import GroupCommandService, GroupStore


def messages(result):
    return "\n".join(strip_colors(message) for message in result.messages)


class GroupControl(FakeControl):
    def request(self, operation, **parameters):
        if operation == "capabilities":
            return {"identityPinnedRequests": True}
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
            patch("endstone_endbot.groups.os.replace", side_effect=OSError("disk unavailable")),
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
        self.manage("create", "miners")
        self.manage("add", "miners", "Alice")
        self.manage("add", "miners", "Bob")
        self.control.calls.clear()

    def manage(self, *arguments):
        return self.service.execute_group(list(arguments))

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
        self.assertIn("#miners[1]: missing Bot profile", messages(result))
        self.assertIn("Bob", messages(result))
        self.assertNotIn("Alice", self.control.bots)
        self.assertEqual(len(self.store.get("miners").members), 2)
        removed = self.manage("remove", "miners", "#miners[1]")
        self.assertIn("remove", messages(removed))
        self.assertEqual(len(self.store.get("miners").members), 1)

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
        self.assertIn("#miners[1]: Bot is offline", messages(result))
        self.assertIn("Bob: movement and actions stopped", messages(result))

    def test_revocation_cancels_rest_of_batch_and_keeps_already_completed_result(self):
        original = self.control.request
        def request(operation, **parameters):
            result = original(operation, **parameters)
            if operation == "stop":
                self.allowed = False
            return result
        self.control.request = request
        result = self.service.execute(["#miners", "stop"])
        self.assertEqual([params["name"] for op, params in self.control.calls if op == "stop"], ["Alice"])
        self.assertIn("Alice: movement and actions stopped", messages(result))
        self.assertIn("permission was revoked", messages(result))

    def test_revocation_during_lookup_prevents_persistent_membership_change(self):
        original = self.control.request
        def request(operation, **parameters):
            result = original(operation, **parameters)
            if operation == "list":
                self.allowed = False
            return result
        self.control.request = request
        result = self.manage("add", "miners", "Carol")
        self.assertIn("permission was revoked", messages(result))
        self.assertEqual(len(self.store.get("miners").members), 2)

    def test_broadcast_identity_changes_are_rejected_before_any_runtime_request(self):
        for arguments in (["#miners", "rename", "Oops"], ["#miners", "forget"]):
            result = self.service.execute(arguments)
            self.assertIn("requires one Bot", messages(result))
        self.assertEqual(self.control.calls, [])

    def test_delete_only_removes_the_group_and_shutdown_prevents_further_work(self):
        self.manage("delete", "miners")
        self.assertEqual(set(self.control.bots), {"Alice", "Bob", "Carol"})
        self.service.cancel_groups()
        result = self.manage("create", "miners")
        self.assertIn("command canceled", messages(result))
        self.assertEqual(self.store.groups, ())

    def test_old_runtime_is_rejected_before_any_bot_operation(self):
        self.control.request = lambda *args, **kwargs: {"identityPinnedRequests": False}
        result = self.service.execute(["#miners", "stop"])
        self.assertIn("matching updated Endbot runtime", messages(result))
        self.assertEqual(self.control.calls, [])


if __name__ == "__main__":
    unittest.main()
