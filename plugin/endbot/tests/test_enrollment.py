import json
import tempfile
import unittest
from pathlib import Path

from endstone_endbot.enrollment import ControllerEnrollment, EnrollmentError, load_bindings, parse_gamertags

OWNER_XUID = "2535469543141592"
OWNER_UUID = "63572362-0c83-5f0a-8cec-e1b788101798"
OTHER_XUID = "2535400000000001"
OTHER_UUID = "526290d1-a78e-4a18-b32e-8c0d14e10850"


class ControllerEnrollmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.data = Path(self.directory.name)
        self.path = self.data / "controllers.json"

    def enrollment(self, *gamertags: str) -> ControllerEnrollment:
        return ControllerEnrollment.from_config(
            {"controller-gamertags": list(gamertags), "controllers-file": str(self.path)}, self.data
        )

    def test_first_authenticated_join_binds_the_pending_gamertag(self) -> None:
        enrollment = self.enrollment("OwnerTag")
        self.assertEqual(enrollment.pending(), ("OwnerTag",))

        authorized, binding = enrollment.authorize("ownertag", OWNER_XUID, OWNER_UUID.upper())

        self.assertTrue(authorized)
        self.assertEqual((binding.gamertag, binding.xuid, binding.uuid), ("OwnerTag", OWNER_XUID, OWNER_UUID))
        self.assertEqual(enrollment.pending(), ())
        stored = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(stored["version"], 1)
        self.assertEqual(stored["bindings"][0]["xuid"], OWNER_XUID)

    def test_binding_survives_restart_and_gamertag_change(self) -> None:
        self.enrollment("OwnerTag").authorize("OwnerTag", OWNER_XUID, OWNER_UUID)

        restarted = self.enrollment("OwnerTag")

        self.assertEqual(restarted.authorize("RenamedOwner", OWNER_XUID, OWNER_UUID), (True, None))

    def test_bound_gamertag_cannot_be_claimed_by_another_account(self) -> None:
        enrollment = self.enrollment("OwnerTag")
        enrollment.authorize("OwnerTag", OWNER_XUID, OWNER_UUID)

        self.assertEqual(enrollment.authorize("OwnerTag", OTHER_XUID, OTHER_UUID), (False, None))
        self.assertEqual(len(load_bindings(self.path)), 1)

    def test_players_without_xuid_never_bind(self) -> None:
        enrollment = self.enrollment("Alice")

        for xuid in ("", "0", "not-a-number", "１２３"):
            self.assertEqual(enrollment.authorize("Alice", xuid, OWNER_UUID), (False, None))
        self.assertFalse(self.path.exists())
        self.assertEqual(enrollment.pending(), ("Alice",))

    def test_unlisted_players_are_not_authorized(self) -> None:
        self.assertEqual(self.enrollment("OwnerTag").authorize("Stranger", OTHER_XUID, OTHER_UUID), (False, None))
        self.assertFalse(self.path.exists())

    def test_removing_a_gamertag_revokes_its_binding(self) -> None:
        self.enrollment("OwnerTag", "FriendTag").authorize("FriendTag", OTHER_XUID, OTHER_UUID)

        revoked = self.enrollment("OwnerTag")

        self.assertEqual(revoked.authorize("FriendTag", OTHER_XUID, OTHER_UUID), (False, None))
        self.assertEqual(revoked.pending(), ("OwnerTag",))

    def test_new_binding_drops_revoked_entries_from_the_file(self) -> None:
        self.enrollment("OwnerTag", "FriendTag").authorize("FriendTag", OTHER_XUID, OTHER_UUID)

        self.enrollment("OwnerTag").authorize("OwnerTag", OWNER_XUID, OWNER_UUID)

        self.assertEqual([binding.gamertag for binding in load_bindings(self.path)], ["OwnerTag"])

    def test_no_gamertags_and_no_file_is_fail_closed(self) -> None:
        enrollment = ControllerEnrollment.from_config({}, self.data)

        self.assertEqual(enrollment.authorize("OwnerTag", OWNER_XUID, OWNER_UUID), (False, None))

    def test_invalid_configuration_is_rejected(self) -> None:
        with self.assertRaises(TypeError):
            parse_gamertags("OwnerTag")
        with self.assertRaises(TypeError):
            parse_gamertags([1234])
        with self.assertRaises(TypeError):
            parse_gamertags([" "])
        with self.assertRaises(ValueError):
            parse_gamertags(["OwnerTag", "ownertag"])
        with self.assertRaises(EnrollmentError):
            ControllerEnrollment.from_config({"controller-gamertags": ["OwnerTag"]}, self.data)

    def test_corrupt_bindings_file_is_rejected(self) -> None:
        for content in ("{", '{"version": 2, "bindings": []}', '{"version": 1, "bindings": [{"gamertag": "x"}]}'):
            self.path.write_text(content, encoding="utf-8")
            with self.assertRaises(EnrollmentError):
                self.enrollment("OwnerTag")


if __name__ == "__main__":
    unittest.main()
