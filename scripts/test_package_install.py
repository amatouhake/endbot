#!/usr/bin/env python3
"""Build-contract test for installing patched Endstone with the Endbot plugin."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]


class InstallTestError(RuntimeError):
    pass


def run(*args: str, capture: bool = False) -> str:
    completed = subprocess.run(
        args,
        check=True,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.STDOUT if capture else None,
    )
    return completed.stdout if capture else ""


def one_wheel(directory: Path, distribution: str) -> Path:
    wheels = sorted(directory.resolve().glob(f"{distribution}-*.whl"))
    if len(wheels) != 1:
        raise InstallTestError(f"expected exactly one {distribution} wheel in {directory}, found {len(wheels)}")
    return wheels[0]


def verify_install(endstone_wheel: Path, plugin_wheel: Path, expected_version: str, report_path: Path) -> None:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    installed = {item["metadata"]["name"]: item for item in report["install"]}
    if set(installed) != {"endstone", "endstone-endbot"}:
        raise InstallTestError(f"offline paired install contained unexpected distributions: {sorted(installed)}")

    expected_wheels = {
        "endstone": endstone_wheel.resolve(),
        "endstone-endbot": plugin_wheel.resolve(),
    }
    for name, expected_wheel in expected_wheels.items():
        source_url = installed[name]["download_info"]["url"]
        source_path = Path(unquote(urlparse(source_url).path)).resolve()
        if source_path != expected_wheel:
            raise InstallTestError(f"{name} was installed from {source_path}, expected {expected_wheel}")

    if importlib.metadata.version("endstone") != expected_version:
        raise InstallTestError("installed Endstone version does not match endstone.lock")
    if "+endbot." not in expected_version:
        raise InstallTestError("installed Endstone version is not Endbot-local")

    plugin_distribution = importlib.metadata.distribution("endstone-endbot")
    requirements = plugin_distribution.requires or []
    if f"endstone=={expected_version}" not in requirements:
        raise InstallTestError("installed plugin does not require the exact patched Endstone version")
    entry_points = [entry for entry in plugin_distribution.entry_points if entry.group == "endstone" and entry.name == "endbot"]
    if len(entry_points) != 1:
        raise InstallTestError("installed plugin does not expose exactly one endbot entry point")
    plugin_class = entry_points[0].load()
    if plugin_class.__module__ != "endstone_endbot.plugin":
        raise InstallTestError("Endbot plugin entry point resolved to an unexpected object")

    print(json.dumps({"endstone": expected_version, "entry_point": entry_points[0].value}, sort_keys=True))


def integration_test(endstone_wheel_dir: Path, plugin_wheel_dir: Path) -> None:
    lock = json.loads((ROOT / "endstone.lock").read_text(encoding="utf-8"))
    expected_version = lock["endstone"]["package_version"]
    endstone_wheel = one_wheel(endstone_wheel_dir, "endstone")
    plugin_wheel = one_wheel(plugin_wheel_dir, "endstone_endbot")

    with tempfile.TemporaryDirectory(prefix="endbot-package-install-") as temporary:
        test_root = Path(temporary)
        environment = test_root / "venv"
        report_path = test_root / "paired-install.json"
        run(sys.executable, "-m", "venv", str(environment))
        python = environment / "bin" / "python"

        # Bootstrap only third-party runtime dependencies from the explicit patched
        # wheel, then remove Endstone so the paired installation starts absent.
        run(str(python), "-m", "pip", "install", str(endstone_wheel))
        run(str(python), "-m", "pip", "uninstall", "--yes", "endstone")

        # This is the contract under test: no package index is available and both
        # distributions must come from the two release-candidate wheels.
        output = run(
            str(python),
            "-m",
            "pip",
            "install",
            "--no-index",
            "--no-deps",
            "--report",
            str(report_path),
            str(endstone_wheel),
            str(plugin_wheel),
            capture=True,
        )
        print(output, end="")
        run(str(python), "-m", "pip", "check")
        run(
            str(python),
            str(Path(__file__).resolve()),
            "--verify-installed",
            "--endstone-wheel",
            str(endstone_wheel),
            "--plugin-wheel",
            str(plugin_wheel),
            "--expected-version",
            expected_version,
            "--report",
            str(report_path),
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endstone-wheel-dir", type=Path)
    parser.add_argument("--plugin-wheel-dir", type=Path)
    parser.add_argument("--verify-installed", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--endstone-wheel", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--plugin-wheel", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--expected-version", help=argparse.SUPPRESS)
    parser.add_argument("--report", type=Path, help=argparse.SUPPRESS)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.verify_installed:
            if not all((args.endstone_wheel, args.plugin_wheel, args.expected_version, args.report)):
                raise InstallTestError("installed verification arguments are incomplete")
            verify_install(args.endstone_wheel, args.plugin_wheel, args.expected_version, args.report)
        else:
            if not args.endstone_wheel_dir or not args.plugin_wheel_dir:
                raise InstallTestError("both wheel directories are required")
            integration_test(args.endstone_wheel_dir, args.plugin_wheel_dir)
    except (InstallTestError, OSError, subprocess.CalledProcessError, json.JSONDecodeError) as error:
        print(f"package-install-test: ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
