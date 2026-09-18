from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def resulting_patch_fragment(path: str, hunk_prefix: str) -> str:
    patch = (ROOT / "patches/endstone/0001-feat-auth-add-local-ownerbot-login-trust.patch").read_text(
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


if __name__ == "__main__":
    unittest.main()
