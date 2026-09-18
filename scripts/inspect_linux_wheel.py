#!/usr/bin/env python3
"""Reject unrepaired or build-host-bound Endstone Linux wheels."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

FORBIDDEN_NEEDED = {"libc++.so.1", "libc++abi.so.1", "libunwind.so.1"}


class WheelInspectionError(RuntimeError):
    pass


def run(*args: str) -> str:
    completed = subprocess.run(args, check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return completed.stdout


def inspect(wheel: Path) -> None:
    if not wheel.is_file():
        raise WheelInspectionError(f"wheel does not exist: {wheel}")
    if "manylinux_" not in wheel.name or "-linux_" in wheel.name:
        raise WheelInspectionError(f"wheel is not repaired with a manylinux platform tag: {wheel.name}")

    auditwheel_output = run("auditwheel", "show", str(wheel))
    if "consistent with the following platform tag" not in auditwheel_output:
        raise WheelInspectionError("auditwheel did not report a compatible platform tag")

    inspected = 0
    with tempfile.TemporaryDirectory(prefix="endbot-wheel-inspection-") as temporary:
        root = Path(temporary)
        with zipfile.ZipFile(wheel) as archive:
            archive.extractall(root)
        binaries = []
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            with path.open("rb") as stream:
                if stream.read(4) == b"\x7fELF":
                    binaries.append(path)
        for binary in binaries:
            dynamic = run("readelf", "-d", str(binary))
            if "Dynamic section" not in dynamic:
                continue
            inspected += 1
            needed = set(re.findall(r"\(NEEDED\).*?\[(.*?)\]", dynamic))
            unsafe = needed & FORBIDDEN_NEEDED
            if unsafe:
                raise WheelInspectionError(
                    f"{binary.relative_to(root)} retains unvendored dependencies: {', '.join(sorted(unsafe))}"
                )
            for loader_path in re.findall(r"\((?:RUNPATH|RPATH)\).*?\[(.*?)\]", dynamic):
                entries = [entry for entry in loader_path.split(":") if entry]
                invalid = [
                    entry
                    for entry in entries
                    if entry not in {"$ORIGIN", "${ORIGIN}"}
                    and not entry.startswith(("$ORIGIN/", "${ORIGIN}/"))
                ]
                if invalid:
                    raise WheelInspectionError(
                        f"{binary.relative_to(root)} has non-relative loader paths: {', '.join(invalid)}"
                    )
    if not inspected:
        raise WheelInspectionError("wheel contains no inspectable ELF objects")

    print(auditwheel_output, end="" if auditwheel_output.endswith("\n") else "\n")
    print(f"wheel-inspection: inspected {inspected} ELF objects; loader dependencies are distributable")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path)
    args = parser.parse_args()
    try:
        inspect(args.wheel.resolve())
    except (WheelInspectionError, OSError, subprocess.CalledProcessError, zipfile.BadZipFile) as error:
        print(f"wheel-inspection: ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
