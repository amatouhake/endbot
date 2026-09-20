from __future__ import annotations

import hashlib
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def resulting_patch_fragment(path: str, hunk_prefix: str) -> str:
    patch = (ROOT / "patches/endstone/0001-feat-auth-add-local-bot-login-trust.patch").read_text(
        encoding="utf-8"
    )
    section_marker = f"diff --git a/{path} b/{path}\n"
    section = patch.split(section_marker, 1)[1].split("\ndiff --git ", 1)[0]
    hunk = section.split(hunk_prefix, 1)[1]
    lines: list[str] = []
    for line in hunk.splitlines()[1:]:
        if line.startswith("@@ "):
            break
        if (line.startswith("+") and not line.startswith("+++")) or line.startswith(" "):
            lines.append(line[1:])
    return "\n".join(lines)


class LoginHookContractTests(unittest.TestCase):
    def test_missing_raw_token_skips_local_verification_and_reaches_stock_validator(self) -> None:
        patch = (ROOT / "patches/endstone/0001-feat-auth-add-local-bot-login-trust.patch").read_text(
            encoding="utf-8"
        )
        self.assertIn("[[nodiscard]] const WebToken *_getRawRequest() const;", patch)
        self.assertIn("return raw_token_ ? &*raw_token_ : nullptr;", patch)

        source = resulting_patch_fragment(
            "src/endstone/runtime/bedrock_hooks/server_network_handler.cpp",
            "@@ -148,10 ",
        )
        raw_guard = source.index("if (const auto *raw_request = request._getRawRequest()) {")
        local_verify = source.index("verifier->verify(", raw_guard)
        stock_call = source.index(
            "auth_info = ENDSTONE_HOOK_CALL_ORIGINAL(&ServerNetworkHandler::_validateLoginPacket",
            local_verify,
        )
        self.assertLess(raw_guard, local_verify)
        self.assertLess(local_verify, stock_call)
        self.assertNotIn("getCompactClientDataToken(request._getRawRequest())", source)

    def test_stock_rejection_returns_before_authentication_result_is_dereferenced(self) -> None:
        source = resulting_patch_fragment(
            "src/endstone/runtime/bedrock_hooks/server_network_handler.cpp",
            "@@ -148,10 ",
        )
        stock_call = source.index(
            "auth_info = ENDSTONE_HOOK_CALL_ORIGINAL(&ServerNetworkHandler::_validateLoginPacket"
        )
        rejection_guard = source.index("if (!auth_info) {\n            return auth_info;\n        }", stock_call)
        first_assuming_use = source.index("const auto &info = *auth_info;", stock_call)

        self.assertLess(stock_call, rejection_guard)
        self.assertLess(rejection_guard, first_assuming_use)
        assuming_uses = [match.start() for match in re.finditer(r"\*auth_info|auth_info->|auth_info\.value\(", source)]
        self.assertTrue(assuming_uses)
        self.assertTrue(all(position > rejection_guard for position in assuming_uses))


class PackageVersionContractTests(unittest.TestCase):
    def test_plugin_requires_exact_endbot_local_endstone_version(self) -> None:
        lock = json.loads((ROOT / "endstone.lock").read_text(encoding="utf-8"))
        plugin = (ROOT / "plugin/endbot/pyproject.toml").read_text(encoding="utf-8")
        package_version = lock["endstone"]["package_version"]

        upstream_version = lock["endstone"]["version"]
        self.assertRegex(package_version, rf"^{re.escape(upstream_version)}\+endbot\.[0-9]+$")
        dependencies = re.findall(r'^dependencies = \["([^"]+)"\]$', plugin, flags=re.MULTILINE)
        self.assertEqual(dependencies, [f"endstone=={package_version}"])
        self.assertNotEqual(package_version, lock["endstone"]["version"])

    def test_third_party_notice_records_current_patch_revision(self) -> None:
        patch_root = ROOT / "patches" / "endstone"
        series = [line for line in (patch_root / "series").read_text(encoding="utf-8").splitlines() if line]
        digest = hashlib.sha256()
        for name in series:
            digest.update((patch_root / name).read_bytes())

        notice = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
        self.assertIn(digest.hexdigest(), notice)


if __name__ == "__main__":
    unittest.main()
