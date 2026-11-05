"""The optional trusted external site (docs/WEB.md P12): off by default.

An operator who already signs players in on their own site lets it prove an XUID
instead of the in-game code. The assertion is a compact JWS, ES384 only, audience
bound, at most 120 s, and tied by an endbotd nonce to the browser that started the
flow. It can never create or widen a grant or a role: the XUID must already hold a
controller grant bound by an Xbox-authenticated join, and the same check runs again
on every later request.
"""

from __future__ import annotations

import base64
import binascii
import json
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.hazmat.primitives.serialization import load_pem_public_key

from endbot_core.enrollment import _is_xuid
from endbotd.authority import Authority
from endbotd.web_auth import AuthError, WebAuth
from endbotd.web_store import LinkIdentity, WebStore

MODES = ("link", "sign-in")
TYPE = "endbot-site+jwt"
NONCE_SECONDS = 120.0
MAX_NONCES = 100
MAX_ASSERTION_BYTES = 8192
MAX_LIFETIME = 120
CLOCK_SKEW = 30
MAX_KEYS = 2
SIGNATURE_BYTES = 96


class SiteConfigError(ValueError):
    """The ``[web.trusted-site]`` section is invalid; the site path stays off."""


class _Refused(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True, slots=True)
class SiteConfig:
    name: str
    mode: str
    issuer: str
    start_url: str
    keys: dict[str, ec.EllipticCurvePublicKey]

    @classmethod
    def load(cls, section: dict, base: Path) -> SiteConfig | None:
        """None when disabled or absent; raises ``SiteConfigError`` when enabled and invalid."""
        if not isinstance(section, dict):
            raise SiteConfigError("[web.trusted-site] must be a table")
        if not section or section.get("enabled", False) is not True:
            return None
        name, mode, issuer, start_url = (section.get(key) for key in ("name", "mode", "issuer", "start-url"))
        if not isinstance(name, str) or not name:
            raise SiteConfigError("name must be a non-empty string")
        if mode not in MODES:
            raise SiteConfigError(f"mode must be one of {MODES}")
        for key, value in (("issuer", issuer), ("start-url", start_url)):
            if not _https_url(value):
                raise SiteConfigError(f"{key} must be an https:// URL")
        if not _is_origin(issuer):
            raise SiteConfigError("issuer must be exactly the site's origin: lowercase, with no path or trailing slash")
        entries = section.get("keys")
        if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_KEYS:
            raise SiteConfigError(f"keys must list 1 to {MAX_KEYS} keys")
        keys: dict[str, ec.EllipticCurvePublicKey] = {}
        for entry in entries:
            kid, file = (entry.get(key) for key in ("kid", "public-key-file")) if isinstance(entry, dict) else (0, 0)
            if not isinstance(kid, str) or not kid or not isinstance(file, str) or not file:
                raise SiteConfigError("each key needs kid and public-key-file")
            if kid in keys:
                raise SiteConfigError(f"kid {kid} is listed twice")
            path = Path(file) if Path(file).is_absolute() else Path(base) / file
            try:
                key = load_pem_public_key(path.read_bytes())
            except (OSError, ValueError, TypeError) as error:
                raise SiteConfigError(f"key {kid} cannot be read from {path}: {error}") from error
            if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(key.curve, ec.SECP384R1):
                raise SiteConfigError(f"key {kid} must be a P-384 public key")
            keys[kid] = key
        return cls(name, mode, issuer, start_url, keys)


def _https_url(value: object) -> bool:
    try:
        parts = urlsplit(value) if isinstance(value, str) else None
        return parts is not None and parts.scheme == "https" and bool(parts.hostname)
    except ValueError:  # urlsplit refuses a malformed host such as "https://["
        return False


def _is_origin(value: str) -> bool:
    """The one spelling a browser sends as ``Origin``, which ``finish`` compares byte for byte."""
    parts = urlsplit(value)
    return value == f"https://{parts.netloc}" and value == value.lower() and "@" not in value \
        and not value.endswith(":443")


def _decode(part: str) -> bytes:
    """Strict base64url without padding: a JWS part has exactly one spelling."""
    if not part or "=" in part or not part.isascii():
        raise ValueError("invalid base64url")
    try:
        data = base64.b64decode(part + "=" * (-len(part) % 4), altchars=b"-_", validate=True)
    except (binascii.Error, ValueError) as error:
        raise ValueError("invalid base64url") from error
    if base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii") != part:
        raise ValueError("non-canonical base64url")
    return data


def _is_time(value: object) -> bool:
    return type(value) is int


class TrustedSite:
    def __init__(self, config: SiteConfig, auth: WebAuth, store: WebStore, authority: Authority, *,
                 log: Callable[[str, str], None] | None = None, clock: Callable[[], float] = time.time) -> None:
        self.config = config
        self._auth = auth
        self._store = store
        self._authority = authority
        self._log = log or (lambda level, message: None)
        self._clock = clock
        # Cookie value -> (nonce, expiry). A restart voids them, so no replay cache has to survive one (S6).
        self._pending: dict[str, tuple[str, float]] = {}
        # Registration options waiting for the browser that just came back from the site (mode link).
        self._registrations: dict[str, tuple[dict, float]] = {}

    @property
    def info(self) -> dict:
        return {"name": self.config.name, "mode": self.config.mode}

    def _purge(self, now: float) -> None:
        self._pending = {key: entry for key, entry in self._pending.items() if entry[1] > now}
        self._registrations = {key: entry for key, entry in self._registrations.items() if entry[1] > now}

    def start(self) -> dict:
        """``GET /auth/site/start``: a redirect to the site and the cookie that binds this browser."""
        now = self._clock()
        self._purge(now)
        if len(self._pending) >= MAX_NONCES:
            return {"status": 429, "body": {"code": "busy"}}
        cookie, nonce = secrets.token_urlsafe(16), secrets.token_urlsafe(16)
        self._pending[cookie] = (nonce, now + NONCE_SECONDS)
        query = urlencode({"nonce": nonce, "return": f"{self._auth.origin}/auth/site/return"})
        separator = "&" if urlsplit(self.config.start_url).query else "?"
        return {"status": 303, "location": f"{self.config.start_url}{separator}{query}", "siteCookie": cookie,
                "body": {}}

    def finish(self, assertion: object, cookie: object, request_origin: object, token: str | None = None) -> dict:
        """``POST /auth/site/return``. Every outcome is a redirect back to the web, carrying a code at most."""
        now = self._clock()
        self._purge(now)
        # One lookup without a started flow: junk POSTs lock nobody out (P12).
        pending = self._pending.pop(cookie, None) if isinstance(cookie, str) else None
        if pending is None:
            return self._back("site_expired")
        try:
            if request_origin != self.config.issuer:
                raise _Refused("site_refused", f"POST from origin {request_origin!r}")
            claims = self._verify(assertion, pending[0], now)
            return self._accepted(claims, token)
        except _Refused as error:
            if error.detail:
                self._log("warning", f"Trusted site assertion refused: {error.detail}")
            return self._back(error.code)
        except AuthError as error:
            return self._back(error.code)
        except OSError as error:
            self._log("error", f"Web store write failed: {error}")
            return self._back("unavailable")

    def _verify(self, assertion: object, nonce: str, now: float) -> dict:
        if not isinstance(assertion, str) or len(assertion) > MAX_ASSERTION_BYTES or assertion.count(".") != 2:
            raise _Refused("site_refused", "not a compact JWS")
        encoded_header, encoded_claims, encoded_signature = assertion.split(".")
        try:
            header = json.loads(_decode(encoded_header))
            signature = _decode(encoded_signature)
            claims = json.loads(_decode(encoded_claims))
        except (ValueError, UnicodeDecodeError, RecursionError):
            # Parsed before the signature is checked, so anyone with a started flow can send anything here.
            raise _Refused("site_refused", "malformed JWS") from None
        if not isinstance(header, dict) or not isinstance(claims, dict):
            raise _Refused("site_refused", "malformed JWS")
        kid = header.get("kid")
        if "crit" in header or header.get("alg") != "ES384" or header.get("typ") != TYPE:
            raise _Refused("site_refused", f"header refused (alg {header.get('alg')!r}, kid {kid!r})")
        key = self.config.keys.get(kid) if isinstance(kid, str) else None
        if key is None:
            raise _Refused("site_refused", f"unknown kid {kid!r}")
        if len(signature) != SIGNATURE_BYTES:
            raise _Refused("site_refused", f"bad signature length (kid {kid})")
        half = SIGNATURE_BYTES // 2
        der = encode_dss_signature(int.from_bytes(signature[:half], "big"), int.from_bytes(signature[half:], "big"))
        try:
            key.verify(der, f"{encoded_header}.{encoded_claims}".encode("ascii"), ec.ECDSA(hashes.SHA384()))
        except InvalidSignature:
            raise _Refused("site_refused", f"bad signature (kid {kid})") from None
        if claims.get("iss") != self.config.issuer:
            raise _Refused("site_refused", f"wrong iss (kid {kid})")
        if claims.get("aud") != self._auth.origin:
            raise _Refused("site_refused", f"wrong aud (kid {kid})")
        issued, expires = claims.get("iat"), claims.get("exp")
        if not _is_time(issued) or not _is_time(expires) or not 0 < expires - issued <= MAX_LIFETIME:
            raise _Refused("site_refused", f"iat and exp refused (kid {kid})")
        if issued > now + CLOCK_SKEW or expires + CLOCK_SKEW <= now:
            raise _Refused("site_expired", f"assertion outside its time window (kid {kid}); check the clocks")
        if not isinstance(claims.get("gtg"), str) or not secrets.compare_digest(
                str(claims.get("nonce")).encode(), nonce.encode()):
            raise _Refused("site_refused", f"wrong nonce or gtg (kid {kid})")
        if not isinstance(claims.get("sub"), str) or not _is_xuid(claims["sub"]):
            raise _Refused("site_refused", f"sub is not an XUID (kid {kid})")
        return claims

    def _bound_grant(self, xuid: str) -> dict | None:
        """An allowed row with a UUID exists only after an Xbox-authenticated join bound this XUID."""
        with self._authority.lock:
            rows = self._authority.report()
        return next((row for row in rows
                     if row.get("xuid") == xuid and row.get("state") == "allowed" and row.get("uuid")), None)

    def _accepted(self, claims: dict, token: str | None) -> dict:
        row = self._bound_grant(claims["sub"])
        if row is None:
            raise _Refused("no_access", f"XUID {claims['sub']} has no controller grant")
        if claims["gtg"].casefold() != row["name"].casefold():
            # Never used for authorization; the operator should still know.
            self._log("warning", f"Trusted site says GamerTag {claims['gtg']!r} for XUID {claims['sub']}, "
                                 f"bound as {row['name']!r}")
        identity = LinkIdentity(claims["sub"], row["uuid"], row["name"])
        if self.config.mode == "link":
            started = self._auth.registration_options(identity, via="site")
            ceremony = started["body"]["ceremony"]
            self._registrations[ceremony] = (started["body"], self._clock() + NONCE_SECONDS)
            return {"status": 303, "location": f"/?site_ceremony={ceremony}", "body": {}}
        account = self._store.ensure_account(identity.xuid, identity.player_id, identity.gamertag)
        # Signing in again refreshes a recent assertion, as a passkey does; the session it replaces ends.
        previous = self._auth.session(token) if token else None
        session = self._auth.issue_session(account, "site")
        if previous is not None and previous["account"] == account["id"]:
            self._auth.end_session(previous)
        self._log("info", f"Web sign-in through the trusted site for {identity.gamertag}")
        return {"status": 303, "location": "/", "session": session, "body": {}}

    def registration(self, body: object) -> dict:
        """``POST /auth/site/options``: the options for the ceremony the return redirect named."""
        ceremony = body.get("ceremony") if isinstance(body, dict) else None
        self._purge(self._clock())
        entry = self._registrations.pop(ceremony, None) if isinstance(ceremony, str) else None
        if entry is None:
            return {"status": 400, "body": {"code": "site_expired"}}
        return {"status": 200, "body": entry[0]}

    def _back(self, code: str) -> dict:
        return {"status": 303, "location": f"/?site_error={code}", "body": {}}
