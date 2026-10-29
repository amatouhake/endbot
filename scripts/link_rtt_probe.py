#!/usr/bin/env python3
"""Measure the endbotd link round trip under a Group-sized mining load (docs/ENDBOTD.md P5).

endbotd renews every mining loop with one world call every 200 ms. With
``--mode batch`` (the default) one ``resolve_mining_batch`` call carries every
loop of a poll; ``--mode per-loop`` keeps the earlier one ``resolve_mining``
call per loop. This runs the real link (``LinkServer`` and the plugin's
``PluginLink`` over loopback TCP) with a stand-in server thread that runs
queued tasks once per 50 ms tick, as the BDS scheduler does, and a world whose
raycast answers at once. It reports each call's round trip and each poll's
total, so the cost of the link itself and the wait for the next tick are
visible apart (``--tick-ms 0`` runs tasks at once). No BDS, runtime or player
is involved.
"""

from __future__ import annotations

import argparse
import json
import queue
import secrets
import statistics
import sys
import tempfile
import threading
import time
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "core" / "src"), str(ROOT / "plugin" / "endbot" / "src")]

from endbot_core.link import LinkServer, resolve_mining_batch
from endstone_endbot.dispatch import ServerThreadBridge
from endstone_endbot.link_adapter import PluginLink


class TickThread:
    """Runs scheduled tasks in batches once per tick, like ``scheduler.run_task`` on BDS."""

    def __init__(self, tick_seconds: float) -> None:
        self.tick = tick_seconds
        self.tasks: queue.Queue = queue.Queue()
        self.running = True
        threading.Thread(target=self._run, daemon=True).start()

    def schedule(self, callback) -> None:
        self.tasks.put(callback)

    def _run(self) -> None:
        while self.running:
            if self.tick:
                time.sleep(self.tick)
                while True:
                    try:
                        self.tasks.get_nowait()()
                    except queue.Empty:
                        break
            else:
                self.tasks.get()()


class World:
    def reachable_block(self, identity_id, block_type, reach):
        return [1, 64, 2]

    def resolve_mining(self, identity_id, parameters, sender):
        return {"blockPosition": [1, 64, 2], "face": 1, "identityId": identity_id}

    def resolve_mining_batch(self, requests):
        return resolve_mining_batch(self, requests)


class Server:
    online_players = ()
    command_sender = None

    def get_player(self, unique_id):
        return None


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(fraction * len(ordered)))]


def measure(loops: int, seconds: float, tick_ms: float, mode: str) -> dict:
    token = secrets.token_urlsafe(32)
    with tempfile.TemporaryDirectory() as directory:
        token_file = Path(directory) / "endbotd.token"
        token_file.write_text(token, encoding="utf-8")
        endbotd = LinkServer("127.0.0.1", 0, token, version="probe", config={}, on_submit=lambda submission: {},
                             world_timeout=8.0).start()
        ticks = TickThread(tick_ms / 1000)
        bridge = ServerThreadBridge(ticks.schedule)
        plugin = PluginLink(host="127.0.0.1", port=endbotd.address[1], token_file=token_file, plugin_version="probe",
                            plugin=object(), server=Server(), bridge=bridge, world=World(),
                            resolve_sender=lambda principal: None, call_deadline=5.0, retry=(0.05, 0.2)).start()
        try:
            deadline = time.monotonic() + 10
            while not (plugin.connected and endbotd.connected):
                if time.monotonic() > deadline:
                    raise RuntimeError("the link did not open")
                time.sleep(0.01)
            identities = [str(UUID(int=index + 1)) for index in range(loops)]
            calls, polls = [], []
            end = time.monotonic() + seconds
            while time.monotonic() < end:
                started = time.perf_counter()
                if mode == "batch":
                    # One poll: every loop renews in one world call, as poll_mining does.
                    before = time.perf_counter()
                    endbotd.world.resolve_mining_batch(
                        [{"identityId": identity, "blockType": None} for identity in identities]
                    )
                    calls.append((time.perf_counter() - before) * 1000)
                else:
                    # One poll: every loop renews in turn, as poll_mining did before batching.
                    for identity in identities:
                        before = time.perf_counter()
                        endbotd.world.resolve_mining(identity, {"mode": "continuous"}, None)
                        calls.append((time.perf_counter() - before) * 1000)
                polls.append((time.perf_counter() - started) * 1000)
                time.sleep(max(0.0, 0.2 - (time.perf_counter() - started)))
        finally:
            plugin.stop()
            endbotd.stop()
            bridge.close()
            ticks.running = False
    return {
        "mode": mode, "loops": loops, "tickMs": tick_ms, "seconds": seconds, "calls": len(calls), "polls": len(polls),
        "callMs": {"p50": round(statistics.median(calls), 2), "p95": round(percentile(calls, 0.95), 2),
                   "max": round(max(calls), 2)},
        "pollMs": {"p50": round(statistics.median(polls), 2), "p95": round(percentile(polls, 0.95), 2),
                   "max": round(max(polls), 2)},
        # A poll that takes longer than its 200 ms period delays the next renewal.
        "pollsOver200Ms": sum(1 for value in polls if value > 200),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--loops", type=int, nargs="+", default=[1, 8, 16, 32])
    parser.add_argument("--seconds", type=float, default=10.0)
    parser.add_argument("--tick-ms", type=float, nargs="+", default=[0.0, 50.0])
    parser.add_argument("--mode", choices=("batch", "per-loop"), default="batch")
    args = parser.parse_args()
    results = [measure(loops, args.seconds, tick, args.mode)
               for tick in args.tick_ms for loops in args.loops]
    print(json.dumps({"python": sys.version.split()[0], "platform": sys.platform, "results": results}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
