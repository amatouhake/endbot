"""Small filesystem helpers shared by config generation and run-state handling."""

from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path


def atomic_write_text(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` atomically (complete-before-publish).

    The temporary file is created in the target directory so ``os.replace`` is
    a rename on the same filesystem; a reader never observes a partial file.
    """

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=str(path.parent), prefix=f"{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(temporary, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temporary)
        raise
