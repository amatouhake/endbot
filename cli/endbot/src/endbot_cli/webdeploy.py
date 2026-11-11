"""`endbot web deploy` and `rotate-key`: the prebuilt Worker through the Cloudflare API (docs/WEB.md P1, P4, P8, P9).

No Node, npm or Wrangler. The API token comes from a hidden prompt or
``CLOUDFLARE_API_TOKEN``, never from argv, is never written to disk and is never
logged: every API error passes through ``_scrub`` first. Nothing secret leaves
the PC: ``ENDBOTD_LINK_KEY`` holds the public half of the link key.
"""

from __future__ import annotations

import base64
import getpass
import hashlib
import json
import mimetypes
import os
import re
import secrets
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
    load_pem_private_key,
)

from endbot_cli.config import EndbotConfig, load_config
from endbot_cli.configgen import generate_endbotd_config
from endbot_cli.fsutil import atomic_write_text
from endbot_cli.instance import InstancePaths
from endbot_cli.processes import _app_dir
from endbot_cli.runstate import RUNNING, inspect_supervisor

API = "https://api.cloudflare.com/client/v4"
KEY_FILE = "web-link.key"
MIGRATION_TAG = "v1"
LINK_WAIT_SECONDS = 30.0
# A rotated key reached the Durable Object about 2 minutes after the API accepted it in step 10; endbotd retries every
# 10 s meanwhile, for as long as this waits.
ROTATE_WAIT_SECONDS = 180.0
TOKEN_VARIABLE = "CLOUDFLARE_API_TOKEN"
_HOSTNAME = re.compile(r"(?=.{1,253}$)[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+")


class DeployError(ValueError):
    """One line for the operator; it never carries the token."""


def read_token(environ: Mapping[str, str], prompt: Callable[[str], str] = getpass.getpass) -> str:
    try:
        token = environ.get(TOKEN_VARIABLE) or prompt("Cloudflare API token (input hidden): ")
    except EOFError:  # no terminal to ask on
        token = ""
    token = token.strip()
    if not token:
        raise DeployError(f"no Cloudflare API token: enter it at the prompt or set {TOKEN_VARIABLE}")
    return token


class CloudflareApi:
    def __init__(self, token: str, account_id: str, opener: Callable = urllib.request.urlopen) -> None:
        self._token = token
        self._account = account_id
        self._open = opener

    def _scrub(self, text: str) -> str:
        return text.replace(self._token, "[token]")

    def _call(self, method: str, url: str, *, body: bytes | None = None, content_type: str | None = None,
              bearer: str | None = None) -> dict:
        result = self._request(method, url, body=body, content_type=content_type, bearer=bearer)
        return result if isinstance(result, dict) else {}

    def _request(self, method: str, url: str, *, body: bytes | None = None, content_type: str | None = None,
                 bearer: str | None = None) -> object:
        request = urllib.request.Request(url, data=body, method=method)
        request.add_header("Authorization", f"Bearer {bearer or self._token}")
        if content_type:
            request.add_header("Content-Type", content_type)
        # Which call failed tells the operator which permission is missing (step 10); the account id is left out.
        call = f"{method} {url.removeprefix(API).replace(f'/accounts/{self._account}', '', 1).split('?', 1)[0]}"
        try:
            with self._open(request, timeout=60) as response:
                data = response.read()
        except urllib.error.HTTPError as error:
            data = error.read()
            raise DeployError(self._scrub(f"Cloudflare API {call} failed ({error.code}): "
                                          f"{self._errors(data)}")) from None
        except OSError as error:
            raise DeployError(self._scrub(f"Cloudflare API {call} failed: {type(error).__name__}")) from None
        try:
            answer = json.loads(data)
        except ValueError:
            raise DeployError(f"Cloudflare API {call} answered something that is not JSON") from None
        if not isinstance(answer, dict) or answer.get("success") is False:
            raise DeployError(self._scrub(f"Cloudflare API {call} refused: {self._errors(data)}"))
        return answer.get("result")

    @staticmethod
    def _errors(data: bytes) -> str:
        try:
            errors = json.loads(data).get("errors") or []
            return "; ".join(str(error.get("message", error)) for error in errors) or "no detail"
        except (ValueError, AttributeError, TypeError):
            return "no detail"

    def _account_url(self, path: str) -> str:
        return f"{API}/accounts/{self._account}{path}"

    def _json(self, method: str, path: str, value: object) -> dict:
        return self._call(method, self._account_url(path), body=json.dumps(value).encode("utf-8"),
                          content_type="application/json")

    def workers_subdomain(self) -> str:
        try:
            subdomain = self._call("GET", self._account_url("/workers/subdomain")).get("subdomain")
        except DeployError as error:
            # A token scoped to chosen Workers cannot read this account-wide setting on its own (step 10).
            if "(403)" in str(error):
                raise DeployError(f"{error}. The token cannot read the account's workers.dev subdomain: add an "
                                  "account-wide Workers read permission (docs/WEB_DEPLOY.md), or use --domain"
                                  ) from None
            raise
        if not isinstance(subdomain, str) or not subdomain:
            raise DeployError("this account has no workers.dev subdomain; open Workers once in the Cloudflare "
                              "dashboard, or use --domain")
        return subdomain

    def find_worker(self, worker: str) -> dict | None:
        """That Worker's entry in the account's list, or None. A Worker with no Durable Objects has no migration tag.

        An answer that cannot be read stops the deploy: it must never pass for "no such Worker", which would let the
        deploy replace one.
        """
        scripts = self._request("GET", self._account_url("/workers/scripts"))
        if not isinstance(scripts, list):
            raise DeployError("Cloudflare did not return the account's list of Workers")
        return next((script for script in scripts if isinstance(script, dict) and script.get("id") == worker), None)

    def upload_assets(self, worker: str, files: dict[str, bytes]) -> str:
        """The three-step direct upload; returns the completion token for the script's metadata."""
        manifest = {path: {"hash": asset_hash(path, content), "size": len(content)} for path, content in files.items()}
        by_hash = {manifest[path]["hash"]: path for path in files}
        session = self._json("POST", f"/workers/scripts/{worker}/assets-upload-session", {"manifest": manifest})
        token = session.get("jwt")
        for bucket in session.get("buckets") or []:
            parts = []
            for file_hash in bucket:
                path = by_hash[file_hash]
                kind = mimetypes.guess_type(path)[0] or "application/octet-stream"
                parts.append((file_hash, file_hash, kind, base64.b64encode(files[path])))
            body, content_type = _multipart(parts)
            answer = self._call("POST", self._account_url("/workers/assets/upload?base64=true"), body=body,
                                content_type=content_type, bearer=session.get("jwt"))
            token = answer.get("jwt") or token
        if not isinstance(token, str) or not token:
            raise DeployError("Cloudflare did not return an assets completion token")
        return token

    def put_script(self, worker: str, metadata: dict, module: bytes) -> None:
        body, content_type = _multipart([
            ("metadata", None, "application/json", json.dumps(metadata).encode("utf-8")),
            (metadata["main_module"], metadata["main_module"], "application/javascript+module", module),
        ])
        self._call("PUT", self._account_url(f"/workers/scripts/{worker}"), body=body, content_type=content_type)

    def put_secret(self, worker: str, name: str, value: str) -> None:
        self._json("PUT", f"/workers/scripts/{worker}/secrets", {"name": name, "text": value, "type": "secret_text"})

    def set_subdomain(self, worker: str, *, enabled: bool) -> None:
        # Previews would serve the UI from a second origin (P8).
        self._json("POST", f"/workers/scripts/{worker}/subdomain", {"enabled": enabled, "previews_enabled": False})

    def attach_domain(self, worker: str, hostname: str, zone_id: str) -> None:
        self._json("PUT", "/workers/domains", {"hostname": hostname, "service": worker, "zone_id": zone_id,
                                               "environment": "production"})


def asset_hash(path: str, content: bytes) -> str:
    """As Cloudflare's direct-upload guide computes it: 32 hex characters over the base64 and the extension."""
    extension = Path(path).suffix.lstrip(".")
    return hashlib.sha256(base64.b64encode(content) + extension.encode("utf-8")).hexdigest()[:32]


def _multipart(parts: list[tuple[str, str | None, str, bytes]]) -> tuple[bytes, str]:
    boundary = f"endbot{secrets.token_hex(16)}"
    chunks = []
    for name, filename, kind, content in parts:
        disposition = f'form-data; name="{name}"' + (f'; filename="{filename}"' if filename else "")
        chunks.append(f"--{boundary}\r\nContent-Disposition: {disposition}\r\nContent-Type: {kind}\r\n\r\n".encode())
        chunks.extend((content, b"\r\n"))
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


# -- the link key --------------------------------------------------------------


def _write_key(path: Path, key: Ed25519PrivateKey) -> None:
    if path.is_symlink():
        raise DeployError(f"{path} must not be a symlink")
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()).decode("ascii"))
    if os.name != "nt":
        os.chmod(path, 0o600)


def ensure_link_key(paths: InstancePaths) -> Ed25519PrivateKey:
    """The instance's link key, created once (P1)."""
    path = paths.state_secrets / KEY_FILE
    if path.is_symlink():
        raise DeployError(f"{path} must not be a symlink")
    try:
        key = load_pem_private_key(path.read_bytes(), password=None)
    except FileNotFoundError:
        key = Ed25519PrivateKey.generate()
        _write_key(path, key)
        return key
    except (OSError, ValueError, TypeError) as error:
        raise DeployError(f"{path} cannot be read ({type(error).__name__}); use `endbot web rotate-key`") from None
    if not isinstance(key, Ed25519PrivateKey):
        raise DeployError(f"{path} is not an Ed25519 key; use `endbot web rotate-key`")
    return key


def link_key_secret(key: Ed25519PrivateKey) -> str:
    """``ENDBOTD_LINK_KEY``: the public key and its id, the same id endbotd proves with."""
    raw = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return json.dumps({"key": base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii"),
                       "keyId": hashlib.sha256(raw).hexdigest()[:16]})


# -- the bundle ------------------------------------------------------------------


def locate_bundle(paths: InstancePaths, override: Path | None) -> Path:
    if override is not None:
        candidates = [override]
    else:
        app_dir = _app_dir(paths)
        candidates = [app_dir / "web"] if app_dir is not None else []
    for candidate in candidates:
        if (candidate / "manifest.json").is_file() and (candidate / "worker" / "index.js").is_file():
            return candidate
    raise DeployError("the prebuilt Worker was not found in this install; reinstall Endbot, or pass --bundle")


def read_assets(bundle: Path) -> dict[str, bytes]:
    root = bundle / "assets"
    files = {f"/{path.relative_to(root).as_posix()}": path.read_bytes()
             for path in sorted(root.rglob("*")) if path.is_file()} if root.is_dir() else {}
    if not files:
        raise DeployError(f"{root} holds no files; the bundle is incomplete")
    return files


def worker_metadata(manifest: dict, origin: str, secret: str, assets_token: str, applied: str | None) -> dict:
    metadata = {
        "main_module": "index.js",
        "compatibility_date": manifest["compatibilityDate"],
        "bindings": [
            {"type": "durable_object_namespace", "name": "RELAY", "class_name": "Relay"},
            {"type": "assets", "name": "ASSETS"},
            {"type": "plain_text", "name": "ENDBOT_ORIGIN", "text": origin},
            {"type": "secret_text", "name": "ENDBOTD_LINK_KEY", "text": secret},
        ],
        "assets": {"jwt": assets_token,
                   "config": {"not_found_handling": "single-page-application", "run_worker_first": True}},
        # Nothing of the relayed traffic stays with Cloudflare (P8).
        "observability": {"enabled": False},
        "logpush": False,
    }
    if applied != MIGRATION_TAG:
        metadata["migrations"] = {"new_tag": MIGRATION_TAG, "new_sqlite_classes": ["Relay"]}
    return metadata


# -- endbot.toml -----------------------------------------------------------------


def _write_web_settings(paths: InstancePaths, **values: str | bool) -> None:
    import tomlkit

    document = tomlkit.parse(paths.endbot_toml.read_text(encoding="utf-8"))
    if "web" not in document:
        document.add("web", tomlkit.table())
    for key, value in values.items():
        document["web"][key] = value
    atomic_write_text(paths.endbot_toml, tomlkit.dumps(document))


def _regenerate(paths: InstancePaths) -> None:
    config = load_config(paths.endbot_toml)
    generate_endbotd_config(config.server.resolve(paths.root), paths, config)


def _clear_credentials(paths: InstancePaths, report: Callable[[str], None], request: Callable) -> None:
    """A passkey is bound to its RP ID: a new domain makes them all useless (P4). Accounts stay."""
    if inspect_supervisor(paths.state_run).status == RUNNING:
        result = request("domain-change")
        if not result["ok"]:
            raise DeployError(result["messages"][0])
    else:
        # Only with endbotd stopped may another process write its store.
        for key in ("passkeys", "sessions"):
            path = paths.root / "state" / "web" / f"{key}.json"
            if path.exists():
                atomic_write_text(path, json.dumps({"version": 1, key: []}) + "\n")
    report("Deleted every passkey and session; accounts stay. Each player runs /botweb link once on the new domain.")


def _announce_link(paths: InstancePaths, request: Callable, report: Callable[[str], None],
                   clock: Callable[[], float], sleep: Callable[[float], None], wait: float) -> bool:
    """Step 5 of P1: wait until endbotd reports the link up, or say why not."""
    if inspect_supervisor(paths.state_run).status != RUNNING:
        report("Endbot is not running; the link comes up at the next `endbot start`.")
        return True
    reloaded = request("reload")
    if not reloaded["ok"]:
        report(f"endbotd could not load the new settings: {reloaded['messages'][0]}")
        return False
    deadline = clock() + wait
    last = reloaded["messages"][0]
    while clock() < deadline:
        status = request("status")
        last = status["messages"][0]
        if status.get("linked"):
            report(last)
            return True
        sleep(1.0)
    report(f"The link is not up yet: {last}")
    if "ProofRefused" in last:
        # The new key just has not reached Cloudflare's Durable Object; endbotd keeps trying (step 10, rc.3).
        report("Cloudflare can take several minutes to start using a new key. endbotd keeps trying every 10 s for "
               "10 minutes, so the link should come up by itself; `endbot doctor --live` shows when it does")
    return False


# -- the commands ----------------------------------------------------------------


def _route(api: CloudflareApi, worker: str, domain: str | None, zone_id: str | None) -> None:
    """One origin only (P8): a custom domain also turns the workers.dev address and previews off."""
    if domain:
        api.attach_domain(worker, domain, zone_id)
        api.set_subdomain(worker, enabled=False)
    else:
        api.set_subdomain(worker, enabled=True)


def _keeps_custom_domain(config: EndbotConfig, account: str, worker: str, domain: str | None, confirmed: bool) -> bool:
    """A plain redeploy, which `endbot update` also runs, leaves an attached custom domain as it is.

    The zone id is not kept, and nothing about the route changes. Asking for workers.dev again takes
    --confirm-domain-change, as any new origin does (P4).
    """
    web = config.web
    return bool(web.origin) and not web.origin.endswith(".workers.dev") and not domain and not confirmed \
        and account == web.account_id and worker == web.worker_name


def deploy(paths: InstancePaths, *, token: str, account_id: str | None, worker_name: str | None, domain: str | None,
           zone_id: str | None, confirm_domain_change: bool, bundle: Path | None, report: Callable[[str], None],
           request: Callable, replace_worker: bool = False, opener: Callable = urllib.request.urlopen,
           clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
           wait: float = LINK_WAIT_SECONDS) -> bool:
    config = load_config(paths.endbot_toml)
    account = account_id or config.web.account_id
    worker = worker_name or config.web.worker_name or "endbot"
    if not account:
        raise DeployError("--account-id is required the first time (it is not secret and is kept in endbot.toml)")
    if domain and not zone_id:
        raise DeployError("--domain also needs --zone-id")
    if domain and not _HOSTNAME.fullmatch(domain):
        # WebAuthn compares this exactly with what a browser reports: lowercase, no port, punycode for an IDN.
        raise DeployError(f"--domain must be a lowercase host name with no port or path, found {domain!r}")
    source = locate_bundle(paths, bundle)
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    files = read_assets(source)
    api = CloudflareApi(token, account, opener)
    keep_route = _keeps_custom_domain(config, account, worker, domain, confirm_domain_change)
    if keep_route:
        origin = config.web.origin
    else:
        origin = f"https://{domain}" if domain else f"https://{worker}.{api.workers_subdomain()}.workers.dev"
    previous = config.web.origin
    changed = bool(previous) and previous != origin
    if changed and not confirm_domain_change:
        raise DeployError(f"the origin would change from {previous} to {origin}, which makes every registered passkey "
                          "unusable, so each player must run /botweb link again. Repeat with --confirm-domain-change "
                          "to do that.")
    # Uploading replaces a Worker's code and bindings outright, and an account usually holds other Workers (P9).
    existing = api.find_worker(worker)
    ours = account == config.web.account_id and worker == config.web.worker_name
    if existing is not None and not ours and not replace_worker:
        raise DeployError(f"a Worker named {worker} already exists in this account and this instance did not deploy "
                          "it. Deploying would replace its code and settings. Choose another name with --worker-name, "
                          "or repeat with --replace-worker if it is yours to replace.")
    if keep_route and existing is None:
        raise DeployError(f"the Worker {worker} no longer exists on Cloudflare, so {origin} has to be attached "
                          f"again: repeat with --domain {origin.removeprefix('https://')} --zone-id <id>")
    key = ensure_link_key(paths)
    secret = link_key_secret(key)
    # The settings first, because they are what can fail here without touching anything remote; undone below if
    # Cloudflare refuses. The script is the last thing to change for a Worker already serving: until it does, the old
    # origin keeps working, so a refused route or domain leaves the site as it was.
    before = paths.endbot_toml.read_text(encoding="utf-8")
    try:
        _write_web_settings(paths, enabled=True, origin=origin, **{"account-id": account, "worker-name": worker})
        _regenerate(paths)
        report(f"Uploading the Worker {worker} ({len(files)} assets) for {origin}")
        assets_token = api.upload_assets(worker, files)
        if existing is not None and not keep_route:
            _route(api, worker, domain, zone_id)
        applied = existing.get("migration_tag") if existing is not None else None
        api.put_script(worker, worker_metadata(manifest, origin, secret, assets_token, applied),
                       (source / "worker" / "index.js").read_bytes())
        if existing is None:
            _route(api, worker, domain, zone_id)
    except (OSError, ValueError):
        atomic_write_text(paths.endbot_toml, before)
        _regenerate(paths)
        raise
    if changed:
        _clear_credentials(paths, report, request)
    report(f"Deployed {origin}")
    return _announce_link(paths, request, report, clock, sleep, wait)


def rotate_key(paths: InstancePaths, *, token: str, report: Callable[[str], None], request: Callable,
               opener: Callable = urllib.request.urlopen, clock: Callable[[], float] = time.monotonic,
               sleep: Callable[[float], None] = time.sleep, wait: float = ROTATE_WAIT_SECONDS) -> bool:
    """One mechanism for routine rotation and suspected exposure (P1)."""
    config = load_config(paths.endbot_toml)
    if not config.web.account_id or not config.web.worker_name:
        raise DeployError("nothing is deployed yet; run `endbot web deploy` first")
    path = paths.state_secrets / KEY_FILE
    new_key = Ed25519PrivateKey.generate()
    pending = path.with_name(f"{KEY_FILE}.new")
    # On disk before the secret changes: a crash in between must not lose the key the Worker now expects.
    _write_key(pending, new_key)
    try:
        CloudflareApi(token, config.web.account_id, opener).put_secret(
            config.web.worker_name, "ENDBOTD_LINK_KEY", link_key_secret(new_key))
    except BaseException:
        # Nothing reads a leftover key, so it must not linger. Should Cloudflare have taken the secret all the same,
        # running rotate-key again puts a key on both sides.
        pending.unlink(missing_ok=True)
        raise
    # The Worker no longer accepts the old key, so nothing needs it: on a suspected exposure it must not stay on disk.
    os.replace(pending, path)
    path.with_name(f"{KEY_FILE}.old").unlink(missing_ok=True)  # left by earlier versions
    report("The Worker now expects the new key; Cloudflare can take a minute to start using it")
    return _announce_link(paths, request, report, clock, sleep, wait)


__all__ = ["DeployError", "deploy", "read_token", "rotate_key"]
