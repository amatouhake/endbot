# Copyright 2026 amatouhake and Endbot contributors
# SPDX-License-Identifier: Apache-2.0

import unittest

from scripts import check_windows_toolchain as toolchain


class WindowsToolchainCheckTests(unittest.TestCase):
    def inspect(self, on_path, in_visual_studio, versions):
        return {
            result["executable"]: result
            for result in toolchain.inspect(
                which=lambda name: f"/path/{name}" if name in on_path else None,
                fallback=lambda name: f"/vs/{name}" if name in in_visual_studio else None,
                version=lambda path, arguments: versions.get(path.rsplit("/", 1)[-1], "9.9.9"),
            )
        }

    def test_reports_missing_llvm_and_developer_prompt_precisely(self):
        results = self.inspect(
            on_path={"python", "git", "node", "npm", "cmake", "ninja", "conan"},
            in_visual_studio={"cl"},
            versions={"python": "3.12.10", "node": "24.17.0", "cmake": "4.3.4", "conan": "2.32.0"},
        )
        self.assertEqual(results["cl"]["status"], "not-on-PATH")
        self.assertEqual(results["cl"]["remedy"], toolchain.DEVELOPER_PROMPT)
        self.assertEqual(results["clang-cl"]["status"], "missing")
        self.assertIn(toolchain.VS_LLVM_COMPONENT, results["clang-cl"]["remedy"])
        self.assertEqual(results["lld-link"]["status"], "missing")
        self.assertEqual(results["conan"]["status"], "ok")
        self.assertEqual(results["conan"]["remedy"], "")

    def test_flags_versions_below_the_pinned_endstone_minimums(self):
        every_tool = {requirement.executable for requirement in toolchain.REQUIREMENTS}
        results = self.inspect(
            on_path=every_tool,
            in_visual_studio=set(),
            versions={"clang-cl": "17.0.3", "conan": "1.66.0", "cmake": "3.28.1", "node": "20.19.0", "lld-link": "18.1.8"},
        )
        self.assertEqual(results["clang-cl"]["status"], "too-old")
        self.assertEqual(results["conan"]["status"], "too-old")
        self.assertEqual(results["cmake"]["status"], "too-old")
        self.assertEqual(results["node"]["status"], "too-old")
        self.assertEqual(results["lld-link"]["status"], "ok")


if __name__ == "__main__":
    unittest.main()
