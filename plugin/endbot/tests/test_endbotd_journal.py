"""endbotd's run journal and restart recovery (docs/ENDBOTD.md P6)."""

import json
import unittest

from endbot_core.principal import Principal
from endbot_core.scripting import ScriptRun
from endbotd.journal import MAX_RUNS, RunJournal
from test_endbotd_service import OWNER, ServiceHarness
from test_link import ALICE, FakePlayer, wait_for

BOB = "00000000-0000-4000-8000-0000000000b0"
STEVE = "00000000-0000-4000-8000-000000000001"
ALEX = "00000000-0000-4000-8000-000000000002"


def journal_run(run, name, owner, channels):
    return {"run": run, "name": name, "owner": owner, "channels": channels}


class RecoveryTests(ServiceHarness):
    def start_service(self):
        """Each test starts endbotd itself, after writing the journal a previous one left."""

    def boot(self):
        ServiceHarness.start_service(self)

    @property
    def journal(self):
        return self.root / "endbotd-runs.json"

    def restart_after(self, runs):
        for name, identity in (("Steve", STEVE), ("Alex", ALEX)):
            self.control.bots[name] = {"identityId": identity, "name": name}
        self.journal.write_text(json.dumps({"version": 1, "runs": runs}), encoding="utf-8")
        self.boot()

    def test_a_clean_stop_leaves_nothing_to_recover(self):
        self.control.bots["Steve"] = {"identityId": STEVE, "name": "Steve"}
        self.boot()
        source = Principal("run", name="Alice", player_id=ALICE, xuid=OWNER, run_id="r1")
        self.service.scripts._runs["r1"] = ScriptRun("r1", "miner", source)
        self.service._inputs.adopt("r1", [{"identityId": STEVE, "channel": "move", "name": "Steve", "loopId": None}])
        self.service._record_runs()
        recorded = json.loads(self.journal.read_text(encoding="utf-8"))
        self.assertEqual(recorded["runs"], [journal_run(
            "r1", "miner", {"playerId": ALICE, "xuid": OWNER, "name": "Alice", "language": "en"},
            [{"identityId": STEVE, "channel": "move", "name": "Steve", "loopId": None}])])
        self.service.close()
        self.assertFalse(self.journal.exists())
        self.assertIn(("move", {"name": "Steve", "expectedIdentityId": STEVE, "direction": "stop"}),
                      self.control.calls)

    def test_an_unclean_exit_ends_each_run_logs_it_and_tells_the_online_owner(self):
        alice = FakePlayer(ALICE, "Alice", xuid=OWNER, locale="ja_JP")
        self.mc.players[ALICE] = alice
        self.restart_after([
            journal_run("r1", "miner", {"playerId": ALICE, "xuid": OWNER, "name": "Alice", "language": "ja"}, [
                {"identityId": STEVE, "channel": "mine", "name": "Steve", "loopId": "loop-1"},
                {"identityId": STEVE, "channel": "sneak", "name": "Steve"},
            ]),
            journal_run("r2", "patrol", {"playerId": None, "name": "console"},
                        [{"identityId": ALEX, "channel": "attack", "name": "Alex"}]),
        ])
        self.assertEqual(self.control.calls, [
            ("mine", {"name": "Steve", "expectedIdentityId": STEVE, "mode": "stop", "loopId": "loop-1"}),
            ("flag", {"name": "Steve", "expectedIdentityId": STEVE, "flag": "sneak", "value": False}),
            ("action", {"name": "Alex", "expectedIdentityId": ALEX, "action": "attack", "mode": "stop"}),
        ])
        self.assertIn(("info", "Script miner (r1) ended: Endbot service restarted"), self.logs)
        self.assertIn(("info", "Script patrol (r2) ended: Endbot service restarted"), self.logs)
        self.assertFalse(self.journal.exists())
        self.link()
        self.assertTrue(wait_for(lambda: any("スクリプト miner (r1) が終了: Endbot サービスが再起動しました" in line
                                             for line in alice.messages)))
        self.assertEqual(self.service._notices, [])

    def test_an_offline_owner_is_not_told_later(self):
        self.restart_after([journal_run("r1", "miner", {"playerId": ALICE, "xuid": OWNER, "name": "Alice"},
                                        [{"identityId": STEVE, "channel": "move", "name": "Steve"}])])
        self.assertIn(("info", "Script miner (r1) ended: Endbot service restarted"), self.logs)
        link = self.link()
        self.assertTrue(wait_for(lambda: not self.service._notices))
        alice = FakePlayer(ALICE, "Alice", xuid=OWNER)
        self.join(link, alice)
        self.assertTrue(wait_for(lambda: self.service.link.presence.player(ALICE)))
        self.assertFalse(any("restarted" in line for line in alice.messages))

    def test_a_corrupt_journal_is_logged_kept_aside_and_does_not_stop_the_start(self):
        self.journal.write_text("{not json", encoding="utf-8")
        self.boot()
        self.assertTrue(any(level == "warning" and "is corrupt" in message for level, message in self.logs))
        self.assertTrue((self.root / "endbotd-runs.json.corrupt").exists())
        self.assertFalse(self.journal.exists())
        self.assertEqual(self.control.calls, [])
        self.link()

    def test_runtime_failures_are_logged_and_the_journal_still_cleared(self):
        self.control.request = self._unavailable
        self.restart_after([journal_run("r1", "miner", {"playerId": BOB},
                                        [{"identityId": STEVE, "channel": "move", "name": "Steve"}])])
        self.assertIn(("info", "Script miner (r1) ended: Endbot service restarted"), self.logs)
        self.assertTrue(any(level == "warning" and "input cleanup failed" in message for level, message in self.logs))
        self.assertFalse(self.journal.exists())

    @staticmethod
    def _unavailable(method, **params):
        from endbot_core.control import RuntimeControlError

        raise RuntimeControlError("unavailable", "runtime is not running")


class JournalFileTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "run" / "endbotd-runs.json"
        self.logs = []
        self.journal = RunJournal(self.path, lambda level, message: self.logs.append((level, message)))

    def test_writes_are_bounded_and_invalid_entries_skipped(self):
        runs = [journal_run(f"r{index}", "s", {}, [{"identityId": STEVE, "channel": "move"}])
                for index in range(MAX_RUNS + 5)]
        self.journal.write(runs)
        self.assertEqual(len(json.loads(self.path.read_text(encoding="utf-8"))["runs"]), MAX_RUNS)
        self.path.write_text(json.dumps({"version": 1, "runs": [
            journal_run("r1", "s", {}, [{"identityId": STEVE, "channel": "fly"}]), {"run": 5}]}), encoding="utf-8")
        loaded = self.journal.load()
        self.assertEqual([(run["run"], run["channels"]) for run in loaded], [("r1", [])])
        self.assertTrue(any("1 entries skipped" in message for _, message in self.logs))

    def test_a_missing_journal_is_empty_and_a_final_write_is_the_last(self):
        self.assertEqual(self.journal.load(), [])
        self.assertEqual(self.logs, [])
        self.journal.write([], final=True)
        self.journal.write([journal_run("r1", "s", {}, [])])
        self.assertFalse(self.path.exists())

    def test_a_journal_of_the_wrong_shape_is_corrupt(self):
        self.path.parent.mkdir()
        for content in ('{"version": 2, "runs": []}', '{"version": 1, "runs": {}}', "[]", "\xff"):
            with self.subTest(content):
                self.path.write_text(content, encoding="latin-1")
                self.assertEqual(self.journal.load(), [])
                self.assertIn("is corrupt", self.logs[-1][1])


if __name__ == "__main__":
    unittest.main()
