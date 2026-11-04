"""Web accounts, passkeys and sessions in ``state/web/``, and in-memory link codes (docs/WEB.md P3, P4, P7).

endbotd is the only reader and writer. A session token is never stored, only its
SHA-256. A change is made in memory first, so a failed write cannot leave a
revoked session usable; the error reaches the caller. Link codes live in memory
only: an endbotd restart voids them, which costs a retry.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import tempfile
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

VERSION = 1
ACCOUNTS, PASSKEYS, SESSIONS = "accounts", "passkeys", "sessions"

# Crockford's base32: 32 symbols, read aloud or typed from a phone without confusing 0/O or 1/I/L.
CODE_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
CODE_SYMBOLS = 6
CODE_SECONDS = 300.0
WRONG_CODE_LIMIT = 10
WRONG_CODE_WINDOW = 600.0
_CODE_READING = str.maketrans({"O": "0", "I": "1", "L": "1"})


class WebStoreError(ValueError):
    """A store file is unreadable or invalid; the web stays off, ``/bot`` keeps working."""


def _load(path: Path, key: str) -> list[dict]:
    try:
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise WebStoreError(f"{path.name} must be a regular file")
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, UnicodeDecodeError, ValueError) as error:
        raise WebStoreError(f"{path.name} cannot be read: {error}") from error
    if not isinstance(document, dict) or document.get("version") != VERSION:
        raise WebStoreError(f"{path.name} has an unsupported version")
    entries = document.get(key)
    if not isinstance(entries, list) or not all(isinstance(entry, dict) for entry in entries):
        raise WebStoreError(f"{path.name} must hold a list of {key}")
    return entries


def _save(path: Path, key: str, entries: list[dict]) -> None:
    if path.is_symlink():
        raise WebStoreError(f"{path.name} must not be a symlink")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.stem}-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump({"version": VERSION, key: entries}, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


class WebStore:
    """One account per XUID, with its passkeys and sessions; ``directory`` is ``state/web``."""

    def __init__(self, directory: Path, *, clock: Callable[[], float] = time.time) -> None:
        self.directory = Path(directory)
        self._clock = clock
        self._lock = threading.RLock()
        self._accounts = _load(self._path(ACCOUNTS), ACCOUNTS)
        self._passkeys = _load(self._path(PASSKEYS), PASSKEYS)
        self._sessions = _load(self._path(SESSIONS), SESSIONS)
        for account in self._accounts:
            if not all(isinstance(account.get(field), str) and account[field]
                       for field in ("id", "xuid", "uuid", "gamertag", "userHandle")):
                raise WebStoreError("accounts.json has an incomplete account")
        if len({account["xuid"] for account in self._accounts}) != len(self._accounts):
            raise WebStoreError("accounts.json has two accounts for one XUID")
        for entry in self._passkeys:
            if not (all(isinstance(entry.get(field), str) and entry[field]
                        for field in ("id", "account", "publicKey", "rpId"))
                    and type(entry.get("signCount")) is int and entry["signCount"] >= 0):
                raise WebStoreError("passkeys.json has an incomplete passkey")
        for entry in self._sessions:
            if not (all(isinstance(entry.get(field), str) and entry[field]
                        for field in ("id", "hash", "account", "via"))
                    and all(isinstance(entry.get(field), (int, float)) and not isinstance(entry[field], bool)
                            for field in ("created", "lastSeen", "assertedAt"))
                    and type(entry.get("grantRevision")) is int):
                raise WebStoreError("sessions.json has an incomplete session")

    def _path(self, key: str) -> Path:
        return self.directory / f"{key}.json"

    # -- accounts -------------------------------------------------------------

    def accounts(self) -> list[dict]:
        with self._lock:
            return [dict(account) for account in self._accounts]

    def account(self, account_id: str) -> dict | None:
        with self._lock:
            account = next((entry for entry in self._accounts if entry["id"] == account_id), None)
            return dict(account) if account is not None else None

    def account_for_xuid(self, xuid: str) -> dict | None:
        with self._lock:
            account = next((entry for entry in self._accounts if entry["xuid"] == xuid), None)
            return dict(account) if account is not None else None

    def account_for_gamertag(self, gamertag: str) -> dict | None:
        matched = gamertag.casefold()
        with self._lock:
            account = next((entry for entry in self._accounts if entry["gamertag"].casefold() == matched), None)
            return dict(account) if account is not None else None

    def ensure_account(self, xuid: str, player_id: str, gamertag: str) -> dict:
        """The XUID's account, created if missing; the GamerTag and player id follow the latest proof."""
        with self._lock:
            account = next((entry for entry in self._accounts if entry["xuid"] == xuid), None)
            if account is None:
                account = {
                    "id": secrets.token_urlsafe(16),
                    "xuid": xuid,
                    "uuid": player_id,
                    "gamertag": gamertag,
                    # Random, never the XUID: a synced passkey provider does not learn who this is (P3).
                    "userHandle": base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode("ascii"),
                    "created": self._clock(),
                }
                self._accounts.append(account)
            elif account["uuid"] == player_id and account["gamertag"] == gamertag:
                return dict(account)
            else:
                account.update(uuid=player_id, gamertag=gamertag)
            _save(self._path(ACCOUNTS), ACCOUNTS, self._accounts)
            return dict(account)

    def delete_account(self, xuid: str) -> list[str]:
        """Remove the account with its passkeys and sessions; return the removed session hashes."""
        with self._lock:
            account = next((entry for entry in self._accounts if entry["xuid"] == xuid), None)
            if account is None:
                return []
            removed = self.delete_sessions(xuid)
            self._passkeys = [entry for entry in self._passkeys if entry.get("account") != account["id"]]
            self._accounts = [entry for entry in self._accounts if entry is not account]
            _save(self._path(PASSKEYS), PASSKEYS, self._passkeys)
            _save(self._path(ACCOUNTS), ACCOUNTS, self._accounts)
            return removed

    # -- passkeys and sessions ------------------------------------------------

    def passkeys(self, account_id: str) -> list[dict]:
        with self._lock:
            return [dict(entry) for entry in self._passkeys if entry.get("account") == account_id]

    def sessions(self, account_id: str) -> list[dict]:
        with self._lock:
            return [dict(entry) for entry in self._sessions if entry.get("account") == account_id]

    def find_passkey(self, credential_id: str) -> dict | None:
        with self._lock:
            entry = next((entry for entry in self._passkeys if entry["id"] == credential_id), None)
            return dict(entry) if entry is not None else None

    def add_passkey(self, passkey: dict) -> None:
        with self._lock:
            self._passkeys.append(passkey)
            _save(self._path(PASSKEYS), PASSKEYS, self._passkeys)

    def update_passkey(self, credential_id: str, **fields: object) -> None:
        with self._lock:
            for entry in self._passkeys:
                if entry["id"] == credential_id:
                    entry.update(fields)
            _save(self._path(PASSKEYS), PASSKEYS, self._passkeys)

    def delete_passkey(self, credential_id: str) -> None:
        with self._lock:
            self._passkeys = [entry for entry in self._passkeys if entry["id"] != credential_id]
            _save(self._path(PASSKEYS), PASSKEYS, self._passkeys)

    def find_session(self, token_hash: str) -> dict | None:
        with self._lock:
            entry = next((entry for entry in self._sessions if entry["hash"] == token_hash), None)
            return dict(entry) if entry is not None else None

    def add_session(self, session: dict) -> None:
        with self._lock:
            self._sessions.append(session)
            _save(self._path(SESSIONS), SESSIONS, self._sessions)

    def update_session(self, token_hash: str, **fields: object) -> None:
        with self._lock:
            for entry in self._sessions:
                if entry["hash"] == token_hash:
                    entry.update(fields)
            _save(self._path(SESSIONS), SESSIONS, self._sessions)

    def delete_session(self, token_hash: str) -> None:
        with self._lock:
            self._sessions = [entry for entry in self._sessions if entry["hash"] != token_hash]
            _save(self._path(SESSIONS), SESSIONS, self._sessions)

    def delete_sessions_via(self, via: str) -> list[str]:
        """End every session issued a certain way (P12); return the removed hashes."""
        with self._lock:
            removed = [entry["hash"] for entry in self._sessions if entry["via"] == via]
            if removed:
                self._sessions = [entry for entry in self._sessions if entry["via"] != via]
                _save(self._path(SESSIONS), SESSIONS, self._sessions)
            return removed

    def delete_sessions(self, xuid: str) -> list[str]:
        """End every session of the XUID's account; the account and its passkeys stay (P6)."""
        with self._lock:
            account = next((entry for entry in self._accounts if entry["xuid"] == xuid), None)
            if account is None:
                return []
            removed = [entry.get("hash", "") for entry in self._sessions if entry.get("account") == account["id"]]
            if removed:
                self._sessions = [entry for entry in self._sessions if entry.get("account") != account["id"]]
                _save(self._path(SESSIONS), SESSIONS, self._sessions)
            return removed


@dataclass(frozen=True, slots=True)
class LinkIdentity:
    """Who a code proves: an Xbox-authenticated player with a controller grant, seen in game."""

    xuid: str
    player_id: str
    gamertag: str


class LinkCodeEntryClosed(ValueError):
    """Too many wrong codes server-wide; code entry stays closed for a while (P7)."""


class LinkCodes:
    """Single-use codes, at most one live per XUID, and the server-wide wrong-code limit."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic,
                 on_closed: Callable[[], None] | None = None) -> None:
        self._clock = clock
        self._on_closed = on_closed or (lambda: None)
        self._lock = threading.Lock()
        self._codes: dict[str, tuple[LinkIdentity, float]] = {}
        self._wrong: list[float] = []
        self._closed_until = 0.0

    def _purge(self, now: float) -> None:
        self._codes = {code: entry for code, entry in self._codes.items() if entry[1] > now}
        self._wrong = [moment for moment in self._wrong if moment > now - WRONG_CODE_WINDOW]

    def issue(self, identity: LinkIdentity) -> str:
        """A new code for this XUID, replacing its earlier one; shown as ``K7Q-4M2``."""
        with self._lock:
            now = self._clock()
            self._purge(now)
            self._codes = {code: entry for code, entry in self._codes.items() if entry[0].xuid != identity.xuid}
            while True:
                code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_SYMBOLS))
                if code not in self._codes:
                    break
            self._codes[code] = (identity, now + CODE_SECONDS)
        return f"{code[:3]}-{code[3:]}"

    def redeem(self, text: str) -> LinkIdentity | None:
        """Consume a code; None for a wrong or expired one. Raises while entry is closed."""
        closed = False
        with self._lock:
            now = self._clock()
            if now < self._closed_until:
                raise LinkCodeEntryClosed("too many wrong link codes")
            self._purge(now)
            code = text.strip().upper().replace("-", "").replace(" ", "").translate(_CODE_READING)
            entry = self._codes.pop(code, None)
            if entry is not None:
                return entry[0]
            self._wrong.append(now)
            if len(self._wrong) >= WRONG_CODE_LIMIT:
                self._closed_until = now + WRONG_CODE_WINDOW
                self._wrong = []
                closed = True
        if closed:
            self._on_closed()
        return None

    def live(self) -> list[LinkIdentity]:
        """Who holds a live code; a grant change checks them, with or without an account."""
        with self._lock:
            self._purge(self._clock())
            return [entry[0] for entry in self._codes.values()]

    def void(self, xuids: Iterable[str]) -> None:
        voided = set(xuids)
        with self._lock:
            self._codes = {code: entry for code, entry in self._codes.items() if entry[0].xuid not in voided}
