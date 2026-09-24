#!/usr/bin/env python3
"""Build the pinned patched Endstone wheel through upstream cibuildwheel."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

LINUX_PLATFORM_TAG = "manylinux_x86_64"
WINDOWS_PLATFORM_TAG = "win_amd64"


class WheelBuildError(RuntimeError):
    pass


def default_build_selector() -> str:
    tag = f"cp{sys.version_info.major}{sys.version_info.minor}"
    if sys.platform == "win32":
        return f"{tag}-{WINDOWS_PLATFORM_TAG}"
    return f"{tag}-{LINUX_PLATFORM_TAG}"


def check_repaired_wheel(repaired_wheel: Path, expected_version: str, build_selector: str) -> None:
    if expected_version not in repaired_wheel.name:
        raise WheelBuildError(
            f"repaired wheel {repaired_wheel.name} does not contain locked package version {expected_version}"
        )
    if WINDOWS_PLATFORM_TAG in build_selector:
        if (
            WINDOWS_PLATFORM_TAG not in repaired_wheel.name
            or "manylinux_" in repaired_wheel.name
            or "-linux_" in repaired_wheel.name
        ):
            raise WheelBuildError(f"repair did not produce a win_amd64-tagged wheel: {repaired_wheel.name}")
    elif "manylinux_" not in repaired_wheel.name or "-linux_" in repaired_wheel.name:
        raise WheelBuildError(f"repair did not produce a manylinux-tagged wheel: {repaired_wheel.name}")


def run(*args: str, env: dict[str, str] | None = None, cwd: Path | None = None) -> None:
    subprocess.run(args, check=True, env=env, cwd=cwd)


def output(*args: str) -> str:
    return subprocess.run(args, check=True, text=True, stdout=subprocess.PIPE).stdout.strip()


def empty_wheel_directory(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    existing = sorted(directory.glob("*.whl"))
    if existing:
        raise WheelBuildError(f"output directory contains stale wheels: {', '.join(path.name for path in existing)}")


def one_wheel(directory: Path, label: str) -> Path:
    wheels = sorted(directory.glob("*.whl"))
    if len(wheels) != 1:
        raise WheelBuildError(f"expected exactly one {label} wheel in {directory}, found {len(wheels)}")
    return wheels[0]


def build(source: Path, output_directory: Path, build_selector: str) -> Path:
    lock = json.loads((ROOT / "endstone.lock").read_text(encoding="utf-8"))
    expected_version = lock["endstone"]["package_version"]
    pyproject = source / "pyproject.toml"
    repair_script = source / "scripts" / "repair_wheel.py"
    if not pyproject.is_file() or not repair_script.is_file():
        raise WheelBuildError("prepared Endstone packaging configuration is incomplete")

    if output("git", "-C", str(source), "status", "--porcelain", "--untracked-files=no"):
        raise WheelBuildError("prepared Endstone source has tracked modifications")

    empty_wheel_directory(output_directory)
    environment = os.environ.copy()
    environment.update(
        {
            "CIBW_BUILD": build_selector,
            "CIBW_BUILD_VERBOSITY": environment.get("CIBW_BUILD_VERBOSITY", "1"),
            "CIBW_CACHE_PATH": environment.get("CIBW_CACHE_PATH", str(ROOT / "build" / ".cibuildwheel-cache")),
        }
    )
    # Clone the committed prepared tree so generated host build state (notably
    # its local Conan cache) cannot leak into the wheel build. On Linux pinned
    # Endstone's pyproject selects its manylinux_2_31 image and invokes
    # scripts/repair_wheel.py as the repair-wheel-command inside cibuildwheel's
    # container, so the raw host-bound wheel remains inside that disposable
    # container; on Windows cibuildwheel builds natively with the same backend
    # and repair command, so the clone keeps host build state out the same way.
    isolated_parent = ROOT / "build"
    isolated_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="endbot-endstone-wheel-", dir=isolated_parent) as temporary:
        isolated_source = Path(temporary) / "source"
        run("git", "clone", "--quiet", "--local", "--no-hardlinks", str(source), str(isolated_source))
        run(
            sys.executable,
            "-m",
            "cibuildwheel",
            ".",
            "--output-dir",
            str(output_directory),
            env=environment,
            cwd=isolated_source,
        )
    repaired_wheel = one_wheel(output_directory, "repaired Endstone")
    check_repaired_wheel(repaired_wheel, expected_version, build_selector)
    print(json.dumps({"build_selector": build_selector, "repaired_wheel": str(repaired_wheel)}, sort_keys=True))
    return repaired_wheel


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "build" / "endstone-patched")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist" / "endstone")
    parser.add_argument(
        "--build-selector",
        default=default_build_selector(),
        help="single cibuildwheel build selector (defaults to this interpreter's CPython wheel for this platform)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        build(args.source.resolve(), args.output_dir.resolve(), args.build_selector)
    except (WheelBuildError, OSError, KeyError, json.JSONDecodeError, subprocess.CalledProcessError) as error:
        print(f"build-endstone-wheel: ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
