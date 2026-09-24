"""Run `endbot start` as a real supervised process with fake children (tests only).

The end-to-end tests drive this script so the supervisor, its PID files, and
`endbot stop` interact exactly as they do in production. Options travel as
JSON in ``ENDBOT_TEST_START_OPTIONS`` (internal ``run_start`` parameters only:
commands, timeouts, the installed Endstone version string).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from endbot_cli.commands import run_start
from endbot_cli.instance import InstancePaths


def main(argv: list[str]) -> int:
    root = Path(argv[0])
    options = json.loads(os.environ.get("ENDBOT_TEST_START_OPTIONS", "{}"))
    endstone_version = options.pop("installed_endstone_version", None)
    if endstone_version is not None:
        options["installed_endstone_version"] = lambda value=endstone_version: value
    options["stdin"] = None
    options["install_signal_handlers"] = False
    code = run_start(InstancePaths.for_root(root), **options)
    print(f"DRIVER EXIT {code}", flush=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
