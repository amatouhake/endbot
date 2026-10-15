"""Fake Endstone/BDS child for supervisor tests (tests only).

Accepts exactly the non-interactive launch shape and rejects the forbidden
``-y`` flag (exit 99) or a missing ``--no-interactive`` (exit 98), so tests
pin the documented command line. Reads its stdin like a console: every line is
echoed to stdout as ``[console] <line>`` and ``stop`` exits cleanly. Environment
knobs (tests only):

- ``FAKE_BDS_IGNORE_STOP``: keep running when `stop` arrives (escalation tests);
- ``FAKE_BDS_EXIT_AFTER`` / ``FAKE_BDS_EXIT_CODE``: self-exit after N seconds;
- ``FAKE_BDS_PID_FILE``: write the child PID for liveness assertions.
"""

from __future__ import annotations

import json
import os
import queue
import sys
import threading
import time
from pathlib import Path


def _stdin_reader(lines: queue.Queue) -> None:
    try:
        for line in sys.stdin:
            lines.put(line.rstrip("\r\n"))
    except (OSError, ValueError):
        pass


def main(argv: list[str]) -> int:
    if "-y" in argv or "--yes" in argv:
        print("fake BDS: refusing -y (never let Endstone act implicitly)", file=sys.stderr)
        return 99
    if "--no-interactive" not in argv:
        print("fake BDS: --no-interactive is required", file=sys.stderr)
        return 98
    pid_file = os.environ.get("FAKE_BDS_PID_FILE")
    if pid_file:
        Path(pid_file).write_text(str(os.getpid()), encoding="utf-8")
    ignore_stop = bool(os.environ.get("FAKE_BDS_IGNORE_STOP"))
    exit_after = os.environ.get("FAKE_BDS_EXIT_AFTER")
    deadline = time.monotonic() + float(exit_after) if exit_after else None
    exit_code = int(os.environ.get("FAKE_BDS_EXIT_CODE", "0"))
    print("fake BDS ready", flush=True)
    lines: queue.Queue = queue.Queue()
    threading.Thread(target=_stdin_reader, args=(lines,), daemon=True).start()
    while True:
        try:
            line = lines.get(timeout=0.05)
        except queue.Empty:
            if deadline is not None and time.monotonic() >= deadline:
                print(f"fake BDS: self-exiting with code {exit_code}", flush=True)
                return exit_code
            continue
        if line == "stop" and not ignore_stop:
            print("fake BDS: stopping", flush=True)
            return 0
        request_dir = os.environ.get("FAKE_CONTROLLER_REQUEST_DIR")
        if request_dir and line.startswith("botcontrollers request "):
            request_id = line.split()[-1]
            directory = Path(request_dir)
            pending = directory / f"{request_id}.dispatch.json"
            request = json.loads(pending.read_text(encoding="utf-8"))
            result = {
                "version": 1,
                "id": request_id,
                "session": request["session"],
                "ok": request["target"] != "Missing",
                "messages": [f"result: {request['target']}"],
                "entries": [],
            }
            temporary = directory / f"{request_id}.tmp"
            temporary.write_text(json.dumps(result), encoding="utf-8")
            temporary.replace(directory / f"{request_id}.response.json")
            pending.unlink()
        print(f"[console] {line}", flush=True)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
