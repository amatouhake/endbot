"""``server.properties`` parsing, the safety-invariant check, and a
round-trip-preserving editor.

``edit_properties`` (used by ``endbot setup``, docs/OPERATIONS.md section 6)
changes only the requested keys: comments, blank lines, ordering, key text,
separators, and the line endings of untouched lines are preserved byte for
byte, so editing an existing server's ``server.properties`` never disturbs
anything the operator wrote.

NOTE: the parser and ``REQUIRED_PROPERTIES`` check are duplicated from
``scripts/preflight.py`` on purpose so the CLI package has no repo-level
dependencies and the script keeps working standalone. A later task should make
``scripts/preflight.py`` a thin wrapper (or remove it) and delete this copy.
"""

from __future__ import annotations

import codecs
import re
from collections.abc import Mapping
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


_LINE_ENDING = re.compile(r"(\r\n|\n|\r)$")


def _split_ending(line: str) -> tuple[str, str]:
    match = _LINE_ENDING.search(line)
    if match is None:
        return line, ""
    return line[: match.start()], match.group(1)


def edit_properties(path: Path, updates: Mapping[str, str]) -> list[str]:
    """Set ``updates`` in a ``.properties`` file, preserving everything else.

    Round-trip preserving: a line whose key is not updated is emitted exactly as
    it was read (comments included); an updated line keeps its key text and
    separator and only replaces the value. Missing keys are appended at the end.
    A missing file is created holding just the requested keys. The file is only
    rewritten when something actually changes.

    Returns one message per requested key describing what happened.
    """

    path = Path(path)
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        raw = None
    if raw is None:
        text, had_bom = "", False
    else:
        had_bom = raw.startswith(codecs.BOM_UTF8)
        text = raw.decode("utf-8-sig")
    lines = text.splitlines(keepends=True)

    messages: list[str] = []
    changed: dict[str, str] = {}
    for index, line in enumerate(lines):
        body, ending = _split_ending(line)
        stripped = body.strip()
        if not stripped or stripped.startswith(("#", "!")) or "=" not in body:
            continue
        key = body.split("=", 1)[0].strip()
        if key not in updates or key in changed:
            continue
        wanted = updates[key]
        old = body.split("=", 1)[1]
        if old.strip() == wanted:
            changed[key] = wanted
            messages.append(f"{key}={wanted} already set")
            continue
        leading = re.match(r"\s*", old).group(0)
        lines[index] = body[: body.index("=") + 1] + leading + wanted + ending
        changed[key] = wanted
        messages.append(f"set {key}={wanted} (was {old.strip()!r})")

    appended: list[str] = []
    for key, wanted in updates.items():
        if key in changed:
            continue
        appended.append(f"{key}={wanted}\n")
        messages.append(f"added {key}={wanted} (was missing)")

    new_text = "".join(lines)
    if appended and new_text and not new_text.endswith(("\n", "\r")):
        new_text += "\n"
    new_text += "".join(appended)

    if new_text != text or raw is None:
        payload = new_text.encode("utf-8")
        if had_bom:
            payload = codecs.BOM_UTF8 + payload
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    return messages
