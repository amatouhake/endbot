"""Synthetic controller identities only; no accounts or tokens are used."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from endstone_endbot.controller_policy import ControllerPolicy
from endstone_endbot.enrollment import Binding

XUID = "123456789001"
UUID = "00000000-0000-4000-8000-000000000001"
OTHER_XUID = "123456789002"
OTHER_UUID = "00000000-0000-4000-8000-000000000002"


class ControllerPolicyTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "controller-policy.json"
        self.policy = ControllerPolicy(self.path)

    def test_pending_add_binds_only_authenticated_join_and_survives_restart(self):
        self.policy.add("Some Player")
        self.assertFalse(self.policy.enroll("Some Player", "", UUID))
        self.assertFalse(self.policy.grants(XUID))
        self.assertTrue(self.policy.enroll("some player", XUID, UUID))
        reloaded = ControllerPolicy(self.path)
        self.assertTrue(reloaded.grants(XUID))
        self.assertFalse(reloaded.enroll("Some Player", OTHER_XUID, OTHER_UUID))
        self.assertFalse(reloaded.grants(OTHER_XUID))
        self.assertEqual(reloaded.state.pending, ())

    def test_online_grant_remove_and_regrant_are_persistent(self):
        self.policy.add("Player", xuid=XUID, player_uuid=UUID)
        self.policy.remove("Player", known=(), configured=())
        reloaded = ControllerPolicy(self.path)
        self.assertTrue(reloaded.denied(XUID, UUID))
        self.assertFalse(reloaded.grants(XUID))
        reloaded.add("Renamed", xuid=XUID, player_uuid=UUID)
        self.assertTrue(reloaded.grants(XUID))
        self.assertFalse(reloaded.denied(XUID, UUID))
        self.assertTrue(reloaded.name_denied("Player"))

    def test_removing_bootstrap_binding_blocks_renamed_account_and_legacy_identity(self):
        binding = Binding("Original", XUID, UUID, "synthetic")
        self.policy.remove("Original", known=(binding,), configured=("Original",))
        self.assertTrue(ControllerPolicy(self.path).denied(XUID, UUID))
        self.assertTrue(self.policy.name_denied("Original"))

    def test_pending_bootstrap_removal_blocks_enrollment_until_explicit_add(self):
        self.policy.remove("Pending", known=(), configured=("Pending",))
        self.assertTrue(self.policy.name_denied("Pending"))
        self.policy.add("Pending")
        self.assertFalse(self.policy.name_denied("Pending"))
        self.assertTrue(self.policy.enroll("Pending", XUID, UUID))

    def test_pending_name_cannot_lift_an_identity_revocation(self):
        self.policy.remove(f"xuid:{XUID}", known=(), configured=())
        self.policy.add("Newcomer")
        # The revoked account renames itself to the pending GamerTag.
        self.assertFalse(self.policy.enroll("Newcomer", XUID, OTHER_UUID))
        self.assertFalse(self.policy.grants(XUID))
        self.assertTrue(self.policy.denied(XUID, OTHER_UUID))
        self.assertEqual(self.policy.state.pending, ("Newcomer",))
        # Only an explicit add naming the authenticated account restores it.
        self.policy.add("Newcomer", xuid=XUID, player_uuid=OTHER_UUID)
        self.assertTrue(self.policy.grants(XUID))

    def test_offline_legacy_revocation_uses_identity_selectors(self):
        self.policy.remove(f"xuid:{XUID}", known=(), configured=())
        self.assertTrue(self.policy.denied(XUID, UUID))
        self.policy.remove(f"uuid:{OTHER_UUID}", known=(), configured=())
        self.assertTrue(self.policy.denied(OTHER_XUID, OTHER_UUID))
        with self.assertRaisesRegex(ValueError, "Unknown offline"):
            self.policy.remove("Unknown", known=(), configured=())

    def test_offline_regrant_preserves_revoked_dynamic_binding(self):
        self.policy.add("Original", xuid=XUID, player_uuid=UUID)
        self.policy.remove("Original", known=(), configured=())
        reloaded = ControllerPolicy(self.path)
        reloaded.add("Original")
        self.assertTrue(reloaded.grants(XUID))
        self.assertEqual(reloaded.state.pending, ())
        self.assertFalse(reloaded.enroll("Original", OTHER_XUID, OTHER_UUID))
        self.assertFalse(reloaded.grants(OTHER_XUID))

    def test_failed_write_never_changes_memory_or_previous_policy(self):
        self.policy.add("Player", xuid=XUID, player_uuid=UUID)
        prior = self.path.read_bytes()
        with (
            patch("endstone_endbot.controller_policy.os.replace", side_effect=OSError("disk full")),
            self.assertRaises(OSError),
        ):
            self.policy.remove("Player", known=(), configured=())
        self.assertTrue(self.policy.grants(XUID))
        self.assertEqual(self.path.read_bytes(), prior)
        self.assertEqual([entry.name for entry in self.path.parent.iterdir()], [self.path.name])

    def test_corrupt_state_fails_closed(self):
        for content in (
            "{",
            '{"version":2}',
            '{"version":1,"grants":[{}]}',
            '{"version":1,"deniedXuids":["not-xuid"]}',
        ):
            with self.subTest(content=content):
                self.path.write_text(content)
                with self.assertRaises((ValueError, TypeError)):
                    ControllerPolicy(self.path)

    def test_invalid_online_bot_identity_and_line_breaks_cannot_grant(self):
        for xuid in ("", "0", "not-xuid"):
            with self.assertRaises(ValueError):
                self.policy.add("Bot", xuid=xuid, player_uuid=UUID)
        with self.assertRaises(ValueError):
            self.policy.add("Player\nstop")
        self.assertFalse(self.path.exists())

    def test_denied_names_and_ids_are_committed_together(self):
        self.policy.remove("Player", known=(Binding("Player", XUID, UUID, "synthetic"),), configured=())
        document = json.loads(self.path.read_text())
        self.assertIn("player", document["deniedNames"])
        self.assertIn(XUID, document["deniedXuids"])
        self.assertIn(UUID, document["deniedUuids"])
