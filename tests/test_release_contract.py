from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

import tomllib

ROOT = Path(__file__).resolve().parents[1]


class ReleaseWheelContractTests(unittest.TestCase):
    def test_release_candidate_repairs_and_inspects_endstone_wheel(self) -> None:
        workflow = (ROOT / ".github/workflows/release-candidate.yml").read_text(encoding="utf-8")
        self.assertIn("python scripts/build_endstone_wheel.py", workflow)
        self.assertIn("python scripts/inspect_linux_wheel.py", workflow)
        self.assertIn("--output-dir dist", workflow)
        self.assertNotIn("python -m build --wheel --outdir dist build/endstone-patched", workflow)

        builder = (ROOT / "scripts/build_endstone_wheel.py").read_text(encoding="utf-8")
        self.assertRegex(builder, r'"-m",\s+"cibuildwheel",')
        self.assertIn('"git", "clone", "--quiet", "--local", "--no-hardlinks"', builder)
        self.assertIn("cwd=isolated_source", builder)
        self.assertNotIn('"-m", "build"', builder)

    def test_full_validation_uses_repaired_wheel_for_paired_install(self) -> None:
        workflow = (ROOT / ".github/workflows/full-validation.yml").read_text(encoding="utf-8")
        build = workflow.index("python scripts/build_endstone_wheel.py")
        inspect = workflow.index("python scripts/inspect_linux_wheel.py", build)
        install = workflow.index("python scripts/test_package_install.py", inspect)
        self.assertLess(build, inspect)
        self.assertLess(inspect, install)

    def test_release_candidate_version_must_match_package_metadata(self) -> None:
        workflow = (ROOT / ".github/workflows/release-candidate.yml").read_text(encoding="utf-8")
        check = workflow.index('python scripts/check_release_version.py "$CANDIDATE_VERSION"')
        plugin_build = workflow.index("python -m build --wheel --outdir dist plugin/endbot", check)
        manifest = workflow.index("python scripts/generate_compatibility_manifest.py", plugin_build)
        self.assertLess(check, plugin_build)
        self.assertLess(plugin_build, manifest)

        runtime_version = json.loads((ROOT / "runtime/package.json").read_text(encoding="utf-8"))["version"]
        plugin_version = tomllib.loads(
            (ROOT / "plugin/endbot/pyproject.toml").read_text(encoding="utf-8")
        )["project"]["version"]
        self.assertNotEqual(runtime_version, plugin_version)

        accepted = subprocess.run(
            (sys.executable, "scripts/check_release_version.py", runtime_version),
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(accepted.returncode, 0, accepted.stderr)

        rejected = subprocess.run(
            (sys.executable, "scripts/check_release_version.py", "9.9.9-rc1"),
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("does not match runtime package version", rejected.stderr)


if __name__ == "__main__":
    unittest.main()
