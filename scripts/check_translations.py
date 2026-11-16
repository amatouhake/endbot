#!/usr/bin/env python3
"""Check the Japanese player documents under docs/ja against their English sources.

Each translation starts with ``<!-- source: docs/NAME.md <blob> -->``: the English file it
translates and that file's git blob when it was translated. This script

- fails when a translation has no such line, names a missing source, links to a file
  that does not exist, links to an anchor another docs/ja page does not define, or
  hard-wraps paragraph lines (each break renders as a stray space in Japanese), or
  has a paragraph longer than MAX_PARAGRAPH characters;
- reports a translation whose source has changed since (a warning, or a failure with
  ``--strict``), with the English diff to carry over when the old blob is in history;
- with ``--accept docs/ja/NAME.md``, records the source's current blob after the
  translation has been brought up to date;
- with ``--unwrap docs/ja/NAME.md ...``, joins hard-wrapped paragraph lines.
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
# Lines that begin a new block, so they never continue the line before them.
BLOCK_START = re.compile(r"([-*+] |\d+\. |#|\||<|>|```)")
# About three Japanese sentences: the "short paragraphs" rule for player documents.
MAX_PARAGRAPH = 400
# Full-width punctuation that never takes a space beside it.
CLOSING = "、。，．：；」』）"
# Lines that are not prose, so nothing continues them.
NOT_PROSE = re.compile(r"(#|\||<|```)")


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


def _wide(char: str) -> bool:
    return char >= "⺀"  # CJK, kana, full-width punctuation and forms


def _joinable(line: str) -> bool:
    """A line a following paragraph line would continue: prose, a list item or its continuation."""
    return bool(line.strip()) and not NOT_PROSE.match(line.lstrip())


def unwrap(text: str) -> str:
    """Join hard-wrapped paragraph and list-item lines into one line each.

    A line break inside a Markdown paragraph renders as a space, which shows as a stray gap between two Japanese
    characters. Lines are joined with no space between wide characters and with one space otherwise.
    """
    out: list[str] = []
    fenced = False
    for line in text.split("\n"):
        if line.lstrip().startswith("```"):
            fenced = not fenced
            out.append(line)
            continue
        continues = (
            not fenced and out and _joinable(out[-1]) and line.strip() and not BLOCK_START.match(line.lstrip())
        )
        if continues:
            previous, current = out[-1].rstrip(), line.strip()
            # Look past emphasis and link brackets to the characters a reader sees.
            before, after = previous.rstrip("*_]")[-1:] or " ", current.lstrip("*_[")[:1] or " "
            tight = (_wide(before) and _wide(after)) or before in CLOSING or after in CLOSING
            separator = "" if tight else " "
            out[-1] = previous + separator + current
        else:
            out.append(line)
    return "\n".join(out)


def wrapped_lines(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    if unwrap(text) == text:
        return []
    return [f"{rel(path)}: paragraph lines are hard-wrapped; run --unwrap {rel(path)}"]


def long_paragraphs(path: Path) -> list[str]:
    """Paragraphs and list items over MAX_PARAGRAPH characters (docs/README.md, "Player documents")."""
    failures = []
    fenced = False
    for number, line in enumerate(path.read_text(encoding="utf-8").split("\n"), 1):
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if not fenced and _joinable(line) and len(line.strip()) > MAX_PARAGRAPH:
            failures.append(
                f"{rel(path)}:{number}: paragraph of {len(line.strip())} characters (at most {MAX_PARAGRAPH}); "
                "split it in the English source, or make it a list or table"
            )
    return failures


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
    parser.add_argument("--unwrap", metavar="FILE", nargs="+", help="join hard-wrapped paragraph lines in FILE")
    args = parser.parse_args(argv)

    if args.accept:
        return accept((Path.cwd() / args.accept).resolve())
    if args.unwrap:
        for name in args.unwrap:
            path = (Path.cwd() / name).resolve()
            path.write_text(unwrap(path.read_text(encoding="utf-8")), encoding="utf-8", newline="\n")
            print(f"{rel(path)}: unwrapped")
        return 0

    failures: list[str] = []
    stale: list[str] = []
    for path in sorted(JA_DIR.glob("*.md")):
        item, error = parse(path)
        if item is None:
            failures.append(error or "")
            continue
        failures.extend(broken_links(path))
        failures.extend(wrapped_lines(path))
        failures.extend(long_paragraphs(path))
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
