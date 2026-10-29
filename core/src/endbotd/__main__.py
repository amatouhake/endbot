"""``python -m endbotd --config state/generated/endbotd.toml``.

Prints one ``endbotd_ready`` JSON line once the plugin link listens, then logs
to stdout, which the supervisor keeps as ``endbotd.log``. It stops when its
stdin closes (the supervisor's stop, or the supervisor dying) or on
SIGINT/SIGTERM, after ending its runs, Group work and mining loops.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import threading
from pathlib import Path

from endbot_core.link import LinkError
from endbotd import __version__
from endbotd.config import EndbotdConfig
from endbotd.service import EndbotService


def _watch_stdin(stop: threading.Event) -> None:
    try:
        while sys.stdin.buffer.read(4096):
            pass
    except (OSError, ValueError):
        pass
    stop.set()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="endbotd", description="Endbot's control plane")
    parser.add_argument("--config", required=True, type=Path, help="path to endbotd.toml")
    arguments = parser.parse_args(argv)
    logging.basicConfig(stream=sys.stdout, level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        force=True)
    log = logging.getLogger("endbotd")
    try:
        config = EndbotdConfig.load(arguments.config)
        service = EndbotService(config).start()
    except (OSError, ValueError, LinkError) as error:
        # Neither the config parser nor load_token puts a token in its message.
        log.error(f"endbotd cannot start: {type(error).__name__}: {error}")
        return 1
    stop = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(signum, lambda *_: stop.set())
        except (OSError, ValueError):
            pass
    threading.Thread(target=_watch_stdin, args=(stop,), name="endbotd-stdin", daemon=True).start()
    host, port = service.link.address
    print(json.dumps({"event": "endbotd_ready", "pid": os.getpid(), "version": __version__,
                      "linkHost": host, "linkPort": port}), flush=True)
    log.info(f"endbotd {__version__} is listening for the Endbot plugin on {host}:{port}")
    while not stop.wait(0.5):
        pass
    log.info("endbotd is stopping")
    service.close()
    log.info("endbotd stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
