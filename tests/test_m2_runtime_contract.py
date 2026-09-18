# Copyright 2026 amatouhake and Endbot contributors
# SPDX-License-Identifier: Apache-2.0

import json
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class M2RuntimeContractTests(unittest.TestCase):
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

    def test_release_runtime_archive_contains_protocol_preparation(self):
        workflow = (ROOT / ".github/workflows/release-candidate.yml").read_text()
        self.assertIn(
            "-C runtime package.json package-lock.json README.md "
            "endbot-runtime.example.json scripts src",
            workflow,
        )


if __name__ == "__main__":
    unittest.main()
