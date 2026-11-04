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
    def __init__(self, section: dict, stores_directory: Path, authority: Authority, *,
                 log: Callable[[str, str], None] | None = None) -> None:
        self._log = log or (lambda level, message: None)
        self._authority = authority
        self.enabled = section.get("enabled", False) is True
        try:
            self.origin = web_origin(section.get("origin"))
        except ValueError as error:
            self.origin = None
            self._log("error", f"Web disabled: {error}")
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
        self.auth = None
        if self.enabled and self.origin is not None and self.store is not None:
            # Imported here: an instance without the web never loads the WebAuthn library.
            try:
                from endbotd.web_auth import WebAuth
            except ImportError as error:
                self._log("error", f"Web disabled: the WebAuthn library is missing ({error}); reinstall Endbot")
            else:
                self.auth = WebAuth(self.store, self.codes, self.origin, authority, log=self._log,
                                    on_sessions_removed=lambda hashes: self.on_sessions_removed(hashes),
                                    on_passkey_added=lambda account: self.on_passkey_added(account))

    @property
    def available(self) -> bool:
        return self.auth is not None

    # -- /botweb --------------------------------------------------------------

    def command(self, record: dict, language: str, argv: list[str]) -> list[str]:
        """``/botweb`` from an allowed player; ``record`` is their presence record, not the plugin's word."""
        action = argv[0].lower() if argv else "status"
        xuid = record.get("xuid") or ""
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
            self._remove_account(xuid)
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
        self._remove_account(account["xuid"])
        self._log("info", f"Web account of {account['gamertag']} revoked")
        message = (f"Web account of {account['gamertag']} removed; its passkeys and sessions no longer work. "
                   "The controller grant is unchanged.")
        return {"ok": True, "messages": [message]}

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
                removed = self.store.delete_sessions(account["xuid"])
                if removed:
                    self._log("info", f"Web sessions of {account['gamertag']} ended: controller grant revoked")
                    self.on_sessions_removed(removed)
