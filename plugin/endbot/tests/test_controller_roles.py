"""Synthetic controller identities only; no accounts or tokens are used."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from endbot_core.controller_roles import DEFAULT_ROLE, ControllerRoles, load_controller_roles

XUID = "2535400000000001"
OTHER_XUID = "2535400000000002"


class ControllerRolesTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.path = self.root / "state" / "controller-roles.json"

    def make_symlink(self) -> Path:
        self.path.parent.mkdir(parents=True)
        target = self.path.parent / "target.json"
        target.write_text(json.dumps({"version": 1, "roles": {XUID: "controller"}}), encoding="utf-8")
        try:
            self.path.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("creating symlinks is not permitted on this platform")
        return target

    def test_missing_entry_is_a_scripter(self):
        roles = ControllerRoles(self.path)
        self.assertEqual(roles.role(XUID), DEFAULT_ROLE)
        self.assertEqual(roles.entries(), {})
        self.assertFalse(self.path.exists())

    def test_set_persists_a_minimal_versioned_document_across_restart(self):
        roles = ControllerRoles(self.path)
        roles.set(OTHER_XUID, "controller")
        roles.set(XUID, "scripter")
        text = self.path.read_text(encoding="utf-8")
        self.assertTrue(text.endswith("\n"))
        document = json.loads(text)
        self.assertEqual(document, {"version": 1, "roles": {XUID: "scripter", OTHER_XUID: "controller"}})
        self.assertEqual(list(document["roles"]), sorted(document["roles"]))
        reloaded = ControllerRoles(self.path)
        self.assertEqual(reloaded.entries(), {XUID: "scripter", OTHER_XUID: "controller"})
        self.assertEqual((reloaded.role(XUID), reloaded.role(OTHER_XUID)), ("scripter", "controller"))
        self.assertEqual(list(self.path.parent.glob(".controller-roles-*")), [])

    def test_forget_removes_entries_and_writes_only_when_something_changed(self):
        roles = ControllerRoles(self.path)
        roles.set(XUID, "controller")
        roles.set(OTHER_XUID, "controller")
        roles.forget(["", "2535400000000099", XUID])
        self.assertEqual(roles.entries(), {OTHER_XUID: "controller"})
        self.assertEqual(
            json.loads(self.path.read_text(encoding="utf-8")),
            {"version": 1, "roles": {OTHER_XUID: "controller"}},
        )
        with patch.object(ControllerRoles, "_save") as save:
            roles.forget([XUID, "2535400000000099"])
        save.assert_not_called()

    def test_memory_only_mode_writes_nothing(self):
        roles = ControllerRoles()
        roles.set(XUID, "controller")
        self.assertEqual(roles.role(XUID), "controller")
        roles.forget([XUID])
        self.assertEqual(roles.role(XUID), DEFAULT_ROLE)
        self.assertIsNone(roles.path)
        self.assertEqual(list(self.root.rglob("*")), [])

    def test_set_validates_identity_and_role_and_entries_is_a_copy(self):
        roles = ControllerRoles()
        roles.set(XUID, "controller")
        entries = roles.entries()
        entries[XUID] = "scripter"
        entries[OTHER_XUID] = "controller"
        self.assertEqual(roles.entries(), {XUID: "controller"})
        for xuid in ("", "0", "not-a-xuid", "\u0661\u0662"):
            with self.assertRaises(ValueError):
                roles.set(xuid, "scripter")
        for role in ("owner", "Controller", "", 1, None):
            with self.assertRaises(ValueError):
                roles.set(XUID, role)
        self.assertEqual(roles.entries(), {XUID: "controller"})

    def test_corrupt_file_is_refused(self):
        self.path.parent.mkdir(parents=True)
        documents = (
            "{not json",
            "[]",
            json.dumps({"version": 2, "roles": {}}),
            json.dumps({"roles": {}}),
            json.dumps({"version": 1, "roles": {}, "extra": True}),
            json.dumps({"version": 1, "roles": []}),
            json.dumps({"version": 1, "roles": None}),
            json.dumps({"version": 1, "roles": {"not-an-xuid": "scripter"}}),
            json.dumps({"version": 1, "roles": {"0": "scripter"}}),
            json.dumps({"version": 1, "roles": {"\u0661\u0662": "scripter"}}),
            json.dumps({"version": 1, "roles": {XUID: "owner"}}),
            json.dumps({"version": 1, "roles": {XUID: "Controller"}}),
            json.dumps({"version": 1, "roles": {XUID: 1}}),
            json.dumps({"version": 1, "roles": {XUID: True}}),
            json.dumps({"version": 1, "roles": {XUID: None}}),
        )
        for document in documents:
            with self.subTest(document=document):
                self.path.write_text(document, encoding="utf-8")
                with self.assertRaises((ValueError, TypeError)):
                    load_controller_roles(self.path)

    def test_missing_roles_key_reads_as_no_roles(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps({"version": 1}), encoding="utf-8")
        self.assertEqual(load_controller_roles(self.path), {})

    def test_directory_in_place_of_the_file_is_refused(self):
        self.path.mkdir(parents=True)
        with self.assertRaises(ValueError):
            load_controller_roles(self.path)

    def test_symlink_is_refused_for_reading_and_writing(self):
        roles = ControllerRoles(self.path)
        target = self.make_symlink()
        with self.assertRaises(ValueError):
            load_controller_roles(self.path)
        with self.assertRaises(ValueError):
            roles.set(XUID, "controller")
        self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"version": 1, "roles": {XUID: "controller"}})
        self.assertEqual(list(self.path.parent.glob(".controller-roles-*")), [])

    def test_failed_write_keeps_the_memory_change_and_the_old_file(self):
        roles = ControllerRoles(self.path)
        roles.set(XUID, "controller")
        with (
            patch("endbot_core.controller_roles.os.replace", side_effect=OSError("disk failure")),
            self.assertRaises(OSError),
        ):
            roles.set(XUID, "scripter")
        self.assertEqual(roles.role(XUID), "scripter")
        self.assertEqual(ControllerRoles(self.path).role(XUID), "controller")
        self.assertEqual(list(self.path.parent.glob(".controller-roles-*")), [])


if __name__ == "__main__":
    unittest.main()
