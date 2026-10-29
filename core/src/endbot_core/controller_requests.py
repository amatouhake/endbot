"""Console-only execution of correlated host requests; no polling or listener."""

import json
import os
import tempfile
from pathlib import Path
from uuid import UUID


def execute_request(directory: Path, request_id: str, execute) -> dict:
    if str(UUID(request_id)) != request_id:
        raise ValueError("invalid controller request ID")
    pending = directory / f"{request_id}.dispatch.json"
    response = directory / f"{request_id}.response.json"
    if pending.is_symlink() or pending.stat().st_size > 4096:
        raise ValueError("invalid controller request file")
    request = json.loads(pending.read_text(encoding="utf-8"))
    if not isinstance(request, dict) or request.get("version") != 1 or request.get("id") != request_id:
        raise ValueError("invalid controller request envelope")
    session = str(UUID(request["session"]))
    operation, target = request.get("operation"), request.get("target")
    if operation not in {"list", "add", "remove"}:
        raise ValueError("invalid controller operation")
    if operation == "list":
        if target is not None:
            raise ValueError("list takes no target")
    elif not isinstance(target, str) or not target.strip() or not target.isprintable() or len(target) > 256:
        raise ValueError("invalid controller target")
    if response.exists():
        raise ValueError("controller request already completed")
    # Pass the target as one value; it is never reparsed as console text.
    result = {"version": 1, "id": request_id, "session": session, **execute([operation], target)}
    descriptor, temporary = tempfile.mkstemp(dir=directory, suffix=".tmp", prefix=".controller-response-")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(result, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, response)
    finally:
        Path(temporary).unlink(missing_ok=True)
    pending.unlink(missing_ok=True)
    return result
