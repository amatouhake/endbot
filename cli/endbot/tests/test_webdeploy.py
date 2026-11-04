"""`endbot web deploy` and `rotate-key` against a fake Cloudflare API (docs/WEB.md P1, P4, P8, P9)."""

from __future__ import annotations

import base64
import contextlib
import hashlib
import io
import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fakeinstance import build_instance

from endbot_cli.__main__ import main
from endbot_cli.commands import run_web_deploy
from endbot_cli.config import load_config
from endbot_cli.instance import InstancePaths
from endbot_cli.lock import load_lock
from endbot_cli.webdeploy import (
    DeployError,
    asset_hash,
    deploy,
    ensure_link_key,
    link_key_secret,
    read_token,
    rotate_key,
)

TOKEN = "cf-secret-token-value"
ACCOUNT = "acc123"


class FakeResponse:
    def __init__(self, data: bytes) -> None:
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self) -> bytes:
        return self.data


class FakeApi:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, object, str]] = []
        self.migration = None
        self.buckets = "all"
        self.fail: dict[str, tuple[int, str]] = {}

    def __call__(self, request, timeout=None):
        url = request.full_url
        path = url.split(f"/accounts/{ACCOUNT}", 1)[1]
        auth = request.get_header("Authorization")
        self.calls.append((request.get_method(), path, request.data, auth))
        for fragment, (code, message) in self.fail.items():
            if fragment in path:
                body = json.dumps({"success": False, "errors": [{"message": message}]}).encode()
                raise urllib.error.HTTPError(url, code, "no", {}, io.BytesIO(body))
        return FakeResponse(json.dumps({"success": True, "result": self._result(request, path)}).encode())

    def _result(self, request, path: str) -> dict:
        if path == "/workers/subdomain":
            return {"subdomain": "alice"}
        if path.endswith("/settings"):
            if self.migration is None:
                raise urllib.error.HTTPError(path, 404, "no", {}, io.BytesIO(b'{"errors":[{"message":"none"}]}'))
            return {"migration_tag": self.migration}
        if path.endswith("/assets-upload-session"):
            manifest = json.loads(request.data)["manifest"]
            self.manifest = manifest
            hashes = [entry["hash"] for entry in manifest.values()]
            return {"jwt": "upload-jwt", "buckets": [hashes] if self.buckets == "all" else []}
        if path.startswith("/workers/assets/upload"):
            return {"jwt": "completion-jwt"}
        return {}

    def paths(self) -> list[str]:
        return [call[1] for call in self.calls]

    def call(self, fragment: str):
        return next(call for call in self.calls if fragment in call[1])


class DeployCase(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.paths: InstancePaths = build_instance(self.root, load_lock())
        self.server_dir = self.root / "server"
        self.bundle = self.root / "web-bundle"
        (self.bundle / "worker").mkdir(parents=True)
        (self.bundle / "assets" / "sub").mkdir(parents=True)
        (self.bundle / "manifest.json").write_text(json.dumps({"compatibilityDate": "2026-08-01", "protocol": 1}))
        (self.bundle / "worker" / "index.js").write_text("export default {};")
        (self.bundle / "assets" / "index.html").write_text("<html></html>")
        (self.bundle / "assets" / "sub" / "a.js").write_text("console.log(1);")
        self.api = FakeApi()
        self.reports: list[str] = []
        self.requests: list[str] = []
        self.linked = True

    def request(self, operation: str) -> dict:
        self.requests.append(operation)
        if operation == "domain-change":
            return {"ok": True, "messages": ["deleted"]}
        return {"ok": True, "linked": self.linked, "messages": [f"link {'up' if self.linked else 'down: why'}"]}

    def deploy(self, **options) -> bool:
        values = {"token": TOKEN, "account_id": ACCOUNT, "worker_name": None, "domain": None, "zone_id": None,
                  "confirm_domain_change": False, "bundle": self.bundle, "report": self.reports.append,
                  "request": self.request, "opener": self.api, "sleep": lambda _: None, "wait": 0.01}
        values.update(options)
        return deploy(self.paths, **values)

    def running(self) -> None:
        (self.paths.state_run).mkdir(parents=True, exist_ok=True)
        (self.paths.state_run / "supervisor.pid").write_text(str(os.getpid()), encoding="utf-8")

    def metadata(self) -> dict:
        put = next(call for call in self.api.calls if call[0] == "PUT" and call[1] == "/workers/scripts/endbot")
        body = put[2]
        start = body.index(b"\r\n\r\n", body.index(b'name="metadata"')) + 4
        return json.loads(body[start:body.index(b"\r\n--endbot", start)])


class DeployTests(DeployCase):
    def test_a_first_deploy_uploads_and_records_the_settings(self) -> None:
        self.assertTrue(self.deploy())
        web = load_config(self.paths.endbot_toml).web
        self.assertEqual((web.enabled, web.origin, web.account_id, web.worker_name),
                         (True, "https://endbot.alice.workers.dev", ACCOUNT, "endbot"))
        generated = (self.paths.state_generated / "endbotd.toml").read_text(encoding="utf-8")
        self.assertIn("https://endbot.alice.workers.dev", generated)
        self.assertIn("web-link.key", generated)
        self.assertIn("/workers/scripts/endbot/subdomain", self.api.paths())

    def test_the_settings_keep_the_operators_comments(self) -> None:
        text = self.paths.endbot_toml.read_text(encoding="utf-8")
        self.paths.endbot_toml.write_text("# my note\n" + text, encoding="utf-8")
        self.deploy()
        self.assertIn("# my note", self.paths.endbot_toml.read_text(encoding="utf-8"))

    def test_assets_follow_the_direct_upload_flow(self) -> None:
        self.deploy()
        self.assertEqual(set(self.api.manifest), {"/index.html", "/sub/a.js"})
        content = (self.bundle / "assets" / "index.html").read_bytes()
        self.assertEqual(self.api.manifest["/index.html"],
                         {"hash": asset_hash("/index.html", content), "size": len(content)})
        upload = self.api.call("/workers/assets/upload?base64=true")
        self.assertEqual(upload[3], "Bearer upload-jwt")
        self.assertIn(base64.b64encode(content), upload[2])
        self.assertEqual(self.metadata()["assets"]["jwt"], "completion-jwt")

    def test_an_asset_hash_is_as_the_guide_computes_it(self) -> None:
        expected = hashlib.sha256(base64.b64encode(b"hi") + b"html").hexdigest()[:32]
        self.assertEqual(asset_hash("/a.html", b"hi"), expected)
        self.assertEqual(len(expected), 32)

    def test_with_nothing_to_upload_the_session_token_completes(self) -> None:
        self.api.buckets = "none"
        self.deploy()
        self.assertEqual(self.metadata()["assets"]["jwt"], "upload-jwt")
        self.assertFalse(any("assets/upload" in path for path in self.api.paths()))

    def test_the_worker_keeps_nothing_it_should_not(self) -> None:
        self.deploy()
        metadata = self.metadata()
        self.assertEqual(metadata["observability"], {"enabled": False})
        self.assertIs(metadata["logpush"], False)
        self.assertTrue(metadata["assets"]["config"]["run_worker_first"])
        enable = next(call for call in self.api.calls if call[1].endswith("/endbot/subdomain"))
        self.assertIs(json.loads(enable[2])["previews_enabled"], False)

    def test_the_bindings_carry_the_public_key_and_origin(self) -> None:
        self.deploy()
        bindings = {binding["name"]: binding for binding in self.metadata()["bindings"]}
        self.assertEqual(bindings["ENDBOT_ORIGIN"]["text"], "https://endbot.alice.workers.dev")
        self.assertEqual(bindings["ENDBOTD_LINK_KEY"]["text"], link_key_secret(ensure_link_key(self.paths)))
        self.assertEqual(bindings["RELAY"]["class_name"], "Relay")
        self.assertEqual(bindings["ASSETS"]["type"], "assets")

    def test_nothing_private_leaves_the_pc(self) -> None:
        self.deploy()
        private = (self.paths.state_secrets / "web-link.key").read_bytes()
        marker = private.split(b"\n")[1]
        for call in self.api.calls:
            self.assertNotIn(marker, call[2] or b"")

    def test_the_durable_object_migration_is_applied_once(self) -> None:
        self.deploy()
        self.assertEqual(self.metadata()["migrations"], {"new_tag": "v1", "new_sqlite_classes": ["Relay"]})
        self.api.calls.clear()
        self.api.migration = "v1"
        self.deploy()
        self.assertNotIn("migrations", self.metadata())

    def test_the_link_key_is_created_once_and_owner_only(self) -> None:
        self.deploy()
        path = self.paths.state_secrets / "web-link.key"
        first = path.read_bytes()
        self.api.calls.clear()
        self.deploy()
        self.assertEqual(path.read_bytes(), first)
        if os.name != "nt":
            self.assertEqual(path.stat().st_mode & 0o077, 0)

    def test_a_symlinked_key_is_refused(self) -> None:
        path = self.paths.state_secrets / "web-link.key"
        try:
            path.symlink_to(self.root / "elsewhere")
        except OSError:
            self.skipTest("symlinks are not available")
        with self.assertRaisesRegex(DeployError, "symlink"):
            ensure_link_key(self.paths)

    def test_a_custom_domain_is_the_only_origin(self) -> None:
        self.deploy(domain="endbot.example", zone_id="zone1")
        routed = next(call for call in self.api.calls if call[1].endswith("/endbot/subdomain"))
        self.assertEqual(json.loads(routed[2]), {"enabled": False, "previews_enabled": False})

    def test_a_domain_a_browser_would_spell_differently_is_refused_before_any_call(self) -> None:
        for domain in ("Endbot.Example", "endbot.example:443", "endbot.example/x", "bücher.example", "localhost"):
            with self.subTest(domain=domain), self.assertRaisesRegex(DeployError, "lowercase host name"):
                self.deploy(domain=domain, zone_id="zone1")
        self.assertEqual(self.api.calls, [])

    def test_a_refused_domain_leaves_a_serving_worker_and_the_settings_alone(self) -> None:
        self.deploy()
        self.api.migration = "v1"
        self.api.calls.clear()
        before = self.paths.endbot_toml.read_text(encoding="utf-8")
        self.api.fail["/workers/domains"] = (403, "no zone permission")
        with self.assertRaisesRegex(DeployError, "no zone permission"):
            self.deploy(domain="endbot.example", zone_id="zone1", confirm_domain_change=True)
        self.assertFalse(any(call[0] == "PUT" and call[1] == "/workers/scripts/endbot" for call in self.api.calls))
        self.assertEqual(self.paths.endbot_toml.read_text(encoding="utf-8"), before)
        self.assertEqual(self.requests, [])

    def test_a_settings_failure_stops_before_anything_remote(self) -> None:
        self.api.calls.clear()
        from unittest.mock import patch

        with patch("endbot_cli.webdeploy._regenerate", side_effect=[OSError("read-only"), None]), \
             self.assertRaises(OSError):
            self.deploy()
        self.assertFalse(any(call[0] in {"PUT", "POST"} for call in self.api.calls))
        self.assertEqual(load_config(self.paths.endbot_toml).web.origin, "")

    def test_a_custom_domain_is_attached(self) -> None:
        self.deploy(domain="endbot.example", zone_id="zone1")
        self.assertEqual(load_config(self.paths.endbot_toml).web.origin, "https://endbot.example")
        attached = json.loads(self.api.call("/workers/domains")[2])
        self.assertEqual((attached["hostname"], attached["zone_id"]), ("endbot.example", "zone1"))

    def test_a_domain_needs_its_zone_and_an_account_is_required_once(self) -> None:
        with self.assertRaisesRegex(DeployError, "zone-id"):
            self.deploy(domain="endbot.example")
        with self.assertRaisesRegex(DeployError, "account-id"):
            self.deploy(account_id=None)
        self.deploy()
        self.assertTrue(self.deploy(account_id=None))

    def test_a_new_origin_needs_confirmation_before_anything_changes(self) -> None:
        self.deploy()
        self.api.calls.clear()
        before = self.paths.endbot_toml.read_text(encoding="utf-8")
        with self.assertRaisesRegex(DeployError, "confirm-domain-change"):
            self.deploy(domain="endbot.example", zone_id="zone1")
        self.assertEqual(self.paths.endbot_toml.read_text(encoding="utf-8"), before)
        self.assertEqual([call for call in self.api.calls if call[0] != "GET"], [])

    def test_a_confirmed_change_deletes_credentials_in_a_stopped_instance(self) -> None:
        self.deploy()
        store = self.root / "state" / "web"
        store.mkdir(parents=True)
        for key, entry in (("passkeys", {"id": "p"}), ("sessions", {"hash": "h"})):
            (store / f"{key}.json").write_text(json.dumps({"version": 1, key: [entry]}), encoding="utf-8")
        accounts = '{"version": 1, "accounts": []}'
        (store / "accounts.json").write_text(accounts, encoding="utf-8")
        self.deploy(domain="endbot.example", zone_id="zone1", confirm_domain_change=True)
        self.assertEqual(json.loads((store / "passkeys.json").read_text(encoding="utf-8"))["passkeys"], [])
        self.assertEqual(json.loads((store / "sessions.json").read_text(encoding="utf-8"))["sessions"], [])
        self.assertEqual((store / "accounts.json").read_text(encoding="utf-8"), accounts)

    def test_a_confirmed_change_asks_a_running_endbotd(self) -> None:
        self.deploy()
        self.running()
        self.deploy(domain="endbot.example", zone_id="zone1", confirm_domain_change=True)
        self.assertEqual(self.requests[0], "domain-change")

    def test_a_running_endbotd_reloads_and_the_link_is_awaited(self) -> None:
        self.running()
        self.assertTrue(self.deploy())
        self.assertEqual(self.requests[:2], ["reload", "status"])
        self.assertIn("link up", self.reports)

    def test_a_link_that_stays_down_says_why(self) -> None:
        self.running()
        self.linked = False
        self.assertFalse(self.deploy())
        self.assertTrue(any("not up yet: link down: why" in line for line in self.reports))

    def test_a_stopped_instance_says_the_link_comes_at_the_next_start(self) -> None:
        self.assertTrue(self.deploy())
        self.assertTrue(any("next `endbot start`" in line for line in self.reports))
        self.assertEqual(self.requests, [])

    def test_a_missing_bundle_says_what_to_do(self) -> None:
        with self.assertRaisesRegex(DeployError, "prebuilt Worker"):
            self.deploy(bundle=self.root / "nope")

    def test_an_api_refusal_leaves_the_settings_alone(self) -> None:
        self.api.fail["/assets-upload-session"] = (403, "not allowed")
        before = self.paths.endbot_toml.read_text(encoding="utf-8")
        with self.assertRaisesRegex(DeployError, "403.*not allowed"):
            self.deploy()
        self.assertEqual(self.paths.endbot_toml.read_text(encoding="utf-8"), before)


class TokenTests(DeployCase):
    def test_the_environment_wins_and_the_prompt_is_the_fallback(self) -> None:
        self.assertEqual(read_token({"CLOUDFLARE_API_TOKEN": " a "}, lambda _: "b"), "a")
        self.assertEqual(read_token({}, lambda _: "b"), "b")
        with self.assertRaises(DeployError):
            read_token({}, lambda _: "  ")

    def test_there_is_no_token_argument(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            main(["--instance", str(self.root), "web", "deploy", "--token", "x"])
        self.assertEqual(raised.exception.code, 2)

    def test_the_token_is_scrubbed_from_an_api_error(self) -> None:
        self.api.fail["/assets-upload-session"] = (400, f"bad header Bearer {TOKEN}")
        with self.assertRaises(DeployError) as raised:
            self.deploy()
        self.assertNotIn(TOKEN, str(raised.exception))
        self.assertIn("[token]", str(raised.exception))

    def test_the_token_is_never_written_or_reported(self) -> None:
        self.assertTrue(self.deploy())
        self.assertFalse(any(TOKEN in line for line in self.reports))
        for path in self.root.rglob("*"):
            if path.is_file():
                self.assertNotIn(TOKEN.encode(), path.read_bytes(), path)

    def test_the_command_reports_a_failure_on_stderr_without_the_token(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = run_web_deploy(self.paths, environ={"CLOUDFLARE_API_TOKEN": TOKEN}, account_id=None,
                                  worker_name=None, domain=None, zone_id=None, confirm_domain_change=False,
                                  bundle=self.bundle)
        self.assertEqual(code, 1)
        self.assertIn("account-id", stderr.getvalue())
        self.assertNotIn(TOKEN, stderr.getvalue())


class RotateTests(DeployCase):
    def rotate(self) -> bool:
        return rotate_key(self.paths, token=TOKEN, report=self.reports.append, request=self.request, opener=self.api,
                          sleep=lambda _: None, wait=0.01)

    def test_nothing_deployed_is_refused(self) -> None:
        with self.assertRaisesRegex(DeployError, "web deploy"):
            self.rotate()

    def test_rotation_replaces_the_secret_and_the_key(self) -> None:
        self.deploy()
        path = self.paths.state_secrets / "web-link.key"
        old = path.read_bytes()
        self.running()
        self.api.calls.clear()
        self.assertTrue(self.rotate())
        self.assertNotEqual(path.read_bytes(), old)
        secret = json.loads(self.api.call("/secrets")[2])
        self.assertEqual(secret["name"], "ENDBOTD_LINK_KEY")
        self.assertEqual(secret["text"], link_key_secret(ensure_link_key(self.paths)))
        # Once the new link is up the old key goes.
        self.assertFalse((path.with_name("web-link.key.old")).exists())

    def test_the_old_key_stays_until_the_new_link_is_up(self) -> None:
        self.deploy()
        self.running()
        self.linked = False
        self.assertFalse(self.rotate())
        self.assertTrue((self.paths.state_secrets / "web-link.key.old").exists())

    def test_a_refused_secret_keeps_the_current_key(self) -> None:
        self.deploy()
        path = self.paths.state_secrets / "web-link.key"
        old = path.read_bytes()
        self.api.fail["/secrets"] = (403, "no")
        with self.assertRaises(DeployError):
            self.rotate()
        self.assertEqual(path.read_bytes(), old)

    def test_the_key_id_matches_what_endbotd_proves_with(self) -> None:
        key = Ed25519PrivateKey.generate()
        secret = json.loads(link_key_secret(key))
        raw = base64.urlsafe_b64decode(secret["key"] + "=")
        self.assertEqual(secret["keyId"], hashlib.sha256(raw).hexdigest()[:16])


if __name__ == "__main__":
    unittest.main()
