from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import tomllib

ROOT = Path(__file__).resolve().parents[1]

CACHE_MISS_GUARD = "steps.endstone-wheel-cache.outputs.cache-hit != 'true'"


def _step_block(workflow: str, marker: str) -> str:
    start = workflow.index(marker)
    end = workflow.find("\n      - ", start + len(marker))
    return workflow[start:] if end == -1 else workflow[start:end]


def _hashfiles_segments(workflow: str) -> list[str]:
    return re.findall(r"hashFiles\((.*?)\)", workflow, flags=re.DOTALL)


class ReleaseWheelContractTests(unittest.TestCase):
    def test_manifest_records_completed_m0_and_m2_achievement_gates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "compatibility-manifest.json"
            completed = subprocess.run(
                (sys.executable, "scripts/generate_compatibility_manifest.py", str(output)),
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            manifest = json.loads(output.read_text(encoding="utf-8"))
            self.assertTrue(manifest["tested_safety_conditions"]["actual_xbox_achievement_unlock_observed"])
            self.assertEqual(
                manifest["tested_safety_conditions"]["actual_xbox_achievement_unlock_scope"],
                "m0_bot_ping",
            )
            self.assertTrue(
                manifest["tested_safety_conditions"]["m2_actual_xbox_achievement_unlock_observed"]
            )
            self.assertEqual(
                manifest["tested_safety_conditions"]["m2_actual_xbox_achievement_unlock_scope"],
                "representative_m2_controls",
            )
            schema = json.loads(
                (ROOT / "release/compatibility-manifest.schema.json").read_text(encoding="utf-8")
            )
            safety_schema = schema["properties"]["tested_safety_conditions"]
            self.assertIn("m2_actual_xbox_achievement_unlock_scope", safety_schema["required"])
            self.assertEqual(
                safety_schema["properties"]["m2_actual_xbox_achievement_unlock_scope"]["const"],
                manifest["tested_safety_conditions"]["m2_actual_xbox_achievement_unlock_scope"],
            )

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


class CICacheContractTests(unittest.TestCase):
    def test_fast_ci_skips_endstone_preparation_but_keeps_coverage(self) -> None:
        workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertNotIn("prepare_endstone", workflow)
        self.assertNotIn("scripts/prepare_endstone.py", workflow)
        for required in (
            "python scripts/check_portability.py",
            "ruff check scripts tests plugin/endbot",
            "python -m unittest discover -s tests",
            "python -m unittest discover -s plugin/endbot/tests",
            "python -m build --wheel --outdir dist/plugin",
            "npm ci --prefix runtime",
            "npm run check --prefix runtime",
            "npm test --prefix runtime",
        ):
            self.assertIn(required, workflow)
        self.assertIn("cache: npm", workflow)
        self.assertIn("runtime/package-lock.json", workflow)
        self.assertNotIn("node_modules", workflow)

    def test_full_validation_restores_cache_before_heavy_setup(self) -> None:
        workflow = (ROOT / ".github/workflows/full-validation.yml").read_text(encoding="utf-8")
        checkout = workflow.index("actions/checkout")
        restore = workflow.index("actions/cache/restore")
        llvm = workflow.index("Install LLVM 20")
        prepare = workflow.index("Prepare patched Endstone")
        conan = workflow.index("conan install")
        self.assertLess(checkout, restore)
        self.assertLess(restore, llvm)
        self.assertLess(restore, prepare)
        self.assertLess(restore, conan)
        self.assertIn("dist/endstone", workflow)

    def test_endstone_cache_uses_exact_matching(self) -> None:
        workflow = (ROOT / ".github/workflows/full-validation.yml").read_text(encoding="utf-8")
        self.assertIn("actions/cache/restore@v6", workflow)
        self.assertIn("actions/cache/save@v6", workflow)
        self.assertNotIn("restore-keys", workflow)
        self.assertNotIn("github.sha", workflow)
        for identifier in (
            "endstone-wheel-v1",
            "linux-x64",
            "cpy312",
            "llvm20",
            "cibw3.4.1",
        ):
            self.assertIn(identifier, workflow)
        segments = _hashfiles_segments(workflow)
        self.assertTrue(segments)
        fingerprinted = " ".join(segments)
        for required_input in (
            "endstone.lock",
            "patches/endstone/**",
            "scripts/prepare_endstone.py",
            "scripts/build_endstone_wheel.py",
        ):
            self.assertIn(required_input, fingerprinted)
        self.assertNotIn("plugin/endbot", fingerprinted)
        self.assertNotIn("runtime/", fingerprinted)

    def test_expensive_endstone_steps_run_only_on_cache_miss(self) -> None:
        workflow = (ROOT / ".github/workflows/full-validation.yml").read_text(encoding="utf-8")
        for marker in (
            "Install LLVM 20 and libc++",
            "Prepare patched Endstone",
            "Resolve patched Endstone dependencies",
            "Build patched Endstone",
            "Run patched Endstone tests",
            "Build repaired Endstone wheel",
        ):
            with self.subTest(step=marker):
                block = _step_block(workflow, marker)
                self.assertIn("cache-hit", block)
                self.assertIn(CACHE_MISS_GUARD, block)

    def test_downstream_gates_run_on_hit_and_miss(self) -> None:
        workflow = (ROOT / ".github/workflows/full-validation.yml").read_text(encoding="utf-8")
        for marker in (
            "Inspect repaired Endstone wheel",
            "Build plugin and test paired installation",
            "Test in a clean Linux consumer without LLVM",
        ):
            with self.subTest(step=marker):
                block = _step_block(workflow, marker)
                self.assertNotIn("cache-hit", block)
        self.assertIn("python scripts/inspect_linux_wheel.py", workflow)
        self.assertIn("python -m build --wheel --outdir dist/plugin", workflow)
        self.assertIn("python scripts/test_package_install.py", workflow)

    def test_cache_save_happens_only_after_validation_on_miss(self) -> None:
        workflow = (ROOT / ".github/workflows/full-validation.yml").read_text(encoding="utf-8")
        inspect = workflow.index("Inspect repaired Endstone wheel")
        paired = workflow.index("Build plugin and test paired installation")
        consumer = workflow.index("Test in a clean Linux consumer without LLVM")
        save = workflow.index("actions/cache/save")
        self.assertLess(inspect, paired)
        self.assertLess(paired, consumer)
        self.assertLess(consumer, save)
        block = _step_block(workflow, "Save validated Endstone wheel")
        self.assertIn(CACHE_MISS_GUARD, block)
        self.assertIn("dist/endstone", block)
        self.assertIn("endstone-wheel-v1-linux-x64-cpy312-llvm20-cibw3.4.1-", block)

    def test_release_candidate_stays_clean_build(self) -> None:
        workflow = (ROOT / ".github/workflows/release-candidate.yml").read_text(encoding="utf-8")
        self.assertNotIn("actions/cache/restore", workflow)
        self.assertNotIn("actions/cache/save", workflow)
        self.assertNotIn("cache-hit", workflow)
        self.assertNotIn("endstone-wheel-cache", workflow)
        for required in (
            "python scripts/prepare_endstone.py",
            "conan install",
            "cmake --build",
            "ctest --preset",
            "python scripts/build_endstone_wheel.py",
            "python scripts/inspect_linux_wheel.py",
            "python scripts/test_package_install.py",
        ):
            self.assertIn(required, workflow)
        self.assertIn("npm ci --prefix runtime", workflow)


if __name__ == "__main__":
    unittest.main()
