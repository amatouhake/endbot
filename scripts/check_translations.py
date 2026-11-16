#!/usr/bin/env python3
"""Check the Japanese player documents under docs/ja against their English sources.

Each translation starts with ``<!-- source: docs/NAME.md <blob> -->``: the English file it
translates and that file's git blob when it was translated. This script

- fails when a translation has no such line, names a missing source, links to a file
  that does not exist, or links to an anchor another docs/ja page does not define;
- reports a translation whose source has changed since (a warning, or a failure with
  ``--strict``), with the English diff to carry over when the old blob is in history;
- with ``--accept docs/ja/NAME.md``, records the source's current blob after the
  translation has been brought up to date.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JA_DIR = ROOT / "docs" / "ja"
HEADER = re.compile(r"^<!-- source: (?P<source>\S+) (?P<blob>[0-9a-f]{40}) -->$")
LINK = re.compile(r"\]\((?P<target>[^)\s]+)\)")
ANCHOR = re.compile(r'<a id="([^"]+)"></a>')


@dataclass
class Translation:
    path: Path
    source: Path
    blob: str


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", *args), cwd=ROOT, check=check, capture_output=True, text=True, encoding="utf-8"
    )


def current_blob(source: Path) -> str:
    return git("hash-object", source.relative_to(ROOT).as_posix()).stdout.strip()


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def parse(path: Path) -> tuple[Translation | None, str | None]:
    first = path.read_text(encoding="utf-8").splitlines()[:1]
    match = HEADER.match(first[0]) if first else None
    if match is None:
        return None, f"{rel(path)}: first line must be <!-- source: docs/NAME.md <blob> -->"
    source = ROOT / match["source"]
    if not source.is_file():
        return None, f"{rel(path)}: source {match['source']} does not exist"
    return Translation(path, source, match["blob"]), None


def anchors(path: Path) -> set[str]:
    return set(ANCHOR.findall(path.read_text(encoding="utf-8")))


def broken_links(path: Path) -> list[str]:
    """Missing files anywhere, and missing anchors inside docs/ja (translations carry explicit ``<a id>``)."""
    failures = []
    text = path.read_text(encoding="utf-8")
    for match in LINK.finditer(text):
        target = match["target"]
        if re.match(r"^[a-z]+:", target):
            continue
        file_part, _, anchor = target.partition("#")
        linked = (path.parent / file_part).resolve() if file_part else path.resolve()
        if not linked.exists():
            failures.append(f"{rel(path)}: link to missing file {target}")
        elif anchor and linked.parent == JA_DIR.resolve() and anchor not in anchors(linked):
            failures.append(f"{rel(path)}: link to missing anchor {target}")
    return failures


def stale_report(item: Translation) -> str:
    lines = [f"{rel(item.path)}: {rel(item.source)} changed since it was translated"]
    old = git("cat-file", "blob", item.blob, check=False)
    if old.returncode == 0:
        with tempfile.TemporaryDirectory() as tmp:
            translated = Path(tmp) / "translated.md"
            translated.write_text(old.stdout, encoding="utf-8", newline="")
            diff = git("diff", "--no-index", "--no-color", str(translated), rel(item.source), check=False).stdout
        lines.append(diff.rstrip())
    else:
        lines.append(f"  (blob {item.blob[:12]} is not in this clone's history; fetch more to see the diff)")
    return "\n".join(lines)


def accept(path: Path) -> int:
    item, error = parse(path)
    if item is None:
        print(error, file=sys.stderr)
        return 1
    now = current_blob(item.source)
    text = path.read_text(encoding="utf-8")
    rest = text.partition("\n")[2]
    path.write_text(f"<!-- source: {rel(item.source)} {now} -->\n{rest}", encoding="utf-8", newline="\n")
    print(f"{rel(path)}: recorded {rel(item.source)} at {now[:12]}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--strict", action="store_true", help="fail when a translation is out of date")
    parser.add_argument("--accept", metavar="FILE", help="record the source's current blob in FILE")
    args = parser.parse_args(argv)

    if args.accept:
        return accept((Path.cwd() / args.accept).resolve())

    failures: list[str] = []
    stale: list[str] = []
    for path in sorted(JA_DIR.glob("*.md")):
        item, error = parse(path)
        if item is None:
            failures.append(error or "")
            continue
        failures.extend(broken_links(path))
        now = current_blob(item.source)
        if now != item.blob:
            stale.append(stale_report(item))

    for report in stale:
        if os.environ.get("GITHUB_ACTIONS") == "true":
            print(f"::warning::{report.splitlines()[0]}")
        print(report)
    for failure in failures:
        print(failure, file=sys.stderr)
    if failures or (args.strict and stale):
        return 1
    if not stale:
        print(f"translations: {len(list(JA_DIR.glob('*.md')))} up to date")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
