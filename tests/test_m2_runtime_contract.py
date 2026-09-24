# Copyright 2026 amatouhake and Endbot contributors
# SPDX-License-Identifier: Apache-2.0

import json
import pathlib
import subprocess
import unittest

import tomllib

ROOT = pathlib.Path(__file__).resolve().parents[1]


class M2RuntimeContractTests(unittest.TestCase):
    def test_control_deadlines_cover_bounded_session_replacement(self):
        runtime = json.loads((ROOT / "runtime" / "endbot-runtime.example.json").read_text())
        plugin = tomllib.loads(
            (ROOT / "plugin" / "endbot" / "src" / "endstone_endbot" / "config.toml").read_text()
        )
        replacement_bound_seconds = 5 + runtime["reconnect"]["sessionReplacementDelayMs"] / 1000
        plugin_timeout = plugin["runtime"]["timeout-seconds"]
        runtime_timeout = runtime["controlRequestTimeoutMs"] / 1000
        self.assertGreater(plugin_timeout, replacement_bound_seconds)
        self.assertGreater(runtime_timeout, replacement_bound_seconds)
        self.assertGreater(plugin_timeout, runtime_timeout)

    def test_exact_protocol_sources_and_schema_are_pinned(self):
        package = json.loads((ROOT / "runtime/package.json").read_text())
        lock = json.loads((ROOT / "runtime/package-lock.json").read_text())
        packages = lock["packages"]

        # Exact upstream releases: no fork, no install-time schema rewrite.
        self.assertEqual(package["dependencies"]["bedrock-protocol"], "3.60.1")
        self.assertEqual(packages["node_modules/bedrock-protocol"]["version"], "3.60.1")
        self.assertEqual(packages["node_modules/minecraft-data"]["version"], "3.117.0")
        self.assertNotIn("postinstall", package["scripts"])
        self.assertFalse((ROOT / "runtime/scripts/prepare-minecraft-data.js").exists())
        self.assertEqual(lock["packages"][""]["dependencies"], package["dependencies"])
        # prismarine-xbox-services is not on the npm registry; pin it as an
        # HTTPS tarball so installs never need git or SSH.
        xbox = "https://codeload.github.com/PrismarineJS/prismarine-xbox-services/tar.gz/052a9676f514c5470a723651b1dbb1adcf238944"
        self.assertEqual(package["dependencies"]["prismarine-xbox-services"], xbox)
        self.assertEqual(package["overrides"], {"prismarine-xbox-services": "$prismarine-xbox-services"})
        self.assertEqual(packages["node_modules/prismarine-xbox-services"]["resolved"], xbox)
        for name, entry in packages.items():
            with self.subTest(package=name):
                self.assertFalse(str(entry.get("resolved", "")).startswith("git"), entry.get("resolved"))
        self.assertEqual(package["engines"]["node"], ">=24")

    def test_runtime_scripts_do_not_depend_on_a_posix_shell(self):
        package = json.loads((ROOT / "runtime/package.json").read_text())
        check = ROOT / "runtime/scripts/check-syntax.js"

        # cmd.exe does not expand globs, and `node --check` only checks one file.
        self.assertEqual(package["scripts"]["check"], "node scripts/check-syntax.js")
        self.assertTrue(check.is_file())
        self.assertNotIn("*", package["scripts"]["check"])
        # The NetherNet transport is pure JS (werift); no dependency install
        # script is approved. raknet-native's blocked script is unused here.
        self.assertNotIn("allowScripts", package)

    def test_release_runtime_archive_contains_protocol_preparation(self):
        workflow = (ROOT / ".github/workflows/release-candidate.yml").read_text()
        self.assertIn(
            "-C runtime package.json package-lock.json README.md "
            "endbot-runtime.example.json scripts src",
            workflow,
        )

    def test_documented_quick_start_state_is_ignored(self):
        generated_paths = (
            "endbot-runtime.json",
            "data/profiles/Alice.json",
            "secrets/control.token",
        )
        for generated_path in generated_paths:
            with self.subTest(path=generated_path):
                result = subprocess.run(
                    ["git", "check-ignore", "--no-index", "--quiet", generated_path],
                    cwd=ROOT,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, f"quick-start path is not ignored: {generated_path}")


if __name__ == "__main__":
    unittest.main()
