"""Form views and guards as plain JSON for the endbotd link (docs/ENDBOTD.md P2).

endbotd builds a result's view (``menu_views``) and the plugin renders it, so
views, screens and form guards cross the link. They are frozen dataclasses
from a fixed set of core modules; each one travels tagged with its class, and
tuples and mappings keep their shape, so the plugin rebuilds equal objects.
Anything outside that set is refused instead of guessed.
"""

from __future__ import annotations

import dataclasses
import importlib

_MODULES = ("forms", "group_forms", "group_results", "groups", "script_forms", "script_store")
_CLASSES: dict[str, type] = {}


def _classes() -> dict[str, type]:
    if not _CLASSES:
        for short in _MODULES:
            module = importlib.import_module(f"endbot_core.{short}")
            for value in vars(module).values():
                if (isinstance(value, type) and dataclasses.is_dataclass(value)
                        and value.__module__ == module.__name__):
                    _CLASSES[f"{short}.{value.__name__}"] = value
    return _CLASSES


def _tag(cls: type) -> str:
    return f"{cls.__module__.rsplit('.', 1)[-1]}.{cls.__name__}"


def encode_view(value):
    """A JSON-safe form of a view, screen or guard; TypeError for anything else."""
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        tag = _tag(type(value))
        if _classes().get(tag) is not type(value):
            raise TypeError(f"{tag} cannot cross the endbotd link")
        return {"$view": tag, "fields": {field.name: encode_view(getattr(value, field.name))
                                         for field in dataclasses.fields(value)}}
    if isinstance(value, tuple):
        return {"$tuple": [encode_view(item) for item in value]}
    if isinstance(value, list):
        return [encode_view(item) for item in value]
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("view mappings need string keys")
        return {"$map": {key: encode_view(item) for key, item in value.items()}}
    raise TypeError(f"{type(value).__name__} cannot cross the endbotd link")


def decode_view(value):
    """Rebuild what :func:`encode_view` produced; ValueError for anything else."""
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, list):
        return [decode_view(item) for item in value]
    if not isinstance(value, dict) or len(value) not in {1, 2}:
        raise ValueError("malformed view value")
    if "$tuple" in value and isinstance(value["$tuple"], list):
        return tuple(decode_view(item) for item in value["$tuple"])
    if "$map" in value and isinstance(value["$map"], dict):
        return {key: decode_view(item) for key, item in value["$map"].items()}
    cls = _classes().get(value.get("$view"))
    fields = value.get("fields")
    if cls is None or not isinstance(fields, dict):
        raise ValueError("unknown view type")
    try:
        return cls(**{key: decode_view(item) for key, item in fields.items()})
    except TypeError as error:
        raise ValueError(f"invalid {value['$view']}: {error}") from None
