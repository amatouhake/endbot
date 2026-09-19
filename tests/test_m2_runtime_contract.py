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
        prepare = (ROOT / "runtime/scripts/prepare-minecraft-data.js").read_text()

        self.assertEqual(
            package["dependencies"]["bedrock-protocol"],
            "https://github.com/amatouhake/bedrock-protocol/archive/"
            "fb0af8e388127724c323fd46800e47ba004b1c55.tar.gz",
        )
        self.assertEqual(package["scripts"]["postinstall"], "node scripts/prepare-minecraft-data.js")
        self.assertEqual(lock["packages"][""]["dependencies"], package["dependencies"])
        self.assertIn("7c1fe886dd92837c0550e8eff91440361c7d677f", prepare)
        self.assertIn("MINECRAFT_PROTOCOL = 2193", prepare)

    def test_runtime_scripts_do_not_depend_on_a_posix_shell(self):
        package = json.loads((ROOT / "runtime/package.json").read_text())
        prepare = (ROOT / "runtime/scripts/prepare-minecraft-data.js").read_text()
        check = ROOT / "runtime/scripts/check-syntax.js"

        # cmd.exe does not expand globs, and `node --check` only checks one file.
        self.assertEqual(package["scripts"]["check"], "node scripts/check-syntax.js")
        self.assertTrue(check.is_file())
        self.assertNotIn("*", package["scripts"]["check"])
        # Node refuses to spawn an npm .cmd shim without a shell on Windows.
        self.assertIn("shell: process.platform === 'win32'", prepare)
        # npm 12 blocks dependency install scripts by default; the NetherNet
        # transport needs its prebuilt binary, so that approval must stay pinned.
        self.assertEqual(package["allowScripts"], {"node-datachannel@0.31.0": True})

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
