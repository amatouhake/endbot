"""Correlated controller requests: CLI -> supervisor -> console -> plugin result.

One atomic file per request avoids the legacy console queue's read/truncate race.
The supervisor session prevents an old request being replayed by a later start.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from uuid import UUID, uuid4

from endbot_cli.fsutil import atomic_write_text
from endbot_cli.runstate import RUNNING, inspect_supervisor

SESSION_FILE = "controller-session.json"
REQUEST_DIR = "controller-requests"
# Far beyond any CLI wait; older files have no waiting reader.
STALE_REQUEST_SECONDS = 300.0


def valid_request(value: object, request_id: str, session: str) -> dict:
    if not isinstance(value, dict) or value.get("version") != 1:
        raise ValueError("invalid controller request version")
    if value.get("id") != request_id or value.get("session") != session:
        raise ValueError("controller request belongs to another supervisor session")
    operation, target = value.get("operation"), value.get("target")
    if operation not in {"add", "remove", "list"}:
        raise ValueError("expected add, remove, or list")
    if operation == "list":
        if target is not None:
            raise ValueError("list takes no target")
    elif not isinstance(target, str) or not target.strip() or not target.isprintable() or len(target) > 256:
        raise ValueError("a non-empty printable target of at most 256 characters is required")
    return value


def controller_request(run_dir: Path, operation: str, target: str | None = None, *, timeout: float = 15.0) -> dict:
    state = inspect_supervisor(run_dir)
    if state.status != RUNNING:
        raise ValueError("no live supervisor is running; start the instance first")
    try:
        session = json.loads((run_dir / SESSION_FILE).read_text(encoding="utf-8"))["session"]
        session = str(UUID(session))
    except (OSError, ValueError, TypeError, KeyError) as error:
        raise ValueError(
            "supervisor has no controller result channel; restart with the current Endbot version"
        ) from error
    request_id = str(uuid4())
    request = valid_request(
        {"version": 1, "id": request_id, "session": session, "operation": operation, "target": target},
        request_id,
        session,
    )
    directory = run_dir / REQUEST_DIR
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    pending = directory / f"{request_id}.request.json"
    response = directory / f"{request_id}.response.json"
    atomic_write_text(pending, json.dumps(request) + "\n")
    deadline = time.monotonic() + timeout
    try:
        while True:
            if response.exists():
                if response.is_symlink() or response.stat().st_size > 1024 * 1024:
                    raise ValueError("invalid controller response file")
                result = json.loads(response.read_text(encoding="utf-8"))
                if (
                    not isinstance(result, dict)
                    or result.get("id") != request_id
                    or result.get("session") != session
                    or result.get("version") != 1
                    or not isinstance(result.get("ok"), bool)
                    or not isinstance(result.get("messages"), list)
                    or not result["messages"]
                    or any(not isinstance(message, str) for message in result["messages"])
                ):
                    raise ValueError("controller response did not match the request; result is unknown")
                return result
            current = inspect_supervisor(run_dir)
            if current != state:
                raise ValueError("supervisor stopped/changed; controller result is unknown; inspect before retrying")
            if time.monotonic() >= deadline:
                raise ValueError("timed out; controller result is unknown; inspect controllers list before retrying")
            time.sleep(0.05)
    finally:
        pending.unlink(missing_ok=True)
        response.unlink(missing_ok=True)


def prune_requests(run_dir: Path, *, older_than: float | None = None) -> None:
    """Remove abandoned request/dispatch/response files.

    A CLI that timed out deletes only its own request; a late plugin response
    or an undelivered dispatch file would otherwise stay forever. ``None``
    removes everything (a new supervisor session invalidates all of them).
    """
    directory = run_dir / REQUEST_DIR
    cutoff = None if older_than is None else time.time() - older_than
    try:
        entries = list(directory.iterdir())
    except FileNotFoundError:
        return
    for entry in entries:
        try:
            if entry.is_dir() and not entry.is_symlink():
                continue
            if cutoff is None or entry.lstat().st_mtime < cutoff:
                entry.unlink(missing_ok=True)
        except OSError:
            continue  # Windows may briefly hold a file being replaced.


def dispatch_requests(run_dir: Path, session: str, send) -> None:
    directory = run_dir / REQUEST_DIR
    prune_requests(run_dir, older_than=STALE_REQUEST_SECONDS)
    for pending in sorted(directory.glob("*.request.json"))[:32]:
        request_id = pending.name.removesuffix(".request.json")
        try:
            if str(UUID(request_id)) != request_id or pending.is_symlink() or pending.stat().st_size > 4096:
                pending.unlink(missing_ok=True)
                continue
            valid_request(json.loads(pending.read_text(encoding="utf-8")), request_id, session)
            # Remove from the queue before console dispatch: never retry a mutation.
            dispatched = directory / f"{request_id}.dispatch.json"
            pending.replace(dispatched)
            if not send(f"botcontrollers request {request_id}"):
                raise ValueError("BDS console write failed; result is unknown; inspect before retrying")
        except FileNotFoundError:  # CLI canceled an undispatched request on timeout.
            continue
        except (OSError, ValueError, TypeError) as error:
            result = {
                "version": 1,
                "id": request_id,
                "session": session,
                "ok": False,
                "messages": [f"Endbot: controller request rejected: {error}"],
            }
            atomic_write_text(directory / f"{request_id}.response.json", json.dumps(result) + "\n")
            pending.unlink(missing_ok=True)
            (directory / f"{request_id}.dispatch.json").unlink(missing_ok=True)
