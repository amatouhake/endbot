#!/usr/bin/env python3
"""Reject mis-tagged or unpatched Endstone Windows wheels.

Windows counterpart of scripts/inspect_linux_wheel.py. It uses only the
standard library (no LLVM, auditwheel, or readelf on the consumer): the
filename tags, the WHEEL/METADATA records, the locked Endbot-local version
as patch-series provenance, and the presence of the Endstone runtime DLL
plus at least one compiled extension module.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

EXPECTED_PYTHON_TAG = "cp312"
EXPECTED_PLATFORM_TAG = "win_amd64"
RUNTIME_DLL = "endstone/endstone_runtime.dll"


class WheelInspectionError(RuntimeError):
    pass


def locked_package_version() -> str:
    lock = json.loads((ROOT / "endstone.lock").read_text(encoding="utf-8"))
    return lock["endstone"]["package_version"]


def read_dist_info_field(names: list[str], archive: zipfile.ZipFile, filename: str, field: str) -> str:
    candidates = [name for name in names if name.endswith(f".dist-info/{filename}")]
    if len(candidates) != 1:
        raise WheelInspectionError(f"wheel contains {len(candidates)} {filename} files, expected exactly one")
    for line in archive.read(candidates[0]).decode("utf-8").splitlines():
        name, separator, value = line.partition(":")
        if separator and name.strip() == field:
            return value.strip()
    raise WheelInspectionError(f"{filename} does not record {field}")


def inspect(wheel: Path) -> None:
    if not wheel.is_file():
        raise WheelInspectionError(f"wheel does not exist: {wheel}")
    expected_version = locked_package_version()

    match = re.fullmatch(r"endstone-(.+)-([^-]+)-([^-]+)-([^-]+)\.whl", wheel.name)
    if match is None:
        raise WheelInspectionError(f"wheel filename is not a valid endstone wheel name: {wheel.name}")
    version, python_tag, abi_tag, platform_tag = match.groups()
    if version != expected_version:
        raise WheelInspectionError(
            f"wheel version {version} does not match locked package version {expected_version}"
        )
    if python_tag != EXPECTED_PYTHON_TAG or abi_tag != EXPECTED_PYTHON_TAG:
        raise WheelInspectionError(
            f"wheel interpreter tags {python_tag}-{abi_tag} are not the pinned {EXPECTED_PYTHON_TAG}: {wheel.name}"
        )
    if platform_tag != EXPECTED_PLATFORM_TAG:
        raise WheelInspectionError(f"wheel is not tagged {EXPECTED_PLATFORM_TAG}: {wheel.name}")
    if "linux" in wheel.name or "manylinux_" in wheel.name:
        raise WheelInspectionError(f"Windows wheel carries a Linux platform tag: {wheel.name}")

    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        recorded_tag = read_dist_info_field(names, archive, "WHEEL", "Tag")
        if recorded_tag != f"{EXPECTED_PYTHON_TAG}-{EXPECTED_PYTHON_TAG}-{EXPECTED_PLATFORM_TAG}":
            raise WheelInspectionError(f"WHEEL file records unexpected tag: {recorded_tag}")
        recorded_version = read_dist_info_field(names, archive, "METADATA", "Version")
        # The locked Endbot-local version (v<upstream>+endbot.<n> local tag
        # applied by scripts/prepare_endstone.py) is the patch-series
        # provenance, the same provenance the Linux inspection path enforces
        # through the build script's locked-version filename assertion.
        if recorded_version != expected_version or "+endbot." not in recorded_version:
            raise WheelInspectionError(
                f"METADATA version {recorded_version} does not match locked package version {expected_version}"
            )
        if RUNTIME_DLL not in names:
            raise WheelInspectionError(f"wheel does not contain the Endstone runtime DLL: {RUNTIME_DLL}")
        extensions = sorted(
            name for name in names if name.startswith("endstone/") and name.endswith(".pyd")
        )
        if not extensions:
            raise WheelInspectionError("wheel contains no compiled endstone extension modules (.pyd)")

    print(f"wheel-inspection: {wheel.name} version {recorded_version} tag {recorded_tag}")
    print(f"wheel-inspection: runtime {RUNTIME_DLL} and {len(extensions)} extension modules present")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path)
    args = parser.parse_args()
    try:
        inspect(args.wheel.resolve())
    except (WheelInspectionError, OSError, KeyError, json.JSONDecodeError, zipfile.BadZipFile) as error:
        print(f"wheel-inspection: ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
