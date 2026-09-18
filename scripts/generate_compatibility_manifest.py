#!/usr/bin/env python3
"""Generate release compatibility metadata from the repository lock and patch series."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def git_revision() -> str:
    override = os.environ.get("ENDBOT_REVISION")
    if override:
        return override
    completed = subprocess.run(
        ("git", "rev-parse", "HEAD"), cwd=ROOT, check=True, text=True, stdout=subprocess.PIPE
    )
    return completed.stdout.strip()


def patch_revision() -> str:
    patch_root = ROOT / "patches" / "endstone"
    series = [line.strip() for line in (patch_root / "series").read_text().splitlines() if line.strip()]
    digest = hashlib.sha256()
    for name in series:
        digest.update((patch_root / name).read_bytes())
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--endbot-version", default="0.1.0-dev")
    args = parser.parse_args()

    lock = json.loads((ROOT / "endstone.lock").read_text(encoding="utf-8"))
    manifest = {
        "schema_version": 1,
        "endbot": {"version": args.endbot_version, "revision": git_revision()},
        "endstone": {
            "tag": lock["endstone"]["tag"],
            "commit": lock["endstone"]["commit"],
            "package_version": lock["endstone"]["package_version"],
        },
        "bds": lock["bds"],
        "endbot_patch_revision": patch_revision(),
        "tested_safety_conditions": {
            "online_mode": True,
            "allow_cheats": False,
            "experiments_required": False,
            "actual_xbox_achievement_unlock_observed": True,
        },
        "bds_binary_included": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
