from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

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
    def test_manifest_separates_current_artifact_from_historical_evidence(self) -> None:
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
            lock = json.loads((ROOT / "endstone.lock").read_text(encoding="utf-8"))

            # 1. Generated manifest contains current package version from lock.
            self.assertEqual(
                manifest["endstone"]["package_version"], lock["endstone"]["package_version"]
            )
            self.assertEqual(manifest["schema_version"], 2)

            # 2. Current artifact does NOT claim M0/M2 human achievement observation.
            safety = manifest["tested_safety_conditions"]
            self.assertFalse(safety["actual_xbox_achievement_unlock_observed"])
            self.assertFalse(safety["m2_actual_xbox_achievement_unlock_observed"])
            self.assertEqual(safety["actual_xbox_achievement_unlock_scope"], "m0_bot_ping")
            self.assertEqual(
                safety["m2_actual_xbox_achievement_unlock_scope"],
                "representative_m2_controls",
            )

            # 3-4. Historical evidence is explicitly associated with .1.
            historical = manifest["historical_validation"]
            self.assertEqual(
                historical["patched_endstone_package_version"], "0.11.11+endbot.1"
            )
            self.assertNotEqual(
                historical["patched_endstone_package_version"],
                manifest["endstone"]["package_version"],
            )
            self.assertTrue(historical["m0"]["observed"])
            self.assertEqual(historical["m0"]["scope"], "m0_bot_ping")
            self.assertTrue(historical["m2"]["observed"])
            self.assertEqual(historical["m2"]["scope"], "representative_m2_controls")

            # 5. Recorded M0 Endbot revision is included where the repo has it.
            self.assertEqual(
                historical["m0"]["endbot_revision"],
                "f509ac4e8677d9bc870b341f001b8e42f512df97",
            )

            # 6. Schema validates the generated representation (manual const checks,
            # no new validator dependency).
            schema = json.loads(
                (ROOT / "release/compatibility-manifest.schema.json").read_text(encoding="utf-8")
            )
            self.assertEqual(schema["properties"]["schema_version"]["const"], 2)
            self.assertEqual(manifest["schema_version"], schema["properties"]["schema_version"]["const"])
            safety_schema = schema["properties"]["tested_safety_conditions"]
            self.assertIn("m2_actual_xbox_achievement_unlock_scope", safety_schema["required"])
            self.assertEqual(
                safety_schema["properties"]["actual_xbox_achievement_unlock_observed"]["const"],
                False,
            )
            self.assertEqual(
                safety_schema["properties"]["m2_actual_xbox_achievement_unlock_observed"]["const"],
                False,
            )
            self.assertEqual(
                safety_schema["properties"]["m2_actual_xbox_achievement_unlock_scope"]["const"],
                manifest["tested_safety_conditions"]["m2_actual_xbox_achievement_unlock_scope"],
            )
            historical_schema = schema["properties"]["historical_validation"]
            self.assertIn("patched_endstone_package_version", historical_schema["required"])
            self.assertEqual(
                historical_schema["properties"]["patched_endstone_package_version"]["const"],
                historical["patched_endstone_package_version"],
            )
            self.assertEqual(
                historical_schema["properties"]["m0"]["properties"]["endbot_revision"]["const"],
                historical["m0"]["endbot_revision"],
            )
            self.assertEqual(
                historical_schema["properties"]["m2"]["properties"]["scope"]["const"],
                historical["m2"]["scope"],
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
        # First-release alignment: the tracked runtime and plugin versions agree
        # (npm spelling and PEP 440 spelling of the same release), so the
        # release-candidate workflow input matches both package metadata files.
        self.assertEqual(runtime_version, plugin_version)

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
            ".github/workflows/full-validation.yml",
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


def _load_script(name: str):
    path = ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _release_job_blocks() -> dict[str, str]:
    workflow = (ROOT / ".github/workflows/release-candidate.yml").read_text(encoding="utf-8")
    linux = workflow.split("  linux:", 1)[1].split("  windows:", 1)[0]
    windows, assemble = workflow.split("  windows:", 1)[1].split("  assemble:", 1)
    return {"workflow": workflow, "linux": linux, "windows": windows, "assemble": assemble}


class WindowsWheelContractTests(unittest.TestCase):
    def test_release_candidate_builds_both_platforms_then_assembles(self) -> None:
        blocks = _release_job_blocks()
        workflow = blocks["workflow"]
        self.assertIn("needs: [linux, windows]", blocks["assemble"])
        self.assertIn("runs-on: ubuntu-22.04", blocks["linux"])
        self.assertIn("runs-on: windows-2022", blocks["windows"])
        self.assertIn('python-version: "3.12"', blocks["windows"])
        self.assertIn("--build-selector cp312-win_amd64", blocks["windows"])
        self.assertIn("python scripts/inspect_windows_wheel.py", blocks["windows"])
        self.assertIn("python scripts/test_package_install.py", blocks["windows"])
        self.assertIn("python scripts/prepare_endstone.py", blocks["windows"])
        self.assertIn("python scripts/check_portability.py", blocks["windows"])
        self.assertIn("python -m unittest discover -s tests", blocks["windows"])
        self.assertIn("python -m unittest discover -s plugin/endbot/tests", blocks["windows"])
        # The published plugin wheel and tarballs are built once, in assemble,
        # so the candidate checksums are unambiguous.
        self.assertEqual(
            workflow.count("python -m build --wheel --outdir dist plugin/endbot"), 1
        )
        self.assertIn(
            "python -m build --wheel --outdir dist plugin/endbot", blocks["assemble"]
        )
        self.assertIn("actions/download-artifact@v4", blocks["assemble"])
        self.assertIn("endstone-linux-wheel", workflow)
        self.assertIn("endstone-windows-wheel", workflow)
        self.assertIn("endbot-${{ inputs.version }}-candidate", blocks["assemble"])

    def test_windows_job_mirrors_upstream_toolchain_without_secrets(self) -> None:
        blocks = _release_job_blocks()
        self.assertIn("ilammy/msvc-dev-cmd@v1", blocks["windows"])
        self.assertIn("lukka/get-cmake@latest", blocks["windows"])
        self.assertIn("cibuildwheel==3.4.1", blocks["windows"])
        # No secret is passed to any step (the workflow comment may name the
        # token it deliberately omits, so match secret references, not words).
        self.assertNotIn("${{ secrets.", blocks["workflow"])
        self.assertNotIn("SENTRY_AUTH_TOKEN:", blocks["workflow"])
        # No container runtime exists on the Windows runner.
        self.assertNotIn("docker", blocks["windows"])

    def test_build_endstone_wheel_supports_windows_selector(self) -> None:
        builder = _load_script("build_endstone_wheel.py")
        tag = f"cp{sys.version_info.major}{sys.version_info.minor}"
        with mock.patch.object(sys, "platform", "win32"):
            self.assertEqual(builder.default_build_selector(), f"{tag}-win_amd64")
        with mock.patch.object(sys, "platform", "linux"):
            self.assertEqual(builder.default_build_selector(), f"{tag}-manylinux_x86_64")

        version = "0.11.12+endbot.1"
        # Linux behaviour is unchanged: manylinux required, raw linux rejected.
        builder.check_repaired_wheel(
            Path(f"endstone-{version}-cp312-cp312-manylinux_2_31_x86_64.whl"),
            version,
            "cp312-manylinux_x86_64",
        )
        with self.assertRaises(builder.WheelBuildError) as linux_failure:
            builder.check_repaired_wheel(
                Path(f"endstone-{version}-cp312-cp312-linux_x86_64.whl"),
                version,
                "cp312-manylinux_x86_64",
            )
        self.assertIn("manylinux-tagged wheel", str(linux_failure.exception))
        # Windows behaviour: locked version plus a win_amd64 tag, no linux tag.
        builder.check_repaired_wheel(
            Path(f"endstone-{version}-cp312-cp312-win_amd64.whl"),
            version,
            "cp312-win_amd64",
        )
        with self.assertRaises(builder.WheelBuildError) as windows_failure:
            builder.check_repaired_wheel(
                Path(f"endstone-{version}-cp312-cp312-manylinux_2_31_x86_64.whl"),
                version,
                "cp312-win_amd64",
            )
        self.assertIn("win_amd64-tagged wheel", str(windows_failure.exception))
        with self.assertRaises(builder.WheelBuildError) as version_failure:
            builder.check_repaired_wheel(
                Path("endstone-0.11.12-cp312-cp312-win_amd64.whl"),
                version,
                "cp312-win_amd64",
            )
        self.assertIn("locked package version", str(version_failure.exception))

    def _make_windows_wheel(
        self,
        directory: Path,
        version: str,
        tag: str = "cp312-cp312-win_amd64",
        metadata_version: str | None = None,
        include_dll: bool = True,
        include_pyd: bool = True,
    ) -> Path:
        wheel = directory / f"endstone-{version}-{tag}.whl"
        dist_info = f"endstone-{version}.dist-info"
        with zipfile.ZipFile(wheel, "w") as archive:
            archive.writestr("endstone/__init__.py", "")
            if include_pyd:
                archive.writestr("endstone/_python.cp312-win_amd64.pyd", b"\x00")
            if include_dll:
                archive.writestr("endstone/endstone_runtime.dll", b"\x00")
            archive.writestr(
                f"{dist_info}/WHEEL",
                "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: false\n"
                "Tag: cp312-cp312-win_amd64\n",
            )
            archive.writestr(
                f"{dist_info}/METADATA",
                f"Metadata-Version: 2.4\nName: endstone\nVersion: {metadata_version or version}\n",
            )
        return wheel

    def test_inspect_windows_wheel_accepts_locked_wheel(self) -> None:
        inspector = _load_script("inspect_windows_wheel.py")
        lock = json.loads((ROOT / "endstone.lock").read_text(encoding="utf-8"))
        version = lock["endstone"]["package_version"]
        with tempfile.TemporaryDirectory() as directory:
            wheel = self._make_windows_wheel(Path(directory), version)
            inspector.inspect(wheel)

    def test_inspect_windows_wheel_rejects_bad_artifacts(self) -> None:
        inspector = _load_script("inspect_windows_wheel.py")
        lock = json.loads((ROOT / "endstone.lock").read_text(encoding="utf-8"))
        version = lock["endstone"]["package_version"]
        cases = {
            "linux tag": {
                "tag": "cp312-cp312-manylinux_2_31_x86_64",
            },
            "wrong interpreter": {
                "tag": "cp311-cp311-win_amd64",
            },
            "unpatched version": {
                "metadata_version": "0.11.12",
            },
            "missing runtime dll": {
                "include_dll": False,
            },
            "missing extensions": {
                "include_pyd": False,
            },
        }
        for label, kwargs in cases.items():
            with self.subTest(case=label), tempfile.TemporaryDirectory() as directory:
                wheel = self._make_windows_wheel(Path(directory), version, **kwargs)
                with self.assertRaises(inspector.WheelInspectionError):
                    inspector.inspect(wheel)

    def test_paired_install_handles_windows_layout(self) -> None:
        installer = (ROOT / "scripts/test_package_install.py").read_text(encoding="utf-8")
        self.assertIn('sys.platform == "win32"', installer)
        self.assertIn('"win_amd64" not in endstone_wheel.name', installer)
        self.assertIn('environment / "Scripts" / "python.exe"', installer)

    def test_bds_build_stays_on_the_single_locked_baseline(self) -> None:
        # Windows BDS 1.26.51.1 reports build 51061361 while the Linux
        # package reports 51061372. The manifest records the single lock
        # value (the Linux baseline) and must not silently invent a
        # per-platform reading; any per-platform record needs a schema
        # change and deliberate validation first.
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
            lock = json.loads((ROOT / "endstone.lock").read_text(encoding="utf-8"))
            self.assertEqual(manifest["bds"], lock["bds"])


if __name__ == "__main__":
    unittest.main()
