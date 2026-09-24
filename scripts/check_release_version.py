#!/usr/bin/env python3
"""Reject a release candidate whose package versions disagree."""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path

import tomllib
from packaging.version import InvalidVersion, Version

ROOT = Path(__file__).resolve().parents[1]


def plugin_module_version() -> str:
    module = ast.parse(
        (ROOT / "plugin/endbot/src/endstone_endbot/__init__.py").read_text(encoding="utf-8")
    )
    for statement in module.body:
        if not isinstance(statement, ast.Assign):
            continue
        if any(isinstance(target, ast.Name) and target.id == "__version__" for target in statement.targets):
            value = ast.literal_eval(statement.value)
            if isinstance(value, str):
                return value
    raise ValueError("plugin module does not define a string __version__")


def validate_release_version(candidate: str) -> None:
    runtime = json.loads((ROOT / "runtime/package.json").read_text(encoding="utf-8"))["version"]
    plugin = tomllib.loads((ROOT / "plugin/endbot/pyproject.toml").read_text(encoding="utf-8"))["project"][
        "version"
    ]
    cli = tomllib.loads((ROOT / "cli/endbot/pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    module = plugin_module_version()

    errors: list[str] = []
    if candidate != runtime:
        errors.append(f"candidate {candidate!r} does not match runtime package version {runtime!r}")
    try:
        if Version(candidate) != Version(plugin):
            errors.append(f"candidate {candidate!r} does not match Python plugin version {plugin!r}")
    except InvalidVersion as error:
        errors.append(f"candidate version is not PEP 440 compatible: {error}")
    try:
        if Version(candidate) != Version(cli):
            errors.append(f"candidate {candidate!r} does not match CLI package version {cli!r}")
    except InvalidVersion as error:
        errors.append(f"CLI version is not PEP 440 compatible: {error}")
    if module != plugin:
        errors.append(f"plugin module version {module!r} does not match package version {plugin!r}")
    if errors:
        raise ValueError("; ".join(errors))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidate")
    args = parser.parse_args()
    try:
        validate_release_version(args.candidate)
    except ValueError as error:
        parser.error(str(error))
    print(f"release version contract passed: {args.candidate}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
