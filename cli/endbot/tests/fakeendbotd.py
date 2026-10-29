"""Fake endbotd child for supervisor tests (tests only).

Impersonates ``python -m endbotd``: prints the ``endbotd_ready`` line and exits
when its stdin closes, as endbotd does. Environment knobs (tests only):

- ``FAKE_ENDBOTD_STARTS_FILE``: append one line per start (restart counting);
- ``FAKE_ENDBOTD_CRASH_ON_START``: ``all`` or a 1-based start number: that run
  exits with code 3 shortly after becoming ready;
- ``FAKE_ENDBOTD_NEVER_READY``: never print the ready line.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path


def _start_number() -> int:
    starts_file = os.environ.get("FAKE_ENDBOTD_STARTS_FILE")
    if not starts_file:
        return 1
    path = Path(starts_file)
    existing = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    with open(path, "a", encoding="utf-8", newline="\n") as handle:
        handle.write("start\n")
    return len(existing) + 1


def main() -> int:
    start = _start_number()
    crash = os.environ.get("FAKE_ENDBOTD_CRASH_ON_START", "")
    if os.environ.get("FAKE_ENDBOTD_NEVER_READY"):
        time.sleep(60)
        return 0
    print(json.dumps({"event": "endbotd_ready", "pid": os.getpid()}), flush=True)
    if crash == "all" or (crash.isdecimal() and int(crash) == start):
        time.sleep(0.05)
        return 3
    sys.stdin.buffer.read()
    print("endbotd stopped", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
