"""WebAuthn ceremonies and sessions, verified in endbotd (docs/WEB.md P4, P5).

The Worker only carries these requests. The origin and RP ID come from endbotd's
own configuration, never from anything relayed, and the values that matter are
signed by the authenticator, so a modified Worker cannot make an assertion for
another origin or account.

``handle`` is the whole HTTP surface: it takes a path, a JSON body and the session
token from the cookie, and returns a status, a JSON body and what to do with the
cookie. The token is the only secret in a result, and the relay never forwards it
to the browser except as the cookie.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

from webauthn import (
    base64url_to_bytes,
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import bytes_to_base64url
from webauthn.helpers.cose import COSEAlgorithmIdentifier
from webauthn.helpers.exceptions import WebAuthnException
from webauthn.helpers.structs import (
    AttestationConveyancePreference,
    AuthenticatorSelectionCriteria,
    AuthenticatorTransport,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from endbot_core.principal import Principal
from endbotd.authority import Authority
from endbotd.web_store import LinkCodeEntryClosed, LinkCodes, LinkIdentity, WebStore

CEREMONY_SECONDS = 120.0
MAX_CEREMONIES = 100
RECENT_ASSERTION_SECONDS = 300.0
SESSION_IDLE_SECONDS = 7 * 86400.0
SESSION_ABSOLUTE_SECONDS = 30 * 86400.0
LAST_SEEN_SECONDS = 3600.0
# RS256 is for Windows Hello: without it, a PC whose TPM offers only RSA is left out of the passkey prompt.
ALGORITHMS = [COSEAlgorithmIdentifier.ECDSA_SHA_256, COSEAlgorithmIdentifier.EDDSA,
              COSEAlgorithmIdentifier.RSASSA_PKCS1_v1_5_SHA_256]
LABEL_CHARACTERS = 64
# A person's real devices fit well under this; it bounds what any one proof can leave behind.
MAX_PASSKEYS_PER_ACCOUNT = 10


class AuthError(Exception):
    """A refusal the browser sees: a status and a code, never the reason in detail."""

    def __init__(self, status: int, code: str) -> None:
        super().__init__(code)
        self.status = status
        self.code = code


@dataclass(frozen=True, slots=True)
class _Ceremony:
    kind: str
    challenge: bytes
    expires: float
    # The player a registration is for. A login proves who it is by its credential.
    identity: LinkIdentity | None = None
    # What proved them: a link code ("passkey", the default) or a site assertion ("site").
    via: str = "passkey"


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("ascii", "replace")).hexdigest()


def _label(body: dict) -> str:
    label = body.get("label")
    return label[:LABEL_CHARACTERS] if isinstance(label, str) and label.isprintable() else ""


def _credential(body: dict) -> dict:
    credential = body.get("credential")
    if not isinstance(credential, dict) or not isinstance(credential.get("id"), str):
        raise AuthError(400, "invalid_request")
    return credential


class WebAuth:
    def __init__(self, store: WebStore, codes: LinkCodes, origin: str, authority: Authority, *,
                 log: Callable[[str, str], None] | None = None, clock: Callable[[], float] = time.time,
                 on_sessions_removed: Callable[[list[str]], None] | None = None,
                 on_passkey_added: Callable[[dict], None] | None = None) -> None:
        self.store = store
        self.codes = codes
        self.origin = origin
        self.rp_id = urlsplit(origin).hostname
        self._authority = authority
        self._log = log or (lambda level, message: None)
        self._clock = clock
        self._on_sessions_removed = on_sessions_removed or (lambda hashes: None)
        self._on_passkey_added = on_passkey_added or (lambda account: None)
        self._ceremonies: dict[str, _Ceremony] = {}

    # -- the one entry point -----------------------------------------------------

    def handle(self, path: str, body: object, token: str | None = None) -> dict:
        """``{status, body}`` plus ``session`` (a new token) or ``clearSession`` for the relay's cookie."""
        routes = {
            "/auth/link/options": self._link_options,
            "/auth/link/verify": self._link_verify,
            "/auth/login/options": self._login_options,
            "/auth/login/verify": self._login_verify,
            "/auth/logout": self._logout,
            "/auth/devices": self._devices,
            "/auth/session/remove": self._remove_session,
            "/auth/passkey/remove": self._remove_passkey,
        }
        route = routes.get(path)
        if route is None:
            return {"status": 404, "body": {"code": "not_found"}}
        if not isinstance(body, dict):
            return {"status": 400, "body": {"code": "invalid_request"}}
        try:
            return route(body, token)
        except AuthError as error:
            result = {"status": error.status, "body": {"code": error.code}}
            if error.status == 401:
                result["clearSession"] = True
            return result
        except OSError as error:
            self._log("error", f"Web store write failed: {error}")
            return {"status": 503, "body": {"code": "unavailable"}}

    # -- ceremonies --------------------------------------------------------------

    def _begin(self, kind: str, challenge: bytes, identity: LinkIdentity | None = None,
               via: str = "passkey") -> str:
        now = self._clock()
        self._ceremonies = {key: entry for key, entry in self._ceremonies.items() if entry.expires > now}
        if len(self._ceremonies) >= MAX_CEREMONIES:
            raise AuthError(429, "busy")
        ceremony = secrets.token_urlsafe(16)
        self._ceremonies[ceremony] = _Ceremony(kind, challenge, now + CEREMONY_SECONDS, identity, via)
        return ceremony

    def _finish(self, body: dict, kind: str) -> _Ceremony:
        """Consume the ceremony on the first attempt, valid or not; bound to its kind."""
        key = body.get("ceremony")
        ceremony = self._ceremonies.pop(key, None) if isinstance(key, str) else None
        if ceremony is None or ceremony.kind != kind or ceremony.expires <= self._clock():
            raise AuthError(400, "expired")
        return ceremony

    def _allowed(self, gamertag: str, xuid: str, player_id: str) -> bool:
        with self._authority.lock:
            return self._authority.control_decision(gamertag, xuid, player_id)[0]

    def _link_options(self, body: dict, token: str | None) -> dict:
        code = body.get("code")
        if not isinstance(code, str) or len(code) > 32:
            raise AuthError(400, "invalid_code")
        try:
            identity = self.codes.redeem(code)
        except LinkCodeEntryClosed:
            raise AuthError(429, "link_closed") from None
        if identity is None:
            raise AuthError(400, "invalid_code")
        if not self._allowed(identity.gamertag, identity.xuid, identity.player_id):
            raise AuthError(403, "no_access")
        return self.registration_options(identity)

    def registration_options(self, identity: LinkIdentity, *, via: str = "passkey") -> dict:
        """Passkey registration for a proven identity: a link code, or a site assertion (P12)."""
        account = self.store.ensure_account(identity.xuid, identity.player_id, identity.gamertag)
        if len(self.store.passkeys(account["id"])) >= MAX_PASSKEYS_PER_ACCOUNT:
            raise AuthError(409, "passkey_limit")
        options = generate_registration_options(
            rp_id=self.rp_id, rp_name="Endbot", user_id=base64url_to_bytes(account["userHandle"]),
            user_name=identity.gamertag, user_display_name=identity.gamertag,
            timeout=int(CEREMONY_SECONDS * 1000), attestation=AttestationConveyancePreference.NONE,
            authenticator_selection=AuthenticatorSelectionCriteria(
                resident_key=ResidentKeyRequirement.REQUIRED, user_verification=UserVerificationRequirement.REQUIRED,
            ),
            exclude_credentials=[PublicKeyCredentialDescriptor(id=base64url_to_bytes(entry["id"]))
                                 for entry in self.store.passkeys(account["id"])],
            supported_pub_key_algs=ALGORITHMS,
        )
        ceremony = self._begin("register", options.challenge, identity, via)
        return {"status": 200, "body": {"ceremony": ceremony, "options": json.loads(options_to_json(options))}}

    def _link_verify(self, body: dict, token: str | None) -> dict:
        ceremony = self._finish(body, "register")
        credential = _credential(body)
        identity = ceremony.identity
        account = self.store.account_for_xuid(identity.xuid)
        # The grant or the account may have gone since the code was used.
        if account is None or not self._allowed(identity.gamertag, identity.xuid, identity.player_id):
            raise AuthError(403, "no_access")
        try:
            verified = verify_registration_response(
                credential=credential, expected_challenge=ceremony.challenge, expected_rp_id=self.rp_id,
                expected_origin=self.origin, require_user_verification=True, supported_pub_key_algs=ALGORITHMS,
            )
        except (WebAuthnException, ValueError, KeyError, TypeError, AttributeError) as error:
            self._log("warning", f"Web passkey registration refused for {identity.gamertag}: {error}")
            raise AuthError(400, "passkey_refused") from None
        # Again here: two ceremonies can be open at once.
        if len(self.store.passkeys(account["id"])) >= MAX_PASSKEYS_PER_ACCOUNT:
            raise AuthError(409, "passkey_limit")
        credential_id = bytes_to_base64url(verified.credential_id)
        if self.store.find_passkey(credential_id) is not None:
            self._log("warning", f"Web passkey registration refused for {identity.gamertag}: credential already used")
            raise AuthError(400, "passkey_refused")
        response = credential.get("response") if isinstance(credential.get("response"), dict) else {}
        transports = response.get("transports") if isinstance(response.get("transports"), list) else []
        # Unsigned and browser-supplied: keep only the strings we know.
        known = {transport.value for transport in AuthenticatorTransport}
        self.store.add_passkey({
            "id": credential_id,
            "account": account["id"],
            "publicKey": bytes_to_base64url(verified.credential_public_key),
            "signCount": verified.sign_count,
            "rpId": self.rp_id,
            "transports": [transport for transport in transports if isinstance(transport, str) and transport in known],
            "label": _label(body),
            "created": self._clock(),
            "lastUsed": None,
            # A suspected site key exposure must reach what an assertion created (P12).
            "via": ceremony.via,
        })
        self._log("info", f"Web passkey added for {identity.gamertag}")
        self._on_passkey_added(account)
        return self._signed_in(account, ceremony.via, body)

    def _login_options(self, body: dict, token: str | None) -> dict:
        options = generate_authentication_options(
            rp_id=self.rp_id, timeout=int(CEREMONY_SECONDS * 1000),
            user_verification=UserVerificationRequirement.REQUIRED,
        )
        ceremony = self._begin("login", options.challenge)
        return {"status": 200, "body": {"ceremony": ceremony, "options": json.loads(options_to_json(options))}}

    def _login_verify(self, body: dict, token: str | None) -> dict:
        ceremony = self._finish(body, "login")
        credential = _credential(body)
        passkey = self.store.find_passkey(credential["id"])
        # Every refusal looks the same to the browser: nothing here tells who holds an account.
        if passkey is None or passkey.get("rpId") != self.rp_id:
            raise AuthError(400, "passkey_refused")
        account = self.store.account(passkey["account"])
        if account is None:
            raise AuthError(400, "passkey_refused")
        try:
            verified = verify_authentication_response(
                credential=credential, expected_challenge=ceremony.challenge, expected_rp_id=self.rp_id,
                expected_origin=self.origin, credential_public_key=base64url_to_bytes(passkey["publicKey"]),
                credential_current_sign_count=passkey["signCount"], require_user_verification=True,
            )
        except (WebAuthnException, ValueError, KeyError, TypeError, AttributeError) as error:
            self._log("warning", f"Web passkey sign-in refused for {account['gamertag']}: {error}")
            raise AuthError(400, "passkey_refused") from None
        if not self._allowed(account["gamertag"], account["xuid"], account["uuid"]):
            raise AuthError(403, "no_access")
        self.store.update_passkey(passkey["id"], signCount=verified.new_sign_count, lastUsed=self._clock())
        # Signing in again refreshes a recent assertion; the session it replaces ends.
        previous = self.session(token) if token else None
        if previous is not None and previous["account"] == account["id"]:
            self.end_session(previous)
        return self._signed_in(account, "passkey", body)

    # -- sessions ----------------------------------------------------------------

    def _signed_in(self, account: dict, via: str, body: dict) -> dict:
        token = self.issue_session(account, via, _label(body))
        return {"status": 200, "body": {"gamertag": account["gamertag"]}, "session": token}

    def issue_session(self, account: dict, via: str, label: str = "") -> str:
        """A new session; the token leaves here once and only its SHA-256 is stored (P5)."""
        token = secrets.token_urlsafe(32)
        now = self._clock()
        self.store.add_session({
            "id": secrets.token_urlsafe(12),
            "hash": _token_hash(token),
            "account": account["id"],
            "via": via,
            "label": label,
            "created": now,
            "lastSeen": now,
            "assertedAt": now,
            "grantRevision": self._authority.grant_revisions.get(account["uuid"]),
        })
        return token

    def session(self, token: str | None) -> dict | None:
        """The live session for this token, or None; an expired or orphaned one is deleted."""
        if not isinstance(token, str) or not token:
            return None
        session = self.store.find_session(_token_hash(token))
        if session is None:
            return None
        now = self._clock()
        account = self.store.account(session["account"])
        if (account is None or now - session["lastSeen"] >= SESSION_IDLE_SECONDS
                or now - session["created"] >= SESSION_ABSOLUTE_SECONDS
                or session["created"] < account.get("sessionsFrom", 0)):
            self.end_session(session)
            return None
        if now - session["lastSeen"] >= LAST_SEEN_SECONDS:
            self.store.update_session(session["hash"], lastSeen=now)
        return session

    def principal(self, token: str | None, language: str = "en") -> Principal | None:
        """The ``web`` principal for a request, checked again on every request (P5); None when refused."""
        session = self.session(token)
        if session is None:
            return None
        account = self.store.account(session["account"])
        if account is None:
            return None
        with self._authority.lock:
            principal = Principal(
                "web", name=account["gamertag"], xuid=account["xuid"], player_id=account["uuid"],
                grant_revision=session["grantRevision"], language=language,
                role=self._authority.role(account["xuid"]),
            )
            return principal if self._authority.authorize(principal) else None

    def end_session(self, session: dict) -> None:
        self.store.delete_session(session["hash"])
        self._on_sessions_removed([session["hash"]])

    def _required_session(self, token: str | None) -> dict:
        session = self.session(token)
        if session is None or self.principal(token) is None:
            raise AuthError(401, "signed_out")
        return session

    def _recent_session(self, token: str | None) -> dict:
        session = self._required_session(token)
        if self._clock() - session["assertedAt"] >= RECENT_ASSERTION_SECONDS:
            raise AuthError(403, "reauthenticate")
        return session

    def _logout(self, body: dict, token: str | None) -> dict:
        session = self.session(token)
        try:
            if session is not None:
                self.end_session(session)
        except OSError as error:
            # This browser still forgets it, and the page must say the session may stay valid elsewhere.
            self._log("error", f"Web store write failed: {error}")
            return {"status": 503, "body": {"code": "unavailable"}, "clearSession": True}
        return {"status": 200, "body": {}, "clearSession": True}

    # -- devices -----------------------------------------------------------------

    def _devices(self, body: dict, token: str | None) -> dict:
        session = self._required_session(token)
        return {"status": 200, "body": {
            "sessions": [{"id": entry["id"], "label": entry["label"], "via": entry["via"],
                          "created": entry["created"], "lastSeen": entry["lastSeen"],
                          "current": entry["hash"] == session["hash"]}
                         for entry in self.store.sessions(session["account"])],
            "passkeys": [{"id": entry["id"], "label": entry["label"], "created": entry["created"],
                          "lastUsed": entry["lastUsed"]}
                         for entry in self.store.passkeys(session["account"])],
        }}

    def _remove_session(self, body: dict, token: str | None) -> dict:
        session = self._required_session(token)
        target = next((entry for entry in self.store.sessions(session["account"])
                       if entry["id"] == body.get("id")), None)
        if target is None:
            raise AuthError(404, "not_found")
        self.end_session(target)
        current = target["hash"] == session["hash"]
        return {"status": 200, "body": {}, **({"clearSession": True} if current else {})}

    def _remove_passkey(self, body: dict, token: str | None) -> dict:
        session = self._recent_session(token)
        target = next((entry for entry in self.store.passkeys(session["account"])
                       if entry["id"] == body.get("id")), None)
        if target is None:
            raise AuthError(404, "not_found")
        self.store.delete_passkey(target["id"])
        return {"status": 200, "body": {}}
