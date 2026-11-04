"""Web accounts, link codes and how revocation reaches them (docs/WEB.md P3, P4, P6, P7)."""

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from endbot_core.principal import Principal
from endbotd.web import web_origin
from endbotd.web_store import LinkCodeEntryClosed, LinkCodes, LinkIdentity, WebStore, WebStoreError
from test_endbotd_service import OWNER, ServiceHarness
from test_link import ALICE, FakePlayer, wait_for

ORIGIN = "https://endbot.example"
IDENTITY = LinkIdentity(OWNER, ALICE, "Alice")
HAS_WEBAUTHN = importlib.util.find_spec("webauthn") is not None


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class WebStoreTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name) / "web"

    def seed(self, store, account):
        """Passkeys and sessions arrive with the ceremonies; deleting them is what is tested here."""
        store._passkeys.append({"account": account["id"], "id": "credential", "publicKey": "key", "rpId": "a.example",
                                "signCount": 0})
        store._sessions.append({"account": account["id"], "hash": "abc", "id": "s1", "via": "passkey", "created": 1.0,
                                "lastSeen": 1.0, "assertedAt": 1.0, "grantRevision": 0})
        (self.directory / "passkeys.json").write_text(
            json.dumps({"version": 1, "passkeys": store._passkeys}), encoding="utf-8")
        (self.directory / "sessions.json").write_text(
            json.dumps({"version": 1, "sessions": store._sessions}), encoding="utf-8")

    def test_one_account_per_xuid_with_a_random_user_handle_that_survives_a_restart(self):
        store = WebStore(self.directory)
        account = store.ensure_account(OWNER, ALICE, "Alice")
        self.assertEqual(store.ensure_account(OWNER, ALICE, "Alice")["id"], account["id"])
        self.assertNotIn(OWNER, account["userHandle"])
        self.assertEqual(len(WebStore(self.directory).accounts()), 1)
        self.assertEqual(WebStore(self.directory).account_for_xuid(OWNER), account)

    def test_a_renamed_gamertag_updates_the_account(self):
        store = WebStore(self.directory)
        store.ensure_account(OWNER, ALICE, "Alice")
        store.ensure_account(OWNER, ALICE, "Alicia")
        self.assertEqual(WebStore(self.directory).account_for_gamertag("alicia")["xuid"], OWNER)
        self.assertIsNone(store.account_for_gamertag("Alice"))

    def test_deleting_sessions_keeps_the_account_and_passkeys(self):
        store = WebStore(self.directory)
        account = store.ensure_account(OWNER, ALICE, "Alice")
        self.seed(store, account)
        self.assertEqual(store.delete_sessions(OWNER), ["abc"])
        reloaded = WebStore(self.directory)
        self.assertEqual(reloaded.sessions(account["id"]), [])
        self.assertEqual(len(reloaded.passkeys(account["id"])), 1)
        self.assertIsNotNone(reloaded.account_for_xuid(OWNER))

    def test_deleting_the_account_takes_its_passkeys_and_sessions(self):
        store = WebStore(self.directory)
        account = store.ensure_account(OWNER, ALICE, "Alice")
        self.seed(store, account)
        self.assertEqual(store.delete_account(OWNER), ["abc"])
        reloaded = WebStore(self.directory)
        self.assertEqual(reloaded.accounts(), [])
        self.assertEqual(reloaded.passkeys(account["id"]), [])
        self.assertEqual(reloaded.sessions(account["id"]), [])
        self.assertEqual(store.delete_account(OWNER), [])

    def test_a_broken_file_is_refused_not_overwritten(self):
        self.directory.mkdir()
        for name, text in (("accounts.json", "{"), ("accounts.json", '{"version": 2, "accounts": []}'),
                           ("accounts.json", '{"version": 1, "accounts": [{"id": "x"}]}'),
                           ("passkeys.json", '{"version": 1, "passkeys": {}}')):
            with self.subTest(name=name, text=text):
                (self.directory / name).write_text(text, encoding="utf-8")
                with self.assertRaises(WebStoreError):
                    WebStore(self.directory)
                self.assertEqual((self.directory / name).read_text(encoding="utf-8"), text)
                (self.directory / name).unlink()

    def test_two_accounts_for_one_xuid_are_refused(self):
        self.directory.mkdir()
        account = {"id": "a", "xuid": OWNER, "uuid": ALICE, "gamertag": "Alice", "userHandle": "h"}
        (self.directory / "accounts.json").write_text(
            json.dumps({"version": 1, "accounts": [account, {**account, "id": "b"}]}), encoding="utf-8")
        with self.assertRaises(WebStoreError):
            WebStore(self.directory)

    @unittest.skipIf(os.name == "nt", "POSIX modes")
    def test_files_are_owner_only(self):
        WebStore(self.directory).ensure_account(OWNER, ALICE, "Alice")
        self.assertEqual((self.directory / "accounts.json").stat().st_mode & 0o077, 0)
        self.assertEqual(self.directory.stat().st_mode & 0o077, 0)

    def test_a_failed_write_leaves_no_temporary_file(self):
        store = WebStore(self.directory)
        store.ensure_account(OWNER, ALICE, "Alice")
        with patch("endbotd.web_store.os.replace", side_effect=OSError("disk full")), self.assertRaises(OSError):
            store.ensure_account("2535400000000002", ALICE, "Bob")
        self.assertEqual([path.name for path in self.directory.iterdir()], ["accounts.json"])


class LinkCodeTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.closed = []
        self.codes = LinkCodes(clock=self.clock, on_closed=lambda: self.closed.append(True))

    def test_a_code_is_six_symbols_and_works_once(self):
        code = self.codes.issue(IDENTITY)
        self.assertRegex(code, r"^[0-9A-HJKMNP-TV-Z]{3}-[0-9A-HJKMNP-TV-Z]{3}$")
        self.assertEqual(self.codes.redeem(code), IDENTITY)
        self.assertIsNone(self.codes.redeem(code))

    def test_typing_is_forgiving_but_the_symbols_still_have_to_match(self):
        code = self.codes.issue(IDENTITY)
        self.assertEqual(self.codes.redeem(f"  {code.lower().replace('-', ' ')} "), IDENTITY)
        with patch("endbotd.web_store.secrets.choice", side_effect=list("0K1ABC")):
            self.assertEqual(self.codes.issue(IDENTITY), "0K1-ABC")
        self.assertEqual(self.codes.redeem("OK-IABC"), IDENTITY)

    def test_a_code_expires_after_five_minutes(self):
        code = self.codes.issue(IDENTITY)
        self.clock.now += 299
        again = self.codes.issue(IDENTITY)
        self.clock.now += 299
        self.assertEqual(self.codes.redeem(again), IDENTITY)
        self.assertIsNone(self.codes.redeem(code))
        expired = self.codes.issue(IDENTITY)
        self.clock.now += 300
        self.assertIsNone(self.codes.redeem(expired))

    def test_one_live_code_per_xuid(self):
        first = self.codes.issue(IDENTITY)
        second = self.codes.issue(IDENTITY)
        self.assertIsNone(self.codes.redeem(first))
        self.assertEqual(self.codes.redeem(second), IDENTITY)

    def test_ten_wrong_codes_close_entry_for_ten_minutes_even_for_a_right_code(self):
        code = self.codes.issue(IDENTITY)
        for _ in range(9):
            self.assertIsNone(self.codes.redeem("AAAAAA"))
        self.assertEqual(self.closed, [])
        self.assertIsNone(self.codes.redeem("AAAAAA"))
        self.assertEqual(self.closed, [True])
        with self.assertRaises(LinkCodeEntryClosed):
            self.codes.redeem(code)
        self.clock.now += 599
        with self.assertRaises(LinkCodeEntryClosed):
            self.codes.redeem(code)
        self.clock.now += 1
        fresh = self.codes.issue(IDENTITY)
        self.assertEqual(self.codes.redeem(fresh), IDENTITY)

    def test_old_wrong_codes_age_out_of_the_limit(self):
        for _ in range(9):
            self.codes.redeem("AAAAAA")
        self.clock.now += 601
        for _ in range(9):
            self.codes.redeem("AAAAAA")
        self.assertEqual(self.closed, [])

    def test_voiding_removes_a_live_code(self):
        code = self.codes.issue(IDENTITY)
        self.codes.void([OWNER])
        self.assertIsNone(self.codes.redeem(code))


class OriginTests(unittest.TestCase):
    def test_only_an_exact_secure_origin_is_accepted(self):
        for value in ("https://a.example", "https://a.example:8443", "http://localhost:8787"):
            self.assertEqual(web_origin(value), value)
        self.assertIsNone(web_origin(""))
        self.assertIsNone(web_origin(None))
        for value in ("http://a.example", "https://a.example/", "https://a.example/x", "https://u@a.example",
                      "ftp://a.example", "a.example", "https://", "https://a.example:99999", "https://a.example?x=1",
                      "https://A.example", "https://a.example:443", "http://localhost:80", 5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                web_origin(value)


@unittest.skipUnless(HAS_WEBAUTHN, "the WebAuthn library is not installed")
class BotWebCommandTests(ServiceHarness):
    def start_service(self, permissions=None, web=None):
        super().start_service(permissions, web={"enabled": True, "origin": ORIGIN} if web is None else web)

    def joined_alice(self, *, grant=True):
        link = self.link()
        alice = FakePlayer(ALICE, "Alice", xuid=OWNER)
        self.join(link, alice)
        if grant:
            self.console(link, "add", "Alice")
            self.assertTrue(wait_for(lambda: alice.attachments))
        return link, alice

    def botweb(self, link, *argv):
        return self.submit(link, Principal("player", player_id=ALICE), "botweb", *argv)

    def message(self, player, text):
        self.assertTrue(wait_for(lambda: any(text in line for line in player.messages)), player.messages)
        return next(line for line in player.messages if text in line)

    def test_a_granted_player_gets_a_code_for_their_own_xuid(self):
        link, alice = self.joined_alice()
        self.assertEqual(self.botweb(link, "link"), {"accepted": True, "code": "ok"})
        line = self.message(alice, "Web link code:")
        self.assertIn(ORIGIN, line)
        code = line.split("code: ")[1].split(" ")[0]
        self.assertEqual(self.service.web.codes.redeem(code), LinkIdentity(OWNER, ALICE, "Alice"))
        # No account until a passkey or site proof uses the code.
        self.assertEqual(self.service.web.store.accounts(), [])

    def test_a_player_without_a_grant_gets_no_code(self):
        link, alice = self.joined_alice(grant=False)
        self.assertEqual(self.botweb(link, "link"), {"accepted": False, "code": "unauthorized"})
        self.message(alice, "you do not have Bot control permission")
        self.assertFalse(any("code" in line for line in alice.messages))

    def test_an_unauthenticated_player_cannot_link(self):
        record = {"playerId": ALICE, "name": "Alice", "xuid": ""}
        self.assertIn("Use /botweb", self.service.web.command(record, "en", ["link"])[0])

    def test_the_console_cannot_link(self):
        link = self.link()
        self.assertEqual(self.submit(link, Principal("console"), "botweb", "link"),
                         {"accepted": False, "code": "unauthorized"})

    def test_status_and_unlink(self):
        link, alice = self.joined_alice()
        self.botweb(link, "status")
        self.message(alice, "no web account; /botweb link creates one")
        self.botweb(link, "unlink")
        self.message(alice, "This player has no web account")
        self.service.web.store.ensure_account(OWNER, ALICE, "Alice")
        self.botweb(link)
        self.message(alice, "Web account linked: 0 passkeys, 0 sessions")
        self.botweb(link, "unlink")
        self.message(alice, "Web account removed")
        self.assertEqual(self.service.web.store.accounts(), [])

    def test_unknown_arguments_get_the_usage(self):
        link, alice = self.joined_alice()
        self.botweb(link, "link", "someone-else")
        self.message(alice, "Use /botweb link")

    def test_revoking_the_grant_ends_sessions_and_codes_but_keeps_the_account(self):
        link, alice = self.joined_alice()
        web = self.service.web
        account = web.store.ensure_account(OWNER, ALICE, "Alice")
        web.store._sessions.append({"account": account["id"], "hash": "abc"})
        removed = []
        web.on_sessions_removed = removed.extend
        self.botweb(link, "link")
        code = self.message(alice, "Web link code:").split("code: ")[1].split(" ")[0]
        self.console(link, "remove", "Alice")
        self.assertTrue(wait_for(lambda: removed == ["abc"]))
        self.assertEqual(web.store.sessions(account["id"]), [])
        self.assertIsNotNone(web.store.account_for_xuid(OWNER))
        self.assertIsNone(web.codes.redeem(code))

    def test_revoking_the_grant_voids_a_code_when_there_is_no_account_yet(self):
        link, alice = self.joined_alice()
        self.botweb(link, "link")
        code = self.message(alice, "Web link code:").split("code: ")[1].split(" ")[0]
        self.assertEqual(self.service.web.store.accounts(), [])
        self.console(link, "remove", "Alice")
        self.assertTrue(wait_for(lambda: self.service.web.codes.live() == []))
        self.assertIsNone(self.service.web.codes.redeem(code))

    def test_unlink_and_revoke_void_a_code_when_there_is_no_account_yet(self):
        link, alice = self.joined_alice()
        self.botweb(link, "link")
        code = self.message(alice, "Web link code:").split("code: ")[1].split(" ")[0]
        self.botweb(link, "unlink")
        self.message(alice, "This player has no web account")
        self.assertIsNone(self.service.web.codes.redeem(code))
        self.service.web.codes.issue(LinkIdentity(OWNER, ALICE, "Alice"))
        self.assertFalse(self.service._cli_web({"operation": "revoke", "target": f"xuid:{OWNER}"})["ok"])
        self.assertEqual(self.service.web.codes.live(), [])

    def test_grant_changes_through_the_cli_reach_the_web_too(self):
        _link, _alice = self.joined_alice()
        web = self.service.web
        account = web.store.ensure_account(OWNER, ALICE, "Alice")
        web.store._sessions.append({"account": account["id"], "hash": "abc"})
        self.service._cli_controllers({"operation": "remove", "target": "Alice"})
        self.assertEqual(web.store.sessions(account["id"]), [])

    def test_a_kept_grant_keeps_sessions(self):
        link, _alice = self.joined_alice()
        web = self.service.web
        account = web.store.ensure_account(OWNER, ALICE, "Alice")
        web.store._sessions.append({"account": account["id"], "hash": "abc"})
        self.console(link, "add", "Bob")
        self.assertEqual(len(web.store.sessions(account["id"])), 1)

    def test_endbot_web_revoke_by_gamertag_or_xuid_keeps_the_grant(self):
        _link, _alice = self.joined_alice()
        web = self.service.web
        for target in ("alice", f"xuid:{OWNER}"):
            with self.subTest(target=target):
                web.store.ensure_account(OWNER, ALICE, "Alice")
                result = self.service._cli_web({"operation": "revoke", "target": target})
                self.assertTrue(result["ok"], result)
                self.assertEqual(web.store.accounts(), [])
        self.assertFalse(self.service._cli_web({"operation": "revoke", "target": "Alice"})["ok"])
        self.assertTrue(self.service.authority.control_decision("Alice", OWNER, ALICE)[0])

    def test_a_passkey_added_tells_the_player_in_chat(self):
        _link, alice = self.joined_alice()
        self.service._passkey_added({"xuid": OWNER})
        self.message(alice, "A passkey was added to your web account")
        self.service._passkey_added({"xuid": "2535400000000002"})

    def test_status_says_the_link_is_off_without_a_key(self):
        result = self.service._cli_web({"operation": "status"})
        self.assertEqual((result["ok"], result["linked"]), (True, False))
        self.assertIn("off", result["messages"][0])

    def test_a_domain_change_deletes_credentials_and_keeps_accounts(self):
        web = self.service.web
        account = web.store.ensure_account(OWNER, ALICE, "Alice")
        token = web.auth.issue_session(account, "passkey")
        removed = []
        web.on_sessions_removed = removed.extend
        self.assertTrue(self.service._cli_web({"operation": "domain-change"})["ok"])
        self.assertIsNone(web.auth.session(token))
        self.assertEqual(len(removed), 1)
        self.assertIsNotNone(web.store.account_for_xuid(OWNER))

    def test_reload_reads_the_generated_settings_again(self):
        (self.root / "endbotd.toml").write_text(
            '[web]\nenabled = true\norigin = "https://new.example"\n', encoding="utf-8")
        self.service._cli_web({"operation": "reload"})
        self.assertEqual(self.service.web.origin, "https://new.example")

    def test_reload_without_the_file_says_so_and_keeps_the_web(self):
        before = self.service.web
        result = self.service._cli_web({"operation": "reload"})
        self.assertFalse(result["ok"])
        self.assertIs(self.service.web, before)

    def test_cli_requests_are_validated(self):
        for body in ({"operation": "delete", "target": "Alice"}, {"operation": "revoke"},
                     {"operation": "revoke", "target": "a\nb"}, {"operation": "revoke", "target": " "}):
            with self.subTest(body=body), self.assertRaises(ValueError):
                self.service._cli_web(body)


class WebDisabledTests(BotWebCommandTests):
    def start_service(self, permissions=None, web=None):
        super().start_service(permissions, web=web if web is not None else {"enabled": False, "origin": ORIGIN})

    def test_a_granted_player_gets_a_code_for_their_own_xuid(self):
        link, alice = self.joined_alice()
        self.botweb(link, "link")
        self.message(alice, "The web is not available on this server")

    def test_a_domain_change_deletes_credentials_and_keeps_accounts(self):
        self.skipTest("a disabled web has no WebAuth to sign in with")

    def test_revoking_the_grant_voids_a_code_when_there_is_no_account_yet(self):
        self.skipTest("a disabled web issues no code")

    def test_unlink_and_revoke_void_a_code_when_there_is_no_account_yet(self):
        self.skipTest("a disabled web issues no code")

    def test_revoking_the_grant_ends_sessions_and_codes_but_keeps_the_account(self):
        """A disabled web still has a store to clean, but issues no code to void."""
        link, _alice = self.joined_alice()
        web = self.service.web
        account = web.store.ensure_account(OWNER, ALICE, "Alice")
        web.store._sessions.append({"account": account["id"], "hash": "abc"})
        self.console(link, "remove", "Alice")
        self.assertTrue(wait_for(lambda: web.store.sessions(account["id"]) == []))


class WebMisconfiguredTests(ServiceHarness):
    def test_an_invalid_origin_keeps_the_web_off_and_the_service_up(self):
        self.service.close()
        self.start_service(web={"enabled": True, "origin": "http://insecure.example"})
        self.assertFalse(self.service.web.available)
        self.assertTrue(any("Web disabled" in message for _, message in self.logs))

    def test_a_broken_store_keeps_the_web_off_and_the_service_up(self):
        self.service.close()
        (self.root / "web").mkdir()
        (self.root / "web" / "accounts.json").write_text("{", encoding="utf-8")
        self.start_service(web={"enabled": True, "origin": ORIGIN})
        self.assertFalse(self.service.web.available)
        self.assertEqual(self.service._cli_web({"operation": "revoke", "target": "Alice"})["ok"], False)
        self.service.web.grants_changed()
        self.assertEqual((self.root / "web" / "accounts.json").read_text(encoding="utf-8"), "{")


if __name__ == "__main__":
    unittest.main()
