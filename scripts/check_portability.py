#!/usr/bin/env python3
"""Fail when tracked/source files leak machine-local repository assumptions."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def source_files() -> list[Path]:
    completed = subprocess.run(
        ("git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"),
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE,
    )
    return [ROOT / entry.decode() for entry in completed.stdout.split(b"\0") if entry]


def main() -> int:
    # Construct repository-specific sentinels so the scanner does not whitelist itself.
    forbidden = (
        "/" + "home" + "/",
        "/" + "Users" + "/",
        "bedrock" + "-fake-player-lab",
        ".." + "/bedrock-headless-player",
        ".." + "/forks",
        ".." + "/tmp",
    )
    failures: list[str] = []
    for path in source_files():
        relative = path.relative_to(ROOT)
        if path.is_symlink():
            target = path.readlink()
            if target.is_absolute() or ".." in target.parts:
                failures.append(f"{relative}: non-portable symlink target {target}")
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, IsADirectoryError):
            continue
        for line_number, line in enumerate(text.splitlines(), start=1):
            for marker in forbidden:
                if marker in line:
                    failures.append(f"{relative}:{line_number}: contains forbidden marker {marker!r}")
    if failures:
        print("portability check failed:", file=sys.stderr)
        print("\n".join(f"  {failure}" for failure in failures), file=sys.stderr)
        return 1
    print(f"portability check passed ({len(source_files())} files inspected)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
