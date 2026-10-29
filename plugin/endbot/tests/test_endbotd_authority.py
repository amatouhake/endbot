"""endbotd's authorization: enrollment, controller policy and the two-layer check, driven by presence (P4)."""

import json
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from endbot_core.authorization import AllowedPlayers
from endbot_core.controller_policy import ControllerPolicy
from endbot_core.enrollment import ControllerEnrollment
from endbot_core.grant_revisions import GrantRevisions
from endbot_core.link import Presence
from endbot_core.principal import Principal, as_job
from endbotd.authority import Authority

OWNER_ID = "11111111-2222-4333-8444-555555555555"


def player(name="Owner", xuid="2535412345678901", player_id=OWNER_ID):
    return {"playerId": player_id, "name": name, "xuid": xuid}


class AuthorityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def authority(self, **options):
        presence = Presence()
        presence.reset("epoch-1")
        options.setdefault("policy", ControllerPolicy(self.root / "policy.json"))
        options.setdefault("grant_revisions", GrantRevisions(self.root / "grant-revisions.json"))
        return Authority(presence, **options)

    def join(self, authority, record):
        return authority.joined(authority.presence.apply({"kind": "join", "player": record}))

    def quit(self, authority, record):
        authority.presence.apply({"kind": "quit", "playerId": record["playerId"]})
        authority.quit(record["playerId"])

    def principal(self, authority, record):
        """What the link derives for a command this player types now."""
        player_id = record["playerId"]
        return Principal("player", name=record["name"], xuid=record["xuid"],
                         grant_revision=authority.grant_revisions.get(player_id), player_id=player_id,
                         authorization_revision=authority.presence.session_revision(player_id))

    def allows(self, authority, record):
        return authority.allows(record)

    def test_join_binds_pending_controller_and_grants_only_control(self):
        authority = Authority.from_config(
            _presence(), {"controller-gamertags": ["OwnerTag"], "controllers-file": "controllers.json"}, self.root)
        bot = player("OwnerTag", "", "3890c6b6-74cf-4bce-8fc3-6bf7f1cc513b")
        owner = player("OwnerTag", "2535469543141592", "63572362-0c83-5f0a-8cec-e1b788101798")
        impostor = player("OwnerTag", "2535400000000001", "526290d1-a78e-4a18-b32e-8c0d14e10850")
        self.assertEqual(self.join(authority, bot), [(bot["playerId"], False)])
        self.assertEqual(self.join(authority, owner), [(owner["playerId"], True)])
        self.assertEqual(self.join(authority, impostor), [(impostor["playerId"], False)])
        self.assertTrue((self.root / "controllers.json").is_file())

    def test_live_grant_revoke_regrant_cancels_old_sources_and_survives_restart(self):
        authority, owner = self.authority(), player()
        self.assertEqual(self.join(authority, owner), [(OWNER_ID, False)])
        _, permissions = authority.manage(["add", owner["name"]])
        self.assertEqual(permissions, [(OWNER_ID, True)])
        source = self.principal(authority, owner)
        self.assertTrue(authority.authorize(source))
        _, permissions = authority.manage(["remove", owner["name"]])
        self.assertEqual(permissions, [(OWNER_ID, False)])
        self.assertFalse(authority.authorize(source))
        authority.manage(["add", owner["name"]])
        self.assertTrue(self.allows(authority, owner))
        self.assertFalse(authority.authorize(source))
        self.assertTrue(self.allows(self.authority(), owner))
        current = self.principal(authority, owner)
        self.assertTrue(authority.authorize(current))
        self.quit(authority, owner)
        self.assertFalse(authority.authorize(current))
        self.join(authority, owner)
        self.assertFalse(authority.authorize(current))
        self.assertTrue(authority.authorize(self.principal(authority, owner)))

    def test_a_permission_change_ends_commands_queued_under_the_old_session(self):
        authority, owner = self.authority(), player()
        self.join(authority, owner)
        authority.manage(["add", owner["name"]])
        source = self.principal(authority, owner)
        # A reconnect snapshot keeps the player allowed but starts a new session revision.
        authority.presence.reset("epoch-2")
        authority.joined(authority.presence.apply({"kind": "snapshot", "players": [owner]}), snapshot=True)
        self.assertFalse(authority.authorize(source))
        self.assertTrue(authority.authorize(self.principal(authority, owner)))

    def test_without_presence_only_runs_jobs_and_the_console_pass(self):
        authority, owner = self.authority(), player()
        self.join(authority, owner)
        authority.manage(["add", owner["name"]])
        source = self.principal(authority, owner)
        authority.presence.reset(None)
        self.assertFalse(authority.authorize(source))
        self.assertTrue(authority.authorize(as_job(source)))
        self.assertTrue(authority.authorize(Principal()))

    def test_mining_loop_survives_controller_logoff_but_not_revocation(self):
        authority, owner = self.authority(), player()
        self.join(authority, owner)
        authority.manage(["add", owner["name"]])
        source = self.principal(authority, owner)
        self.assertEqual((source.xuid, source.name), (owner["xuid"], owner["name"]))
        job = as_job(source)
        self.quit(authority, owner)
        # Queued commands are invalidated by logoff; a running job is not.
        self.assertFalse(authority.authorize(source))
        self.assertTrue(authority.authorize(job))
        authority.manage(["remove", f"xuid:{owner['xuid']}"])
        self.assertFalse(authority.authorize(job))
        self.assertTrue(authority.authorize(as_job(Principal())))

    def test_revoke_and_regrant_never_revive_an_older_job(self):
        authority, owner = self.authority(), player()
        self.join(authority, owner)
        self.assertTrue(authority.operation(["add", owner["name"]])[0]["ok"])
        job = as_job(self.principal(authority, owner))
        self.assertTrue(authority.authorize(job))
        self.assertTrue(authority.operation(["remove", f"xuid:{owner['xuid']}"])[0]["ok"])
        self.assertTrue(authority.operation(["add", owner["name"]])[0]["ok"])
        self.assertTrue(self.allows(authority, owner))
        self.assertFalse(authority.authorize(job))
        self.assertTrue(authority.authorize(as_job(self.principal(authority, owner))))

    def test_script_grant_revision_survives_logout_but_revoke_regrant_never_revives_it(self):
        authority, owner = self.authority(), player()
        self.join(authority, owner)
        authority.operation(["add", owner["name"]])
        source = replace(self.principal(authority, owner), kind="run", run_id="run1")
        self.assertTrue(authority.authorize(source))
        self.quit(authority, owner)
        self.assertTrue(authority.authorize(source))
        authority.operation(["remove", f"xuid:{owner['xuid']}"])
        self.assertFalse(authority.authorize(source))
        authority.operation(["add", owner["name"]])
        self.assertTrue(self.allows(authority, owner))
        self.assertFalse(authority.authorize(source))
        self.assertTrue(authority.authorize(replace(source, grant_revision=authority.grant_revisions.get(OWNER_ID))))

    def test_revoke_regrant_survives_restart_and_never_revives_captured_work(self):
        authority, owner = self.authority(), player()
        self.join(authority, owner)
        authority.operation(["add", owner["name"]])
        before_revoke = replace(self.principal(authority, owner), kind="run", run_id="run1")
        authority.operation(["remove", f"xuid:{owner['xuid']}"])
        authority.operation(["add", owner["name"]])
        after_regrant = replace(self.principal(authority, owner), kind="run", run_id="run2")

        restarted = self.authority()
        self.join(restarted, owner)
        self.assertEqual(restarted.grant_revisions.get(OWNER_ID), 1)
        self.assertTrue(self.allows(restarted, owner))
        self.assertFalse(restarted.authorize(before_revoke))
        self.assertTrue(restarted.authorize(after_regrant))
        restarted.operation(["remove", f"xuid:{owner['xuid']}"])
        restarted.operation(["add", owner["name"]])
        self.assertFalse(restarted.authorize(after_regrant))
        self.assertEqual(self.authority().grant_revisions.get(OWNER_ID), 2)

    def test_grant_revisions_load_beside_the_policy_and_a_corrupt_file_fails_closed(self):
        state = self.root / "state"
        state.mkdir()

        def load():
            return Authority.from_config(_presence(), {"controller-policy-file": str(state / "controller-policy.json")},
                                         self.root / "plugin-data")

        self.assertEqual(load().grant_revisions.get(OWNER_ID), 0)
        (state / "grant-revisions.json").write_text(json.dumps({"version": 1, "revisions": {OWNER_ID: 4}}),
                                                    encoding="utf-8")
        self.assertEqual(load().grant_revisions.get(OWNER_ID), 4)
        (state / "grant-revisions.json").write_text("{corrupt", encoding="utf-8")
        with self.assertRaises(ValueError):
            load()

    def test_local_bot_cannot_be_granted_control(self):
        authority, bot = self.authority(), player(xuid="")
        self.join(authority, bot)
        lines, permissions = authority.manage(["add", bot["name"]])
        self.assertIn("change failed", lines[-1])
        self.assertEqual(permissions, [])
        self.assertFalse(self.allows(authority, bot))
        self.assertFalse(authority.policy.path.exists())

    def test_pending_grant_binds_on_join_and_revocation_overrides_bootstrap(self):
        authority = self.authority(controllers=ControllerEnrollment(("Owner",), self.root / "bindings.json"))
        owner = player()
        authority.manage(["remove", "Owner"])
        self.join(authority, owner)
        self.assertFalse(self.allows(authority, owner))
        self.assertEqual(authority.controllers.bindings, ())
        authority.manage(["add", "Owner"])
        self.join(authority, owner)
        self.assertTrue(self.allows(authority, owner))
        authority.manage(["remove", "Owner"])
        renamed = dict(owner, name="RenamedOwner")
        self.join(authority, renamed)
        self.assertFalse(self.allows(authority, renamed))
        self.assertFalse(self.allows(self.authority(), renamed))

    def test_failed_policy_write_does_not_change_permissions(self):
        authority, owner = self.authority(), player()
        self.join(authority, owner)
        authority.manage(["add", owner["name"]])
        with patch("endbot_core.controller_policy.os.replace", side_effect=OSError("disk failure")):
            lines, permissions = authority.manage(["remove", owner["name"]])
        self.assertTrue(self.allows(authority, owner))
        self.assertEqual(permissions, [])
        self.assertIn("disk failure", lines[-1])

    def test_revocation_overrides_legacy_uuid_and_xuid_permissions(self):
        owner = player()
        authority = self.authority(allowed=AllowedPlayers.from_config(
            {"allowed-uuids": [OWNER_ID], "allowed-xuids": [owner["xuid"]]}))
        self.assertTrue(self.allows(authority, owner))
        self.join(authority, owner)
        authority.manage(["remove", owner["name"]])
        self.assertFalse(self.allows(authority, owner))
        renamed = dict(owner, name="Renamed")
        self.join(authority, renamed)
        self.assertFalse(self.allows(authority, renamed))
        self.assertFalse(self.allows(authority, dict(renamed, xuid="")))

    def test_request_result_reaches_the_file(self):
        authority = self.authority(requests_directory=self.root)
        request_id, session = str(uuid4()), str(uuid4())
        path = self.root / f"{request_id}.dispatch.json"
        path.write_text(json.dumps({"version": 1, "id": request_id, "session": session, "operation": "add",
                                    "target": "Offline Player"}))
        authority.manage(["request", request_id])
        result = json.loads((self.root / f"{request_id}.response.json").read_text())
        self.assertTrue(result["ok"])
        self.assertEqual((result["id"], result["session"]), (request_id, session))
        self.assertEqual(result["entries"][0]["state"], "pending")
        self.assertFalse(path.exists())

    def test_failed_management_returns_failure_in_correlated_reply(self):
        authority = self.authority(requests_directory=self.root)
        request_id = str(uuid4())
        (self.root / f"{request_id}.dispatch.json").write_text(json.dumps(
            {"version": 1, "id": request_id, "session": str(uuid4()), "operation": "remove", "target": "Missing"}))
        authority.manage(["request", request_id])
        result = json.loads((self.root / f"{request_id}.response.json").read_text())
        self.assertFalse(result["ok"])
        self.assertIn("Unknown offline controller", result["messages"][0])

    def test_report_matches_effective_control_and_explains_revocation(self):
        authority, owner = self.authority(), player()
        self.join(authority, owner)
        authority.operation(["add", owner["name"]])
        row = authority.report()[0]
        self.assertEqual((row["state"], row["reason"], row["online"]), ("allowed", "runtime grant", True))
        authority.operation(["remove", owner["name"]])
        row = authority.report()[0]
        self.assertEqual((row["state"], row["reason"]), ("revoked", "revoked XUID"))
        self.assertFalse(self.allows(authority, owner))
        authority.operation(["add", "Pending"])
        self.assertIn("pending", [entry["state"] for entry in authority.report()])

    def test_offline_doctor_report_and_endbotd_report_agree(self):
        cli_source = str(Path(__file__).resolve().parents[3] / "cli/endbot/src")
        sys.path.insert(0, cli_source)
        self.addCleanup(sys.path.remove, cli_source)
        from endbot_cli.controller_status import load_policy, saved_report
        from endbot_cli.controllers import load_controllers

        binding_path = self.root / "bindings.json"
        authority = self.authority(controllers=ControllerEnrollment(("Owner", "Pending"), binding_path))
        owner = player()
        self.join(authority, owner)
        self.quit(authority, owner)
        for operation in ("remove", "add", "remove"):
            authority.operation([operation, "Owner"])
            rows = [{key: entry[key] for key in ("name", "state", "reason")} for entry in authority.report()]
            doctor_rows = saved_report(authority.controllers.gamertags, load_controllers(binding_path),
                                       load_policy(authority.policy.path), {})
            self.assertEqual(doctor_rows, rows)


def _presence():
    presence = Presence()
    presence.reset("epoch-1")
    return presence


if __name__ == "__main__":
    unittest.main()
