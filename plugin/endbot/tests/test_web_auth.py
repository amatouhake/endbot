"""Passkey ceremonies and sessions, against a software authenticator (docs/WEB.md P4, P5, P6)."""

import hashlib
import importlib.util
import tempfile
import unittest
from pathlib import Path

from endbot_core.link import Presence
from endbotd.authority import Authority
from endbotd.web_store import LinkCodes, LinkIdentity, WebStore

HAS_WEBAUTHN = importlib.util.find_spec("webauthn") is not None and importlib.util.find_spec("cbor2") is not None
if HAS_WEBAUTHN:
    from cryptography.hazmat.primitives.asymmetric import ec
    from endbotd.web_auth import MAX_CEREMONIES, MAX_PASSKEYS_PER_ACCOUNT, WebAuth
    from soft_authenticator import AT, UP, UV, SoftAuthenticator

ORIGIN = "https://endbot.example"
RP_ID = "endbot.example"
XUID = "2535412345678901"
PLAYER = "00000000-0000-4000-8000-0000000000a1"
ALICE = LinkIdentity(XUID, PLAYER, "Alice")
DAY = 86400.0


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


@unittest.skipUnless(HAS_WEBAUTHN, "the WebAuthn library is not installed")
class WebAuthCase(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.clock = Clock()
        self.logs, self.removed, self.added = [], [], []
        self.authority = Authority.from_config(Presence(), {"controller-policy-file": "policy.json"}, self.root)
        self.authority.operation(["add"], "Alice")
        self.authority.policy.add("Alice", xuid=XUID, player_uuid=PLAYER)
        self.store = WebStore(self.root / "web", clock=self.clock)
        self.codes = LinkCodes(clock=self.clock)
        self.auth = WebAuth(self.store, self.codes, ORIGIN, self.authority, clock=self.clock,
                            log=lambda level, message: self.logs.append((level, message)),
                            on_sessions_removed=self.removed.extend, on_passkey_added=self.added.append)
        self.device = SoftAuthenticator(ORIGIN, RP_ID)

    def post(self, path, body=None, token=None):
        return self.auth.handle(path, {} if body is None else body, token)

    def link_options(self, device_identity=ALICE):
        reply = self.post("/auth/link/options", {"code": self.codes.issue(device_identity)})
        self.assertEqual(reply["status"], 200, reply)
        return reply["body"]

    def link(self, device=None):
        device = device or self.device
        started = self.link_options()
        reply = self.post("/auth/link/verify", {"ceremony": started["ceremony"], "label": "Phone",
                                                "credential": device.register(started["options"])})
        self.assertEqual(reply["status"], 200, reply)
        return reply["session"]

    def login(self, device=None, token=None, **assertion):
        device = device or self.device
        started = self.post("/auth/login/options")["body"]
        return self.post("/auth/login/verify", {"ceremony": started["ceremony"],
                                                "credential": device.assert_(started["options"], **assertion)}, token)

    def account(self):
        return self.store.account_for_xuid(XUID)


class RegistrationTests(WebAuthCase):
    def test_a_code_registers_a_passkey_and_signs_in(self):
        token = self.link()
        account = self.account()
        passkey = self.store.passkeys(account["id"])[0]
        self.assertEqual((passkey["rpId"], passkey["label"], passkey["transports"]), (RP_ID, "Phone", ["internal"]))
        self.assertEqual(self.store.sessions(account["id"])[0]["via"], "passkey")
        self.assertEqual(self.added[0]["xuid"], XUID)
        self.assertIsNotNone(self.auth.session(token))

    def test_the_options_ask_for_a_discoverable_verified_credential_and_hide_the_xuid(self):
        options = self.link_options()["options"]
        self.assertEqual(options["rp"]["id"], RP_ID)
        self.assertEqual(options["authenticatorSelection"]["residentKey"], "required")
        self.assertEqual(options["authenticatorSelection"]["userVerification"], "required")
        self.assertEqual(options["attestation"], "none")
        self.assertEqual([entry["alg"] for entry in options["pubKeyCredParams"]], [-7, -8, -257])
        self.assertEqual(options["user"]["name"], "Alice")
        self.assertNotIn(XUID, str(options))

    def test_an_rs256_passkey_registers_and_signs_in_as_windows_hello_may_need(self):
        device = SoftAuthenticator(ORIGIN, RP_ID, rsa=True)
        self.assertIsNotNone(self.auth.session(self.link(device)))
        reply = self.login(device)
        self.assertEqual(reply["status"], 200, reply)

    def test_only_the_session_token_hash_is_stored(self):
        token = self.link()
        stored = self.store.sessions(self.account()["id"])[0]["hash"]
        self.assertEqual(stored, hashlib.sha256(token.encode()).hexdigest())
        for path in (self.root / "web").iterdir():
            self.assertNotIn(token, path.read_text(encoding="utf-8"))

    def test_a_wrong_code_is_refused(self):
        self.assertEqual(self.post("/auth/link/options", {"code": "AAAAAA"})["body"], {"code": "invalid_code"})

    def test_a_code_works_once(self):
        code = self.codes.issue(ALICE)
        self.assertEqual(self.post("/auth/link/options", {"code": code})["status"], 200)
        self.assertEqual(self.post("/auth/link/options", {"code": code})["status"], 400)

    def test_entry_closes_after_ten_wrong_codes(self):
        for _ in range(10):
            self.post("/auth/link/options", {"code": "AAAAAA"})
        reply = self.post("/auth/link/options", {"code": self.codes.issue(ALICE)})
        self.assertEqual((reply["status"], reply["body"]["code"]), (429, "link_closed"))

    def test_a_grant_revoked_before_the_code_is_used_refuses_it(self):
        code = self.codes.issue(ALICE)
        self.authority.operation(["remove"], "Alice")
        self.assertEqual(self.post("/auth/link/options", {"code": code})["status"], 403)
        self.assertIsNone(self.account())

    def test_a_grant_revoked_during_the_ceremony_refuses_it(self):
        started = self.link_options()
        self.authority.operation(["remove"], "Alice")
        reply = self.post("/auth/link/verify", {"ceremony": started["ceremony"],
                                                "credential": self.device.register(started["options"])})
        self.assertEqual(reply["status"], 403)
        self.assertEqual(self.store.passkeys(self.account()["id"]), [])

    def test_each_signed_value_is_checked(self):
        for name, changes in (
            ("another origin", {"origin": "https://evil.example"}),
            ("another RP ID", {"rp_id": "evil.example"}),
            ("another challenge", {"challenge": "AAAA"}),
            ("a get response", {"kind": "webauthn.get"}),
            ("no user verification", {"flags": UP | AT}),
            ("no user presence", {"flags": UV | AT}),
        ):
            with self.subTest(name):
                started = self.link_options()
                credential = self.device.register(started["options"], **changes)
                reply = self.post("/auth/link/verify", {"ceremony": started["ceremony"], "credential": credential})
                self.assertEqual((reply["status"], reply["body"]["code"]), (400, "passkey_refused"), reply)
                self.assertNotIn("session", reply)
        self.assertEqual(self.store.passkeys(self.account()["id"]), [])

    def test_a_ceremony_is_single_use_even_when_it_fails(self):
        started = self.link_options()
        bad = self.device.register(started["options"], origin="https://evil.example")
        reply = self.post("/auth/link/verify", {"ceremony": started["ceremony"], "credential": bad})
        self.assertEqual(reply["status"], 400)
        good = self.device.register(started["options"])
        reply = self.post("/auth/link/verify", {"ceremony": started["ceremony"], "credential": good})
        self.assertEqual(reply["body"]["code"], "expired")

    def test_a_ceremony_expires_after_two_minutes(self):
        started = self.link_options()
        self.clock.now += 120
        reply = self.post("/auth/link/verify", {"ceremony": started["ceremony"],
                                                "credential": self.device.register(started["options"])})
        self.assertEqual(reply["body"]["code"], "expired")

    def test_a_login_ceremony_cannot_finish_a_registration(self):
        started = self.post("/auth/login/options")["body"]
        reply = self.post("/auth/link/verify", {"ceremony": started["ceremony"],
                                                "credential": self.device.register(started["options"])})
        self.assertEqual(reply["body"]["code"], "expired")

    def test_outstanding_ceremonies_are_bounded(self):
        for _ in range(MAX_CEREMONIES):
            self.assertEqual(self.post("/auth/login/options")["status"], 200)
        self.assertEqual(self.post("/auth/login/options")["status"], 429)
        self.clock.now += 121
        self.assertEqual(self.post("/auth/login/options")["status"], 200)

    def test_a_second_account_cannot_take_an_existing_credential(self):
        self.link()
        bob = LinkIdentity("2535400000000002", "00000000-0000-4000-8000-0000000000b2", "Bob")
        self.authority.policy.add("Bob", xuid=bob.xuid, player_uuid=bob.player_id)
        started = self.link_options(bob)
        reply = self.post("/auth/link/verify", {"ceremony": started["ceremony"],
                                                "credential": self.device.register(started["options"])})
        self.assertEqual(reply["body"]["code"], "passkey_refused")
        self.assertEqual(self.store.passkeys(self.store.account_for_xuid(bob.xuid)["id"]), [])

    def test_registered_credentials_are_excluded_when_adding_another(self):
        self.link()
        options = self.link_options()["options"]
        self.assertEqual(len(options["excludeCredentials"]), 1)

    def test_a_bad_request_is_refused_without_a_crash(self):
        for path, body in (("/auth/link/options", {"code": 5}), ("/auth/link/options", {"code": "A" * 33}),
                           ("/auth/link/verify", {}), ("/auth/login/verify", {"ceremony": 5}),
                           ("/nope", {}), ("/auth/login/options", [])):
            with self.subTest(path=path, body=body):
                self.assertGreaterEqual(self.auth.handle(path, body)["status"], 400)
        started = self.link_options()
        for credential in (None, {}, {"id": "x"}, {"id": "x", "response": {}}, {"id": "x", "response": 5}):
            with self.subTest(credential=credential):
                reply = self.post("/auth/link/verify", {"ceremony": started["ceremony"], "credential": credential})
                self.assertEqual(reply["status"], 400)
                started = self.link_options()


class SignInTests(WebAuthCase):
    def test_a_passkey_signs_in_without_a_username(self):
        self.link()
        options = self.post("/auth/login/options")["body"]["options"]
        self.assertEqual(options.get("allowCredentials", []), [])
        self.assertEqual(options["userVerification"], "required")
        reply = self.login()
        self.assertEqual((reply["status"], reply["body"]), (200, {"gamertag": "Alice"}))
        self.assertIsNotNone(self.auth.principal(reply["session"]))
        self.assertEqual(self.store.passkeys(self.account()["id"])[0]["signCount"], 1)

    def test_each_signed_value_is_checked(self):
        self.link()
        for name, changes in (
            ("another origin", {"origin": "https://evil.example"}),
            ("another RP ID", {"rp_id": "evil.example"}),
            ("another challenge", {"challenge": "AAAA"}),
            ("a create response", {"kind": "webauthn.create"}),
            ("no user verification", {"flags": UP}),
            ("another key", {"key": ec.generate_private_key(ec.SECP256R1())}),
        ):
            with self.subTest(name):
                reply = self.login(**changes)
                self.assertEqual((reply["status"], reply["body"]["code"]), (400, "passkey_refused"), reply)
                self.assertNotIn("session", reply)

    def test_a_replayed_assertion_is_refused(self):
        self.link()
        started = self.post("/auth/login/options")["body"]
        credential = self.device.assert_(started["options"])
        body = {"ceremony": started["ceremony"], "credential": credential}
        self.assertEqual(self.post("/auth/login/verify", body)["status"], 200)
        self.assertEqual(self.post("/auth/login/verify", body)["body"]["code"], "expired")

    def test_a_sign_counter_that_goes_back_is_refused_and_logged(self):
        self.link()
        self.assertEqual(self.login(count=5)["status"], 200)
        reply = self.login(count=4)
        self.assertEqual(reply["body"]["code"], "passkey_refused")
        self.assertTrue(any("refused" in message for _, message in self.logs))
        self.assertEqual(self.store.passkeys(self.account()["id"])[0]["signCount"], 5)

    def test_an_unknown_credential_looks_like_any_other_refusal(self):
        self.link()
        reply = self.login(SoftAuthenticator(ORIGIN, RP_ID))
        self.assertEqual((reply["status"], reply["body"]["code"]), (400, "passkey_refused"))

    def test_a_passkey_from_another_domain_is_never_accepted(self):
        self.link()
        self.store.update_passkey(self.store.passkeys(self.account()["id"])[0]["id"], rpId="old.example")
        self.assertEqual(self.login()["body"]["code"], "passkey_refused")

    def test_signing_in_after_a_revoke_is_refused(self):
        self.link()
        self.authority.operation(["remove"], "Alice")
        self.assertEqual(self.login()["status"], 403)

    def test_signing_in_again_replaces_the_session_and_refreshes_the_assertion(self):
        old = self.link()
        self.clock.now += 400
        self.assertEqual(self.post("/auth/passkey/remove", {"id": "none"}, old)["body"]["code"], "reauthenticate")
        reply = self.login(token=old)
        self.assertIsNone(self.auth.session(old))
        self.assertEqual(self.post("/auth/passkey/remove", {"id": "none"}, reply["session"])["status"], 404)
        self.assertEqual(len(self.removed), 1)

    def test_another_accounts_session_is_left_alone_when_signing_in(self):
        other = self.store.ensure_account("2535400000000002", "00000000-0000-4000-8000-0000000000b2", "Bob")
        other_token = self.auth.issue_session(other, "passkey")
        self.link()
        self.login(token=other_token)
        self.assertIsNotNone(self.auth.session(other_token))


class SessionTests(WebAuthCase):
    def test_the_web_principal_carries_the_identity_and_role_and_is_not_the_console(self):
        token = self.link()
        self.authority.roles.set(XUID, "controller")
        principal = self.auth.principal(token, "ja")
        self.assertEqual((principal.kind, principal.xuid, principal.player_id, principal.name, principal.language),
                         ("web", XUID, PLAYER, "Alice", "ja"))
        self.assertFalse(principal.console)
        self.assertEqual(principal.role, "controller")
        self.authority.roles.set(XUID, "scripter")
        self.assertEqual(self.auth.principal(token).role, "scripter")

    def test_an_unknown_token_has_no_principal(self):
        self.link()
        for token in (None, "", "nope", 5):
            self.assertIsNone(self.auth.principal(token))

    def test_a_revoke_ends_the_principal_even_when_the_session_record_survives(self):
        """Every request rechecks the grant revision; the session list is not what enforces a revoke (P6)."""
        token = self.link()
        self.authority.operation(["remove"], "Alice")
        self.assertIsNotNone(self.store.find_session(self.store.sessions(self.account()["id"])[0]["hash"]))
        self.assertIsNone(self.auth.principal(token))
        self.authority.operation(["add"], "Alice")
        self.authority.policy.add("Alice", xuid=XUID, player_uuid=PLAYER)
        # A regrant never revives a session captured at the old revision.
        self.assertIsNone(self.auth.principal(token))

    def test_seven_idle_days_end_a_session(self):
        token = self.link()
        self.clock.now += 7 * DAY - 1
        self.assertIsNotNone(self.auth.session(token))
        self.clock.now += 7 * DAY - 1
        self.assertIsNotNone(self.auth.session(token))
        self.clock.now += 7 * DAY
        self.assertIsNone(self.auth.session(token))
        self.assertEqual(self.store.sessions(self.account()["id"]), [])
        self.assertEqual(len(self.removed), 1)

    def test_thirty_days_end_a_session_however_active(self):
        token = self.link()
        for _ in range(4):
            self.clock.now += 7 * DAY - 1
            self.assertIsNotNone(self.auth.session(token))
        self.clock.now += 2 * DAY + 4
        self.assertIsNone(self.auth.session(token))

    def test_last_seen_is_written_at_most_once_an_hour(self):
        token = self.link()
        created = self.store.sessions(self.account()["id"])[0]["lastSeen"]
        self.clock.now += 3599
        self.auth.session(token)
        self.assertEqual(self.store.sessions(self.account()["id"])[0]["lastSeen"], created)
        self.clock.now += 1
        self.auth.session(token)
        self.assertEqual(self.store.sessions(self.account()["id"])[0]["lastSeen"], self.clock.now)

    def test_logout_ends_the_session_and_clears_the_cookie(self):
        token = self.link()
        reply = self.post("/auth/logout", token=token)
        self.assertTrue(reply["clearSession"])
        self.assertIsNone(self.auth.session(token))
        self.assertTrue(self.post("/auth/logout", token="nope")["clearSession"])

    def test_a_signed_out_request_clears_the_cookie(self):
        reply = self.post("/auth/devices", token="nope")
        self.assertEqual((reply["status"], reply["clearSession"]), (401, True))

    def test_a_failed_write_is_an_unavailable_reply(self):
        from unittest.mock import patch

        with patch("endbotd.web_store.os.replace", side_effect=OSError("disk full")):
            self.assertEqual(self.post("/auth/link/options", {"code": self.codes.issue(ALICE)})["status"], 503)


class FailedWriteTests(WebAuthCase):
    def failing(self):
        from unittest.mock import patch

        return patch("endbotd.web_store.os.replace", side_effect=OSError("disk full"))

    def test_a_failed_logout_clears_the_cookie_and_the_store_stays_consistent(self):
        token = self.link()
        with self.failing():
            reply = self.post("/auth/logout", token=token)
        self.assertEqual((reply["status"], reply["clearSession"]), (503, True))
        # Memory and disk agree, so a restart holds no surprise; a retry can finish it.
        self.assertIsNotNone(self.auth.session(token))
        self.assertEqual(WebStore(self.root / "web").sessions(self.account()["id"]),
                         self.store.sessions(self.account()["id"]))
        self.assertTrue(self.post("/auth/logout", token=token)["clearSession"])
        self.assertEqual(WebStore(self.root / "web").sessions(self.account()["id"]), [])

    def test_a_removed_passkey_and_session_do_not_come_back_after_a_restart(self):
        token = self.link()
        passkey = self.store.passkeys(self.account()["id"])[0]["id"]
        self.assertEqual(self.post("/auth/passkey/remove", {"id": passkey}, token)["status"], 200)
        self.post("/auth/logout", token=token)
        reloaded = WebStore(self.root / "web")
        self.assertEqual(reloaded.passkeys(self.account()["id"]), [])
        self.assertEqual(reloaded.sessions(self.account()["id"]), [])

    def test_a_failed_removal_says_so_and_nothing_changes(self):
        token = self.link()
        passkey = self.store.passkeys(self.account()["id"])[0]["id"]
        with self.failing():
            self.assertEqual(self.post("/auth/passkey/remove", {"id": passkey}, token)["status"], 503)
        self.assertEqual(len(WebStore(self.root / "web").passkeys(self.account()["id"])), 1)
        self.assertEqual(len(self.store.passkeys(self.account()["id"])), 1)


class RevokeEpochTests(WebAuthCase):
    def test_a_surviving_session_stays_refused_after_the_revision_file_is_reset(self):
        """grant-revisions.json is documented as safe to reset; a persisted session must not rely on it."""
        token = self.link()
        stored = self.store.sessions(self.account()["id"])[0]
        self.clock.now += 1
        self.store.end_sessions_for_revoke(XUID)
        # The record survives, as a restored backup would bring it back, and the revisions are back at 0.
        self.store.add_session(stored)
        self.assertEqual(self.authority.grant_revisions.get(PLAYER), 0)
        self.assertIsNone(self.auth.session(token))
        self.assertIsNone(self.auth.principal(token))

    def test_signing_in_after_a_regrant_works(self):
        self.link()
        self.clock.now += 1
        self.store.end_sessions_for_revoke(XUID)
        self.clock.now += 1
        reply = self.login()
        self.assertEqual(reply["status"], 200)
        self.assertIsNotNone(self.auth.principal(reply["session"]))


class DeviceTests(WebAuthCase):
    def test_a_signed_in_browser_cannot_mint_a_code(self):
        """A new passkey starts in the game: a session, however fresh, cannot widen itself."""
        token = self.link()
        self.assertEqual(self.post("/auth/device-code", token=token)["status"], 404)

    def test_a_second_code_from_the_game_adds_a_passkey_and_tells_the_owner(self):
        self.link()
        self.link(SoftAuthenticator(ORIGIN, RP_ID))
        self.assertEqual(len(self.store.passkeys(self.account()["id"])), 2)
        self.assertEqual(len(self.added), 2)

    def test_an_account_holds_at_most_ten_passkeys(self):
        for _ in range(MAX_PASSKEYS_PER_ACCOUNT):
            self.link(SoftAuthenticator(ORIGIN, RP_ID))
        reply = self.post("/auth/link/options", {"code": self.codes.issue(ALICE)})
        self.assertEqual((reply["status"], reply["body"]["code"]), (409, "passkey_limit"))
        self.assertEqual(len(self.store.passkeys(self.account()["id"])), MAX_PASSKEYS_PER_ACCOUNT)

    def test_a_ceremony_opened_before_the_limit_is_refused_at_the_limit(self):
        for _ in range(MAX_PASSKEYS_PER_ACCOUNT - 1):
            self.link(SoftAuthenticator(ORIGIN, RP_ID))
        waiting = self.link_options()
        self.link(SoftAuthenticator(ORIGIN, RP_ID))
        device = SoftAuthenticator(ORIGIN, RP_ID)
        reply = self.post("/auth/link/verify", {"ceremony": waiting["ceremony"],
                                                "credential": device.register(waiting["options"])})
        self.assertEqual(reply["body"]["code"], "passkey_limit")
        self.assertEqual(len(self.store.passkeys(self.account()["id"])), MAX_PASSKEYS_PER_ACCOUNT)

    def test_removing_a_passkey_makes_room_again(self):
        for _ in range(MAX_PASSKEYS_PER_ACCOUNT - 1):
            self.link(SoftAuthenticator(ORIGIN, RP_ID))
        token = self.link(SoftAuthenticator(ORIGIN, RP_ID))
        passkey = self.store.passkeys(self.account()["id"])[0]["id"]
        self.assertEqual(self.post("/auth/passkey/remove", {"id": passkey}, token)["status"], 200)
        self.assertEqual(self.post("/auth/link/options", {"code": self.codes.issue(ALICE)})["status"], 200)

    def test_the_list_shows_this_accounts_devices_without_secrets(self):
        token = self.link()
        body = self.post("/auth/devices", token=token)["body"]
        self.assertEqual([entry["current"] for entry in body["sessions"]], [True])
        self.assertEqual([entry["label"] for entry in body["passkeys"]], ["Phone"])
        self.assertNotIn("hash", body["sessions"][0])
        self.assertNotIn("publicKey", body["passkeys"][0])

    def test_removing_a_session_closes_it(self):
        first = self.link()
        second = self.login()["session"]
        target = next(entry for entry in self.post("/auth/devices", token=first)["body"]["sessions"]
                      if not entry["current"])
        self.assertEqual(self.post("/auth/session/remove", {"id": target["id"]}, first)["status"], 200)
        self.assertIsNone(self.auth.session(second))
        self.assertIsNotNone(self.auth.session(first))
        self.assertEqual(len(self.removed), 1)

    def test_removing_the_current_session_clears_the_cookie(self):
        token = self.link()
        current = self.post("/auth/devices", token=token)["body"]["sessions"][0]
        self.assertTrue(self.post("/auth/session/remove", {"id": current["id"]}, token)["clearSession"])

    def test_another_accounts_items_cannot_be_removed(self):
        bob = self.store.ensure_account("2535400000000002", "00000000-0000-4000-8000-0000000000b2", "Bob")
        self.auth.issue_session(bob, "passkey")
        target = self.store.sessions(bob["id"])[0]
        self.store.add_passkey({"id": "bobkey", "account": bob["id"], "publicKey": "x", "signCount": 0, "rpId": RP_ID,
                                "label": "", "created": 0, "lastUsed": None, "transports": []})
        token = self.link()
        self.assertEqual(self.post("/auth/session/remove", {"id": target["id"]}, token)["status"], 404)
        self.assertEqual(self.post("/auth/passkey/remove", {"id": "bobkey"}, token)["status"], 404)
        self.assertEqual(len(self.store.sessions(bob["id"])), 1)
        self.assertEqual(len(self.store.passkeys(bob["id"])), 1)

    def test_removing_a_passkey_needs_a_recent_assertion(self):
        token = self.link()
        passkey = self.store.passkeys(self.account()["id"])[0]["id"]
        self.clock.now += 300
        self.assertEqual(self.post("/auth/passkey/remove", {"id": passkey}, token)["body"]["code"], "reauthenticate")
        token = self.login(token=token)["session"]
        self.assertEqual(self.post("/auth/passkey/remove", {"id": passkey}, token)["status"], 200)
        self.assertEqual(self.login()["body"]["code"], "passkey_refused")

    def test_a_valid_credential_with_junk_in_its_unsigned_fields_still_works(self):
        for junk in ([[]], [{}], [5], "usb", None):
            with self.subTest(junk=junk):
                device = SoftAuthenticator(ORIGIN, RP_ID)
                started = self.link_options()
                credential = device.register(started["options"])
                credential["response"]["transports"] = junk
                reply = self.post("/auth/link/verify", {"ceremony": started["ceremony"], "credential": credential})
                self.assertEqual(reply["status"], 200, reply)
                stored = self.store.find_passkey(credential["id"])
                self.assertEqual(stored["transports"], [])

    def test_a_label_is_cut_to_its_bound(self):
        started = self.link_options()
        reply = self.post("/auth/link/verify", {"ceremony": started["ceremony"], "label": "x" * 100,
                                                "credential": self.device.register(started["options"])})
        self.assertEqual(reply["status"], 200)
        self.assertEqual(self.store.passkeys(self.account()["id"])[0]["label"], "x" * 64)

    def test_a_label_is_bounded_and_printable(self):
        started = self.link_options()
        reply = self.post("/auth/link/verify", {"ceremony": started["ceremony"], "label": "x\ny",
                                                "credential": self.device.register(started["options"])})
        self.assertEqual(reply["status"], 200)
        self.assertEqual(self.store.passkeys(self.account()["id"])[0]["label"], "")


if __name__ == "__main__":
    unittest.main()
