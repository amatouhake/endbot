import json
import tempfile
import unittest
from pathlib import Path

from endbot_cli.controller_status import load_policy, saved_report
from endbot_cli.controllers import ControllersError, ControllerState


class ControllerStatusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "policy.json"

    def test_policy_names_and_uuids_match_plugin_normalization(self):
        self.path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "pending": ["  Pending  "],
                    "deniedNames": [" Revoked "],
                    "deniedUuids": ["AAAAAAAA-BBBB-4CCC-8DDD-EEEEEEEEEEEE"],
                }
            )
        )
        policy = load_policy(self.path)
        self.assertEqual(policy["pending"], ["Pending"])
        self.assertEqual(policy["deniedNames"], ["Revoked"])
        self.assertEqual(policy["deniedUuids"], ["aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"])

    def test_legacy_identity_revocation_is_not_hidden_by_allowlist(self):
        self.path.write_text(json.dumps({"version": 1, "deniedXuids": ["123456789001"]}))
        rows = saved_report(
            (), ControllerState(), load_policy(self.path), {"allowed-xuids": ["123456789001", "123456789002"]}
        )
        self.assertEqual(
            [(row["state"], row["reason"]) for row in rows],
            [
                ("revoked", "revoked XUID"),
                ("conditional", "legacy XUID allowlist; requires Xbox authentication and identity checks"),
            ],
        )

    def test_malformed_policy_and_legacy_entries_fail_diagnostics(self):
        for value in (
            {"version": 2},
            {"version": 1, "grants": [None]},
            {"version": 1, "deniedUuids": [1]},
            {"version": 1, "pending": ["bad\nname"]},
        ):
            self.path.write_text(json.dumps(value))
            with self.assertRaises(ControllersError):
                load_policy(self.path)
        self.path.unlink()
        for legacy in ({"allowed-uuids": ["invalid"]}, {"allowed-xuids": [1]}):
            with self.assertRaises(ControllersError):
                saved_report((), ControllerState(), load_policy(self.path), legacy)
