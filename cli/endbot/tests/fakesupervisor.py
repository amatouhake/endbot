"""Fake supervisor process for `endbot stop` / `endbot console` tests (tests only).

Writes ``state/run/supervisor.pid`` like the real supervisor, then either
exits when ``stop.request`` appears (default) or ignores it (``--ignore-stop``)
so `endbot stop` times out. Usage: ``fakesupervisor.py RUN_DIR [--ignore-stop] [--exit-code N]``.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path


def main(argv: list[str]) -> int:
    run_dir = Path(argv[0])
    ignore_stop = "--ignore-stop" in argv
    exit_code = 0
    if "--exit-code" in argv:
        exit_code = int(argv[argv.index("--exit-code") + 1])
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "supervisor.pid").write_text(f"{os.getpid()}\n", encoding="utf-8")
    print("fake supervisor ready", flush=True)
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        if not ignore_stop and (run_dir / "stop.request").exists():
            (run_dir / "stop.request").unlink()
            print("fake supervisor: stopping", flush=True)
            return exit_code
        time.sleep(0.05)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
