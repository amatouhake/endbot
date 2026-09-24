#!/usr/bin/env python3
"""Set the Endbot release version in every package manifest at once.

The runtime (npm) takes the version as written, for example ``0.1.0-rc.2``;
the Python plugin and CLI take its PEP 440 form, ``0.1.0rc2``.
``scripts/check_release_version.py`` verifies the result.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from packaging.version import Version

ROOT = Path(__file__).resolve().parents[1]
PYTHON_PACKAGES = (
    ("plugin/endbot/pyproject.toml", "plugin/endbot/src/endstone_endbot/__init__.py"),
    ("cli/endbot/pyproject.toml", "cli/endbot/src/endbot_cli/__init__.py"),
)
NPM_VERSION = re.compile(r"^\d+\.\d+\.\d+(-(alpha|beta|rc)\.\d+)?$")


def replace_once(path: Path, pattern: str, replacement: str) -> None:
    text = path.read_text(encoding="utf-8")
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.MULTILINE)
    if count != 1:
        raise ValueError(f"{path}: expected one match for {pattern!r}")
    path.write_text(updated, encoding="utf-8", newline="\n")


def set_version(version: str) -> str:
    if not NPM_VERSION.match(version):
        raise ValueError(f"{version!r} is not a supported release version (X.Y.Z or X.Y.Z-rc.N)")
    python_version = str(Version(version))

    package_path = ROOT / "runtime/package.json"
    package = json.loads(package_path.read_text(encoding="utf-8"))
    package["version"] = version
    package_path.write_text(json.dumps(package, indent=2) + "\n", encoding="utf-8", newline="\n")

    lock_path = ROOT / "runtime/package-lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock["version"] = version
    lock["packages"][""]["version"] = version
    lock_path.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8", newline="\n")

    for pyproject, module in PYTHON_PACKAGES:
        replace_once(ROOT / pyproject, r'^version = "[^"]+"$', f'version = "{python_version}"')
        replace_once(ROOT / module, r'^__version__ = "[^"]+"$', f'__version__ = "{python_version}"')
    return python_version


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", help="npm-form release version, e.g. 0.1.0 or 0.1.0-rc.2")
    args = parser.parse_args()
    try:
        python_version = set_version(args.version)
    except ValueError as error:
        parser.error(str(error))
    print(f"set runtime {args.version}, Python packages {python_version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
