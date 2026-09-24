"""``server.properties`` parsing and the safety-invariant check.

NOTE: the parser and ``REQUIRED_PROPERTIES`` check are duplicated from
``scripts/preflight.py`` on purpose so the CLI package has no repo-level
dependencies and the script keeps working standalone. A later task should make
``scripts/preflight.py`` a thin wrapper (or remove it) and delete this copy.
"""

from __future__ import annotations

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
