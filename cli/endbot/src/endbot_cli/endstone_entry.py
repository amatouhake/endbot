"""Run Endstone's own ``python -m endstone`` entry point from a relocated interpreter.

python-build-standalone (the bundle's private CPython) keeps its build-time
``sysconfig`` paths, so ``LIBDIR`` names ``/install/lib`` instead of the
bundle's ``python/lib``. On Linux, Endstone sets ``LD_LIBRARY_PATH`` for
``bedrock_server`` from ``LIBDIR``; with the stale value the injected runtime
cannot load ``libpython3.x.so`` and BDS exits with code 127. This entry point
points ``LIBDIR`` at the interpreter's real library directory, then hands over
to Endstone unchanged. Nothing is patched when the configured directory already
holds the shared library (system or developer interpreters).
"""

from __future__ import annotations

import runpy
import sys
import sysconfig
from pathlib import Path


def shared_library_name() -> str:
    return f"libpython{sys.version_info.major}.{sys.version_info.minor}.so.1.0"


def relocated_libdir(configured: str | None, base_prefix: str, library: str) -> str | None:
    """Return the directory to use for ``LIBDIR``, or None when no change is needed."""

    if configured and (Path(configured) / library).exists():
        return None
    candidate = Path(base_prefix) / "lib"
    if (candidate / library).exists():
        return str(candidate)
    return None


def fix_libdir() -> None:
    if not sys.platform.startswith("linux"):
        return
    config = sysconfig.get_config_vars()
    replacement = relocated_libdir(config.get("LIBDIR"), sys.base_prefix, shared_library_name())
    if replacement is not None:
        config["LIBDIR"] = replacement


def main() -> None:
    fix_libdir()
    runpy.run_module("endstone", run_name="__main__", alter_sys=True)


if __name__ == "__main__":
    main()
