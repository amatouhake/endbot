#!/usr/bin/env python3
"""Fail-closed checks for the server-side portion of Endbot's safety invariants."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REQUIRED_PROPERTIES = {
    "online-mode": "true",
    "allow-cheats": "false",
}


class PreflightError(ValueError):
    pass


def parse_properties(path: Path) -> dict[str, str]:
    properties: dict[str, str] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise PreflightError(f"{path}:{line_number}: expected key=value")
        key, value = (part.strip() for part in line.split("=", 1))
        if not key:
            raise PreflightError(f"{path}:{line_number}: empty property name")
        if key in properties:
            raise PreflightError(f"{path}:{line_number}: duplicate property {key!r}")
        properties[key] = value
    return properties


def verify_server_properties(path: Path) -> list[str]:
    properties = parse_properties(path)
    messages: list[str] = []
    for key, expected in REQUIRED_PROPERTIES.items():
        if key not in properties:
            raise PreflightError(f"required property {key!r} is missing")
        actual = properties[key].lower()
        if actual != expected:
            raise PreflightError(f"{key} must be {expected}, found {properties[key]!r}")
        messages.append(f"PASS {key}={expected}")
    return messages


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("server_properties", type=Path)
    parser.add_argument(
        "--acknowledge-world-state-unverified",
        action="store_true",
        help="limit this invocation to server.properties; world history remains a separate validation gate",
    )
    args = parser.parse_args()

    try:
        messages = verify_server_properties(args.server_properties)
    except (OSError, PreflightError) as error:
        print(f"preflight: FAIL: {error}", file=sys.stderr)
        return 1

    print("\n".join(messages))
    print("UNKNOWN world creative/experiment history: no robust level.dat parser is available", file=sys.stderr)
    print("UNKNOWN actual Xbox achievement unlock: requires a real authenticated client", file=sys.stderr)
    if not args.acknowledge_world_state_unverified:
        print(
            "preflight: INCOMPLETE: pass --acknowledge-world-state-unverified only when a properties-only check is intended",
            file=sys.stderr,
        )
        return 2
    print("PASS scoped server.properties preflight; this is not a world or Xbox achievement certification")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
