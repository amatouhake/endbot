"""The optional trusted site: every row of the claim and failure tables (docs/WEB.md P12)."""

import base64
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from endbot_core.link import Presence
from endbotd.authority import Authority
from endbotd.web import WebService
from endbotd.web_store import LinkCodes, LinkIdentity, WebStore

HAS_LIBRARIES = importlib.util.find_spec("webauthn") is not None and importlib.util.find_spec("cbor2") is not None
if HAS_LIBRARIES:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
    from endbotd.web_auth import WebAuth
    from endbotd.web_site import SiteConfig, SiteConfigError, TrustedSite
    from soft_authenticator import SoftAuthenticator

ORIGIN = "https://endbot.example"
ISSUER = "https://alice.example"
XUID = "2535412345678901"
PLAYER = "00000000-0000-4000-8000-0000000000a1"
NOW = 1_800_000_000


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


class Clock:
    def __init__(self):
        self.now = float(NOW)

    def __call__(self):
        return self.now


@unittest.skipUnless(HAS_LIBRARIES, "the WebAuthn library is not installed")
class SiteCase(unittest.TestCase):
    mode = "sign-in"

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.clock = Clock()
        self.logs = []
        self.key = ec.generate_private_key(ec.SECP384R1())
        self.authority = Authority.from_config(Presence(), {"controller-policy-file": "policy.json"}, self.root)
        self.authority.policy.add("Alice", xuid=XUID, player_uuid=PLAYER)
        self.store = WebStore(self.root / "web", clock=self.clock)
        log = lambda level, message: self.logs.append((level, message))  # noqa: E731
        self.auth = WebAuth(self.store, LinkCodes(clock=self.clock), ORIGIN, self.authority, clock=self.clock, log=log)
        config = SiteConfig("Alice's site", self.mode, ISSUER, f"{ISSUER}/endbot/start",
                            {"k1": self.key.public_key()})
        self.site = TrustedSite(config, self.auth, self.store, self.authority, log=log, clock=self.clock)

    def begin(self):
        """Start a flow; return the browser's cookie and the nonce the site receives."""
        reply = self.site.start()
        self.assertEqual(reply["status"], 303)
        query = reply["location"].split("?", 1)[1]
        return reply["siteCookie"], query.split("&")[0].split("=", 1)[1]

    def sign(self, claims, *, header=None, key=None, signature=None, raw_header=None):
        header = {"alg": "ES384", "kid": "k1", "typ": "endbot-site+jwt"} if header is None else header
        encoded = f"{raw_header or b64(json.dumps(header).encode())}.{b64(json.dumps(claims).encode())}"
        der = (key or self.key).sign(encoded.encode(), ec.ECDSA(hashes.SHA384()))
        r, s = decode_dss_signature(der)
        return f"{encoded}.{signature or b64(r.to_bytes(48, 'big') + s.to_bytes(48, 'big'))}"

    def claims(self, nonce, **changes):
        claims = {"iss": ISSUER, "aud": ORIGIN, "sub": XUID, "gtg": "Alice", "nonce": nonce,
                  "iat": NOW, "exp": NOW + 60}
        claims.update(changes)
        return {key: value for key, value in claims.items() if value is not None}

    def finish(self, assertion, cookie, origin=ISSUER, token=None):
        return self.site.finish(assertion, cookie, origin, token)

    def refused(self, reply, code="site_refused"):
        self.assertEqual((reply["status"], reply["location"]), (303, f"/?site_error={code}"), reply)
        self.assertNotIn("session", reply)
        self.assertEqual(self.store.accounts(), [])


class GuideTests(SiteCase):
    def test_the_guides_reference_signer_is_accepted(self):
        guide = Path(__file__).resolve().parents[3] / "docs" / "WEB_DEPLOY.md"
        block = guide.read_text(encoding="utf-8").split("<!-- reference-signer:", 1)[1]
        source = block.split("```python\n", 1)[1].split("```", 1)[0]
        namespace = {}
        exec(source, namespace)  # noqa: S102 - the repository's own documentation
        pem = self.key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                     serialization.NoEncryption())
        cookie, nonce = self.begin()
        with patch("time.time", return_value=NOW):
            assertion = namespace["sign_assertion"](pem, "k1", ISSUER, ORIGIN, XUID, "Alice", nonce)
        reply = self.finish(assertion, cookie)
        self.assertEqual((reply["status"], reply["location"]), (303, "/"), reply)
        self.assertEqual(self.auth.principal(reply["session"]).xuid, XUID)


class ClaimTests(SiteCase):
    def test_a_good_assertion_signs_in(self):
        cookie, nonce = self.begin()
        reply = self.finish(self.sign(self.claims(nonce)), cookie)
        self.assertEqual((reply["status"], reply["location"]), (303, "/"))
        account = self.store.account_for_xuid(XUID)
        self.assertEqual(self.store.sessions(account["id"])[0]["via"], "site")
        self.assertEqual(self.auth.principal(reply["session"]).xuid, XUID)

    def test_the_start_redirect_carries_the_nonce_and_return_url(self):
        reply = self.site.start()
        self.assertTrue(reply["location"].startswith(f"{ISSUER}/endbot/start?nonce="))
        self.assertIn("return=https%3A%2F%2Fendbot.example%2Fauth%2Fsite%2Freturn", reply["location"])

    def test_every_required_claim_is_checked(self):
        for name, changes in (
            ("wrong iss", {"iss": "https://evil.example"}),
            ("missing iss", {"iss": None}),
            ("wrong aud", {"aud": "https://other.example"}),
            ("aud with a path", {"aud": f"{ORIGIN}/"}),
            ("missing aud", {"aud": None}),
            ("non-decimal sub", {"sub": "12ab"}),
            ("empty sub", {"sub": ""}),
            ("numeric sub", {"sub": 2535412345678901}),
            ("missing sub", {"sub": None}),
            ("missing gtg", {"gtg": None}),
            ("missing iat", {"iat": None}),
            ("missing exp", {"exp": None}),
            ("boolean exp", {"exp": True}),
            ("float iat", {"iat": NOW + 0.5}),
            ("lifetime over 120 s", {"exp": NOW + 121}),
            ("exp before iat", {"exp": NOW - 1}),
        ):
            with self.subTest(name):
                cookie, nonce = self.begin()
                self.refused(self.finish(self.sign(self.claims(nonce, **changes)), cookie))

    def test_the_nonce_is_this_browsers(self):
        cookie, nonce = self.begin()
        other_cookie, other_nonce = self.begin()
        self.refused(self.finish(self.sign(self.claims(other_nonce)), cookie))
        # A copied assertion is useless in another browser, and that browser's flow is spent.
        self.refused(self.finish(self.sign(self.claims(nonce)), other_cookie))
        cookie, nonce = self.begin()
        claims = self.claims(nonce)
        del claims["nonce"]
        self.refused(self.finish(self.sign(claims), cookie))

    def test_the_nonce_is_consumed_on_the_first_attempt_valid_or_not(self):
        cookie, nonce = self.begin()
        self.refused(self.finish(self.sign(self.claims(nonce, iss="https://evil.example")), cookie))
        self.refused(self.finish(self.sign(self.claims(nonce)), cookie), "site_expired")

    def test_a_good_assertion_cannot_be_replayed(self):
        cookie, nonce = self.begin()
        assertion = self.sign(self.claims(nonce))
        self.assertEqual(self.finish(assertion, cookie)["location"], "/")
        reply = self.finish(assertion, cookie)
        self.assertEqual(reply["location"], "/?site_error=site_expired")
        self.assertNotIn("session", reply)

    def test_the_time_window_allows_30_s_of_skew(self):
        cookie, nonce = self.begin()
        reply = self.finish(self.sign(self.claims(nonce, iat=NOW + 30, exp=NOW + 90)), cookie)
        self.assertEqual(reply["location"], "/")
        self.store.delete_account(XUID)
        for name, changes in (("issued in the future", {"iat": NOW + 31, "exp": NOW + 91}),
                              ("expired past the skew", {"iat": NOW - 90, "exp": NOW - 30})):
            with self.subTest(name):
                cookie, nonce = self.begin()
                self.refused(self.finish(self.sign(self.claims(nonce, **changes)), cookie), "site_expired")
        self.assertTrue(any("check the clocks" in message for _, message in self.logs))
        cookie, nonce = self.begin()
        reply = self.finish(self.sign(self.claims(nonce, iat=NOW - 89, exp=NOW - 29)), cookie)
        self.assertEqual(reply["location"], "/")

    def test_an_abandoned_flow_expires_after_two_minutes(self):
        cookie, nonce = self.begin()
        assertion = self.sign(self.claims(nonce, iat=NOW + 120, exp=NOW + 150))
        self.clock.now += 120
        self.refused(self.finish(assertion, cookie), "site_expired")

    def test_a_missing_or_unknown_cookie_is_expired_and_costs_nothing(self):
        cookie, nonce = self.begin()
        assertion = self.sign(self.claims(nonce))
        for bad in (None, "", "unknown", 5):
            self.refused(self.finish(assertion, bad), "site_expired")
        self.assertEqual(self.finish(assertion, cookie)["location"], "/")

    def test_pending_nonces_are_bounded(self):
        for _ in range(100):
            self.assertEqual(self.site.start()["status"], 303)
        self.assertEqual(self.site.start()["status"], 429)
        self.clock.now += 121
        self.assertEqual(self.site.start()["status"], 303)


class SignatureTests(SiteCase):
    def test_only_es384_with_a_listed_kid_and_the_right_type_is_accepted(self):
        for name, header in (
            ("alg none", {"alg": "none", "kid": "k1", "typ": "endbot-site+jwt"}),
            ("alg ES256", {"alg": "ES256", "kid": "k1", "typ": "endbot-site+jwt"}),
            ("alg HS384", {"alg": "HS384", "kid": "k1", "typ": "endbot-site+jwt"}),
            ("no alg", {"kid": "k1", "typ": "endbot-site+jwt"}),
            ("unknown kid", {"alg": "ES384", "kid": "k2", "typ": "endbot-site+jwt"}),
            ("no kid", {"alg": "ES384", "typ": "endbot-site+jwt"}),
            ("kid not a string", {"alg": "ES384", "kid": ["k1"], "typ": "endbot-site+jwt"}),
            ("wrong typ", {"alg": "ES384", "kid": "k1", "typ": "JWT"}),
            ("no typ", {"alg": "ES384", "kid": "k1"}),
            ("crit", {"alg": "ES384", "kid": "k1", "typ": "endbot-site+jwt", "crit": ["x"]}),
        ):
            with self.subTest(name):
                cookie, nonce = self.begin()
                self.refused(self.finish(self.sign(self.claims(nonce), header=header), cookie))

    def test_a_signature_from_another_key_is_refused_and_logged_without_the_assertion(self):
        cookie, nonce = self.begin()
        assertion = self.sign(self.claims(nonce), key=ec.generate_private_key(ec.SECP384R1()))
        self.refused(self.finish(assertion, cookie))
        self.assertTrue(any("bad signature (kid k1)" in message for _, message in self.logs))
        self.assertFalse(any(assertion in message for _, message in self.logs))

    def test_a_changed_claim_breaks_the_signature(self):
        cookie, nonce = self.begin()
        header, _claims, signature = self.sign(self.claims(nonce)).split(".")
        forged = b64(json.dumps(self.claims(nonce, sub="2535400000000002")).encode())
        self.refused(self.finish(f"{header}.{forged}.{signature}", cookie))

    def test_malformed_assertions_are_refused(self):
        good = None
        for name, build in (
            ("not a string", lambda c: 5),
            ("two parts", lambda c: "a.b"),
            ("four parts", lambda c: f"{c}.x"),
            ("empty signature", lambda c: c.rsplit(".", 1)[0] + "."),
            ("DER signature length", lambda c: c.rsplit(".", 1)[0] + "." + b64(b"x" * 72)),
            ("padded signature", lambda c: c + "="),
            ("too large", lambda c: c + "A" * 9000),
            ("non-ascii", lambda c: c.replace(".", "é", 1)),
            ("header not an object", lambda c: b64(b"[]") + "." + c.split(".", 1)[1]),
            ("header not JSON", lambda c: b64(b"{") + "." + c.split(".", 1)[1]),
        ):
            with self.subTest(name):
                cookie, nonce = self.begin()
                good = self.sign(self.claims(nonce))
                self.refused(self.finish(build(good), cookie))

    def test_deeply_nested_json_is_refused_not_a_crash(self):
        deep = b64(b"[" * 3000 + b"]" * 3000)
        for assertion in (f"{deep}.e30.AAAA", f"{b64(b'{}')}.{deep}.AAAA"):
            cookie, _nonce = self.begin()
            self.refused(self.finish(assertion, cookie))

    def test_a_post_from_any_other_origin_is_refused(self):
        for origin in ("https://evil.example", ORIGIN, None, f"{ISSUER}/"):
            with self.subTest(origin=origin):
                cookie, nonce = self.begin()
                self.refused(self.finish(self.sign(self.claims(nonce)), cookie, origin))


class GrantRuleTests(SiteCase):
    def test_an_xuid_without_a_grant_is_refused_and_logged(self):
        cookie, nonce = self.begin()
        reply = self.finish(self.sign(self.claims(nonce, sub="2535400000000009")), cookie)
        self.refused(reply, "no_access")
        self.assertTrue(any("2535400000000009" in message for _, message in self.logs))

    def test_a_revoked_xuid_is_refused(self):
        self.authority.operation(["remove"], "Alice")
        cookie, nonce = self.begin()
        self.refused(self.finish(self.sign(self.claims(nonce)), cookie), "no_access")

    def test_a_pending_gamertag_is_never_bound_by_a_site_claim(self):
        self.authority.operation(["add"], "Bob")
        cookie, nonce = self.begin()
        reply = self.finish(self.sign(self.claims(nonce, sub="2535400000000002", gtg="Bob")), cookie)
        self.refused(reply, "no_access")
        self.assertFalse(any(grant.gamertag == "Bob" and grant.xuid
                             for grant in self.authority.policy.state.grants))

    def test_a_gamertag_mismatch_is_logged_but_does_not_decide(self):
        cookie, nonce = self.begin()
        reply = self.finish(self.sign(self.claims(nonce, gtg="Mallory")), cookie)
        self.assertEqual(reply["location"], "/")
        self.assertEqual(self.store.account_for_xuid(XUID)["gamertag"], "Alice")
        self.assertTrue(any("Mallory" in message for _, message in self.logs))

    def test_a_revoke_ends_a_site_session_at_the_next_request(self):
        cookie, nonce = self.begin()
        token = self.finish(self.sign(self.claims(nonce)), cookie)["session"]
        self.authority.operation(["remove"], "Alice")
        self.assertIsNone(self.auth.principal(token))

    def test_a_site_sign_in_cannot_change_a_role(self):
        self.authority.roles.set(XUID, "controller")
        cookie, nonce = self.begin()
        token = self.finish(self.sign(self.claims(nonce, role="scripter")), cookie)["session"]
        self.assertEqual(self.auth.principal(token).role, "controller")

    def test_signing_in_again_refreshes_the_recent_assertion_and_replaces_the_session(self):
        cookie, nonce = self.begin()
        old = self.finish(self.sign(self.claims(nonce)), cookie)["session"]
        self.clock.now += 300
        refused = self.auth.handle("/auth/passkey/remove", {"id": "none"}, old)
        self.assertEqual(refused["body"]["code"], "reauthenticate")
        cookie, nonce = self.begin()
        claims = self.claims(nonce, iat=NOW + 300, exp=NOW + 360)
        new = self.finish(self.sign(claims), cookie, token=old)["session"]
        self.assertIsNone(self.auth.session(old))
        self.assertEqual(self.auth.handle("/auth/passkey/remove", {"id": "none"}, new)["status"], 404)


class LinkModeTests(SiteCase):
    mode = "link"

    def test_a_busy_or_failing_ceremony_is_a_redirect_not_a_crash(self):
        from endbotd.web_auth import MAX_CEREMONIES

        cookie, nonce = self.begin()
        for _ in range(MAX_CEREMONIES):
            self.auth.handle("/auth/login/options", {})
        self.assertEqual(self.finish(self.sign(self.claims(nonce)), cookie)["location"], "/?site_error=busy")
        self.clock.now += 121
        cookie, nonce = self.begin()
        claims = self.claims(nonce, iat=NOW + 121, exp=NOW + 150)
        self.store.delete_account(XUID)  # So the failing step is the write that creates it.
        with patch("endbotd.web_store.os.replace", side_effect=OSError("disk full")):
            self.assertEqual(self.finish(self.sign(claims), cookie)["location"], "/?site_error=unavailable")

    def test_link_mode_registers_a_passkey_before_any_session(self):
        cookie, nonce = self.begin()
        reply = self.finish(self.sign(self.claims(nonce)), cookie)
        self.assertNotIn("session", reply)
        self.assertTrue(reply["location"].startswith("/?site_ceremony="))
        ceremony = reply["location"].split("=", 1)[1]
        account = self.store.account_for_xuid(XUID)
        self.assertEqual(self.store.sessions(account["id"]), [])
        started = self.site.registration({"ceremony": ceremony})["body"]
        self.assertEqual(started["ceremony"], ceremony)
        # The options go out once.
        self.assertEqual(self.site.registration({"ceremony": ceremony})["status"], 400)
        device = SoftAuthenticator(ORIGIN, "endbot.example")
        verified = self.auth.handle("/auth/link/verify", {"ceremony": ceremony,
                                                          "credential": device.register(started["options"])})
        self.assertEqual(verified["status"], 200)
        self.assertEqual(self.store.sessions(account["id"])[0]["via"], "site")

    def test_unknown_ceremonies_get_no_options(self):
        for body in ({"ceremony": "nope"}, {"ceremony": 5}, {}, []):
            self.assertEqual(self.site.registration(body)["status"], 400)


class ConfigTests(SiteCase):
    def write_key(self, name, curve=None):
        key = ec.generate_private_key(curve or ec.SECP384R1())
        (self.root / name).write_bytes(key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
        return name

    def section(self, **changes):
        section = {"enabled": True, "name": "Alice's site", "mode": "link", "issuer": ISSUER,
                   "start-url": f"{ISSUER}/start", "keys": [{"kid": "a", "public-key-file": self.write_key("a.pem")}]}
        section.update(changes)
        return section

    def test_off_by_default(self):
        self.assertIsNone(SiteConfig.load({}, self.root))
        self.assertIsNone(SiteConfig.load({"enabled": False}, self.root))

    def test_a_valid_section_loads_two_keys_for_rotation(self):
        keys = [{"kid": "a", "public-key-file": self.write_key("a.pem")},
                {"kid": "b", "public-key-file": self.write_key("b.pem")}]
        self.assertEqual(sorted(SiteConfig.load(self.section(keys=keys), self.root).keys), ["a", "b"])

    def test_invalid_sections_are_refused(self):
        for name, changes in (
            ("no name", {"name": ""}),
            ("a malformed issuer", {"issuer": "https://["}),
            ("a malformed start URL", {"start-url": "https://["}),
            ("wrong mode", {"mode": "both"}),
            ("http issuer", {"issuer": "http://alice.example"}),
            ("http start", {"start-url": "http://alice.example/start"}),
            ("no keys", {"keys": []}),
            ("three keys", {"keys": [{"kid": str(i), "public-key-file": "a.pem"} for i in range(3)]}),
            ("duplicate kid", {"keys": [{"kid": "a", "public-key-file": "a.pem"},
                                        {"kid": "a", "public-key-file": "a.pem"}]}),
            ("missing file", {"keys": [{"kid": "a", "public-key-file": "missing.pem"}]}),
            ("bad entry", {"keys": ["a"]}),
        ):
            with self.subTest(name), self.assertRaises(SiteConfigError):
                SiteConfig.load(self.section(**changes), self.root)

    def test_a_section_that_is_not_a_table_is_refused(self):
        with self.assertRaises(SiteConfigError):
            SiteConfig.load("enabled", self.root)

    def test_a_key_on_another_curve_is_refused(self):
        name = self.write_key("p256.pem", ec.SECP256R1())
        with self.assertRaisesRegex(SiteConfigError, "P-384"):
            SiteConfig.load(self.section(keys=[{"kid": "a", "public-key-file": name}]), self.root)


class ServiceRoutingTests(ConfigTests):
    def service(self, site=None):
        section = {"enabled": True, "origin": ORIGIN}
        if site is not None:
            section["trusted-site"] = site
        return WebService(section, self.root, self.authority, base=self.root,
                          log=lambda level, message: self.logs.append((level, message)))

    def test_without_a_site_there_is_no_button_and_its_paths_are_404(self):
        web = self.service()
        self.assertEqual(web.http("/auth/info", {})["body"], {"site": None})
        for path in ("/auth/site/start", "/auth/site/return", "/auth/site/options"):
            self.assertEqual(web.http(path, {})["status"], 404)

    def test_a_broken_section_keeps_the_site_off_and_passkeys_on(self):
        for section in (self.section(issuer="https://["), "not a table"):
            with self.subTest(section=section):
                self.assertIsNone(self.service(section).site)
        web = self.service(self.section(mode="both"))
        self.assertIsNone(web.site)
        self.assertTrue(web.available)
        self.assertEqual(web.http("/auth/site/start", {})["status"], 404)
        self.assertEqual(web.http("/auth/login/options", {})["status"], 200)
        self.assertTrue(any("Trusted site disabled" in message for _, message in self.logs))

    def test_the_routes_reach_the_site(self):
        web = self.service(self.section())
        self.assertEqual(web.http("/auth/info", {})["body"]["site"], {"name": "Alice's site", "mode": "link"})
        started = web.http("/auth/site/start", {})
        self.assertEqual(started["status"], 303)
        reply = web.http("/auth/site/return", {"assertion": "x"}, request_origin=ISSUER,
                         site_cookie=started["siteCookie"])
        self.assertEqual(reply["location"], "/?site_error=site_refused")
        self.assertEqual(web.http("/auth/site/other", {})["status"], 404)

    def test_revoke_says_the_site_can_vouch_again(self):
        web = self.service(self.section())
        web.store.ensure_account(XUID, PLAYER, "Alice")
        result = web.revoke(f"xuid:{XUID}")
        self.assertEqual(len(result["messages"]), 2)
        self.assertIn("can vouch again", result["messages"][1])

    def test_revoke_site_sessions_reaches_what_link_mode_created(self):
        web = self.service(self.section())
        self.authority.policy.add("Alice", xuid=XUID, player_uuid=PLAYER)
        started = web.auth.registration_options(LinkIdentity(XUID, PLAYER, "Alice"), via="site")["body"]
        device = SoftAuthenticator(ORIGIN, "endbot.example")
        reply = web.auth.handle("/auth/link/verify", {"ceremony": started["ceremony"],
                                                      "credential": device.register(started["options"])})
        account = web.store.account_for_xuid(XUID)
        self.assertEqual(web.store.sessions(account["id"])[0]["via"], "site")
        self.assertEqual(web.store.passkeys(account["id"])[0]["via"], "site")
        self.assertIn("1 passkeys", web.revoke_site_sessions()["messages"][0])
        self.assertIsNone(web.auth.session(reply["session"]))
        self.assertEqual(web.store.passkeys(account["id"]), [])

    def test_revoke_site_sessions_ends_only_site_sessions(self):
        web = self.service(self.section())
        account = web.store.ensure_account(XUID, PLAYER, "Alice")
        site_token = web.auth.issue_session(account, "site")
        passkey_token = web.auth.issue_session(account, "passkey")
        removed = []
        web.on_sessions_removed = removed.extend
        self.assertTrue(web.revoke_site_sessions()["ok"])
        self.assertIsNone(web.auth.session(site_token))
        self.assertIsNotNone(web.auth.session(passkey_token))
        self.assertEqual(len(removed), 1)


if __name__ == "__main__":
    unittest.main()
