"""The run journal, ``state/run/endbotd-runs.json`` (docs/ENDBOTD.md P6).

endbotd keeps one atomic record of the script runs alive now: each run's id,
owner, script name and the input channels it owns, with the Bot's UUID pin. A
clean stop removes the file. After an unclean exit the next start reads it,
stops those channels on the runtime and tells each owner; runs are never
resumed.

The file is bounded (runs, channels per run, text lengths, bytes read), and a
missing, unreadable or corrupt journal is logged and treated as empty.
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Callable
from pathlib import Path

VERSION = 1
MAX_RUNS = 64
MAX_CHANNELS = 256
MAX_TEXT = 128
MAX_BYTES = 1024 * 1024
CHANNELS = frozenset({"move", "jump", "attack", "use", "sprint", "sneak", "mine"})


def _text(value: object) -> str | None:
    return value[:MAX_TEXT] if isinstance(value, str) and value else None


def _channel(item: object) -> dict | None:
    if not isinstance(item, dict) or not _text(item.get("identityId")) or item.get("channel") not in CHANNELS:
        return None
    return {"identityId": _text(item["identityId"]), "channel": item["channel"], "name": _text(item.get("name")),
            "loopId": _text(item.get("loopId"))}


def _owner(value: object) -> dict:
    value = value if isinstance(value, dict) else {}
    language = value.get("language")
    return {"playerId": _text(value.get("playerId")), "xuid": _text(value.get("xuid")),
            "name": _text(value.get("name")), "language": language if language in {"en", "ja"} else "en"}


def _run(item: object) -> dict | None:
    if not isinstance(item, dict) or not _text(item.get("run")) or not _text(item.get("name")):
        return None
    channels = item.get("channels")
    channels = [channel for channel in map(_channel, channels[:MAX_CHANNELS] if isinstance(channels, list) else ())
                if channel is not None]
    return {"run": _text(item["run"]), "name": _text(item["name"]), "owner": _owner(item.get("owner")),
            "channels": channels}


class RunJournal:
    def __init__(self, path: Path, log: Callable[[str, str], None]) -> None:
        self.path = Path(path)
        self._log = log
        self._lock = threading.Lock()
        self._written: bytes | None = None
        self._failing = False
        self._closed = False

    def load(self) -> list[dict]:
        """The runs a previous endbotd left; never raises."""
        try:
            with self.path.open("rb") as handle:
                data = handle.read(MAX_BYTES + 1)
        except FileNotFoundError:
            return []
        except OSError as error:
            self._log("warning", f"Run journal {self.path} could not be read ({error}); ignoring it")
            return []
        try:
            if len(data) > MAX_BYTES:
                raise ValueError(f"larger than {MAX_BYTES} bytes")
            document = json.loads(data.decode("utf-8"))
            if not isinstance(document, dict) or document.get("version") != VERSION:
                raise ValueError("not a version 1 run journal")
            if not isinstance(document.get("runs"), list):
                raise ValueError("runs must be a list")  # noqa: TRY004 - a corrupt file, like the others
        except (ValueError, UnicodeDecodeError) as error:
            corrupt = self.path.with_name(self.path.name + ".corrupt")
            try:
                os.replace(self.path, corrupt)
                kept = f"; kept as {corrupt.name}"
            except OSError:
                kept = ""
            self._log("warning", f"Run journal {self.path} is corrupt ({error}){kept}; no runs are recovered")
            return []
        runs = [run for run in map(_run, document["runs"][:MAX_RUNS]) if run is not None]
        if len(runs) != len(document["runs"]):
            self._log("warning", f"Run journal {self.path}: {len(document['runs']) - len(runs)} entries skipped")
        return runs

    def write(self, runs: list[dict], *, final: bool = False) -> None:
        """Record ``runs`` atomically, or remove the journal when there are none; never raises.

        After a ``final`` write (endbotd's clean stop) later writes are ignored.
        """
        runs = [run for run in map(_run, runs[:MAX_RUNS]) if run is not None]
        data = json.dumps({"version": VERSION, "runs": runs}, sort_keys=True).encode("utf-8") if runs else b""
        with self._lock:
            if self._closed:
                return
            self._closed = final
            if data == self._written:
                return
            try:
                if data:
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    temporary = self.path.with_name(self.path.name + ".tmp")
                    with temporary.open("wb") as handle:
                        handle.write(data)
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.replace(temporary, self.path)
                else:
                    self.path.unlink(missing_ok=True)
            except OSError as error:
                if not self._failing:
                    self._log("warning", f"Run journal {self.path} could not be written: {error}")
                self._failing = True
                return
            self._written, self._failing = data, False
