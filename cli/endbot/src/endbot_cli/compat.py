"""Small compatibility shims (stdlib only at runtime)."""

from __future__ import annotations

import sys

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - Python 3.10 fallback provided by the tomli dependency
    import tomli as tomllib

__all__ = ["tomllib"]
