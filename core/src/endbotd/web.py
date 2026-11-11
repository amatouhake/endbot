"""endbotd's side of the web: linking, accounts and how revocation reaches them (docs/WEB.md P3, P4, P6).

The Worker is a relay. Everything that decides who may do what lives here and in
``Authority``. An account exists only for an XUID with a controller grant, and a
code is issued only to that player, seen in game with Xbox authentication.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

from endbot_core.enrollment import _is_xuid
from endbot_core.i18n import tr
from endbotd.authority import Authority
from endbotd.web_store import LinkCodes, LinkIdentity, WebStore, WebStoreError

STORE_DIRECTORY = "web"


def web_origin(value: object) -> str | None:
    """The configured origin, exactly ``https://host[:port]``; ``http://localhost[:port]`` is the one exception,
    a secure context for development. Anything else raises: WebAuthn trusts this value (P4)."""
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError("web.origin must be a string")  # noqa: TRY004 - a config error, like the others
    parts = urlsplit(value)
    try:
        hostname, port = parts.hostname, parts.port
    except ValueError as error:
        raise ValueError(f"web.origin has an invalid port: {value}") from error
    secure = parts.scheme == "https" or (parts.scheme == "http" and hostname == "localhost")
    # A browser leaves out the default port, so an origin spelled with it could never match clientDataJSON.origin.
    if port == {"https": 443, "http": 80}.get(parts.scheme):
        raise ValueError(f"web.origin must leave out the default port, found {value}")
    host = f"{hostname}:{port}" if port is not None else hostname
    if not secure or not hostname or parts.netloc != host or value != f"{parts.scheme}://{host}":
        raise ValueError(f"web.origin must be https://host with no path, found {value}")
    return value


class WebService:
    def __init__(self, section: dict, stores_directory: Path, authority: Authority, *, base: Path | None = None,
                 log: Callable[[str, str], None] | None = None) -> None:
        self._log = log or (lambda level, message: None)
        self._authority = authority
        self.store: WebStore | None = None
        try:
            self.store = WebStore(Path(stores_directory) / STORE_DIRECTORY)
        except WebStoreError as error:
            # A broken store must not take /bot down with it; nobody can link or sign in until it is fixed.
            self._log("error", f"Web disabled: {error}; fix or move the file and restart Endbot")
        self.codes = LinkCodes(on_closed=lambda: self._log(
            "warning", "Web link code entry closed for 10 minutes after too many wrong codes"))
        # The relay closes these sessions' connections (P6); set once the link exists.
        self.on_sessions_removed: Callable[[list[str]], None] = lambda hashes: None
        # Told when a passkey is added, so the player hears about it in chat (P4).
        self.on_passkey_added: Callable[[dict], None] = lambda account: None
        self.configure(section, base)

    def configure(self, section: dict, base: Path | None) -> None:
        """Apply the settings: at start and after a deploy. The one store stays, so it keeps a single writer."""
        self.enabled = section.get("enabled", False) is True
        name = section.get("display-name", "")
        self.display_name = name[:64] if isinstance(name, str) and name.isprintable() else ""
        try:
            self.origin = web_origin(section.get("origin"))
        except ValueError as error:
            self.origin = None
            self._log("error", f"Web disabled: {error}")
        self.auth = None
        self.site = None
        if self.enabled and self.origin is not None and self.store is not None:
            # Imported here: an instance without the web never loads the WebAuthn library.
            try:
                from endbotd.web_auth import WebAuth
            except ImportError as error:
                self._log("error", f"Web disabled: the WebAuthn library is missing ({error}); reinstall Endbot")
            else:
                self.auth = WebAuth(self.store, self.codes, self.origin, self._authority, log=self._log,
                                    on_sessions_removed=lambda hashes: self.on_sessions_removed(hashes),
                                    on_passkey_added=lambda account: self.on_passkey_added(account))
                self.site = self._load_site(section, base, self._authority)

    def _load_site(self, section: dict, base: Path | None, authority: Authority):
        from endbotd.web_site import SiteConfig, SiteConfigError, TrustedSite

        try:
            config = SiteConfig.load(section.get("trusted-site") or {}, base or Path("."))
        except (SiteConfigError, ValueError, TypeError, AttributeError) as error:
            # P12: no site button, /auth/site/* is 404; passkeys and /botweb link keep working.
            self._log("error", f"Trusted site disabled: {error}")
            return None
        if config is None:
            return None
        return TrustedSite(config, self.auth, self.store, authority, log=self._log)

    @property
    def available(self) -> bool:
        return self.auth is not None

    # -- what the relay forwards -------------------------------------------------

    def http(self, path: str, body: object, token: str | None = None, *, request_origin: str | None = None,
             site_cookie: str | None = None, ip_bucket: str | None = None) -> dict:
        """One HTTP request from the relay. Nothing in it is trusted until checked here."""
        if self.auth is None:
            return {"status": 503, "body": {"code": "unavailable"}}
        if path == "/auth/info":
            return {"status": 200, "body": {"site": self.site.info if self.site is not None else None}}
        if path.startswith("/auth/site/"):
            if self.site is None:
                return {"status": 404, "body": {"code": "not_found"}}
            if path == "/auth/site/start":
                return self.site.start()
            if path == "/auth/site/return":
                assertion = body.get("assertion") if isinstance(body, dict) else None
                return self.site.finish(assertion, site_cookie, request_origin, token)
            if path == "/auth/site/options":
                return self.site.registration(body)
            return {"status": 404, "body": {"code": "not_found"}}
        return self.auth.handle(path, body, token, bucket=ip_bucket)

    # -- /botweb --------------------------------------------------------------

    def command(self, record: dict, language: str, argv: list[str]) -> list[str]:
        """``/botweb`` from an allowed player; ``record`` is their presence record, not the plugin's word."""
        action = argv[0].lower() if argv else "status"
        xuid = record.get("xuid") or ""
        if action == "help" and len(argv) == 1:
            return [tr(language, "Web help: link (a code for the web page) | status | unlink (delete your web account) | help")]
        if len(argv) > 1 or action not in {"link", "unlink", "status"} or not _is_xuid(xuid):
            return [tr(language, "Use /botweb link, /botweb unlink or /botweb status")]
        if self.store is None:
            return [tr(language, "The web is not available on this server")]
        if action == "status":
            return [self._status(xuid, language)]
        if action == "unlink":
            self.codes.void([xuid])
            if self.store.account_for_xuid(xuid) is None:
                return [tr(language, "This player has no web account")]
            try:
                self._remove_account(xuid)
            except OSError as error:
                self._failed_write(error)
                return [tr(language, "The web account could not be removed; try again")]
            return [tr(language, "Web account removed; its passkeys and sessions no longer work")]
        if not self.available:
            return [tr(language, "The web is not available on this server")]
        code = self.codes.issue(LinkIdentity(xuid, record["playerId"], record.get("name") or ""))
        return [tr(language, "Web link code: {code} (5 minutes, one use). Enter it at {origin}", code=code,
                   origin=self.origin)]

    def _status(self, xuid: str, language: str) -> str:
        account = self.store.account_for_xuid(xuid)
        if account is None:
            return tr(language, "This player has no web account; /botweb link creates one")
        return tr(language, "Web account linked: {passkeys} passkeys, {sessions} sessions",
                  passkeys=len(self.store.passkeys(account["id"])), sessions=len(self.store.sessions(account["id"])))

    def _remove_account(self, xuid: str) -> None:
        self.codes.void([xuid])
        self.on_sessions_removed(self.store.delete_account(xuid))

    def _failed_write(self, error: OSError) -> list[str]:
        self._log("error", f"Web store write failed: {error}")
        return [f"Web store write failed: {error}"]

    # -- the console and the endbot CLI ----------------------------------------

    def revoke(self, target: str) -> dict:
        """``endbot web revoke <GamerTag|xuid:id>``: the account, passkeys and sessions go; the grant stays."""
        if self.store is None:
            return {"ok": False, "messages": ["Web store is unavailable; see the endbotd log"]}
        if target.startswith("xuid:"):
            self.codes.void([target[len("xuid:"):]])
        account = self._find_account(target)
        if account is None:
            return {"ok": False, "messages": [f"No web account for {target}"]}
        try:
            self._remove_account(account["xuid"])
        except OSError as error:
            return {"ok": False, "messages": self._failed_write(error)}
        self._log("info", f"Web account of {account['gamertag']} revoked")
        note = ("The trusted site can vouch again at the next sign-in; to keep this player out, remove the "
                "controller grant or have the site stop vouching." if self.site is not None else None)
        message = (f"Web account of {account['gamertag']} removed; its passkeys and sessions no longer work. "
                   "The controller grant is unchanged.")
        return {"ok": True, "messages": [message, note] if note else [message]}

    def domain_changed(self) -> dict:
        if self.store is None:
            return {"ok": False, "messages": ["Web store is unavailable; see the endbotd log"]}
        self.on_sessions_removed(self.store.delete_credentials())
        self._log("info", "Web passkeys and sessions deleted: the domain changed")
        return {"ok": True, "messages": ["Every web passkey and session was deleted"]}

    def revoke_site_sessions(self) -> dict:
        """``endbot web revoke --site-sessions``: for a suspected key exposure (P12)."""
        if self.store is None:
            return {"ok": False, "messages": ["Web store is unavailable; see the endbotd log"]}
        try:
            removed = self.store.delete_sessions_via("site")
            passkeys = self.store.delete_passkeys_via("site")
        except OSError as error:
            return {"ok": False, "messages": self._failed_write(error)}
        self.on_sessions_removed(removed)
        self._log("info", f"Web sign-ins through the trusted site ended: {len(removed)} sessions, {passkeys} passkeys")
        return {"ok": True, "messages": [f"Ended {len(removed)} web sessions and removed {passkeys} passkeys "
                                         "that came through the trusted site"]}

    def _find_account(self, target: str) -> dict | None:
        if target.startswith("xuid:"):
            return self.store.account_for_xuid(target[len("xuid:"):])
        with self._authority.lock:
            rows = self._authority.report()
        # The current name first: a stored GamerTag may be stale, and someone else may have taken it since.
        row = next((row for row in rows if row["name"].casefold() == target.casefold() and row.get("xuid")), None)
        if row is not None:
            return self.store.account_for_xuid(row["xuid"])
        return self.store.account_for_gamertag(target)

    # -- revocation (P6) --------------------------------------------------------

    def grants_changed(self) -> None:
        """After any grant change: a revoked XUID loses its sessions and codes; the account and passkeys stay.

        Every later request rechecks the grant revision anyway, so this is what closes connections promptly,
        not what makes the revoke effective.
        """
        for identity in self.codes.live():
            with self._authority.lock:
                allowed = self._authority.control_decision(identity.gamertag, identity.xuid, identity.player_id)[0]
            if not allowed:
                self.codes.void([identity.xuid])
        if self.store is None:
            return
        for account in self.store.accounts():
            with self._authority.lock:
                allowed = self._authority.control_decision(account["gamertag"], account["xuid"], account["uuid"])[0]
            if not allowed:
                self.codes.void([account["xuid"]])
                try:
                    removed = self.store.end_sessions_for_revoke(account["xuid"])
                except OSError as error:
                    # The grant revision check still refuses these sessions on every request.
                    self._failed_write(error)
                    continue
                if removed:
                    self._log("info", f"Web sessions of {account['gamertag']} ended: controller grant revoked")
                    self.on_sessions_removed(removed)
