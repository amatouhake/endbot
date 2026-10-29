"""Validate declarations before returning any run arguments to Luau."""

import math
import re

from endbot_core.i18n import LocalizedError

KINDS = {"group", "bot", "pos", "number", "boolean", "string", "choice"}
NAMED = re.compile(r"([A-Za-z_][A-Za-z0-9_]{0,31})=(.*)", re.DOTALL)


def declarations(schema):
    if not isinstance(schema, list):
        raise LocalizedError("Script parameters must be an array")
    if len(schema) > 32:
        raise LocalizedError("Invalid script parameter declarations")
    result = {}
    for spec in schema:
        if not isinstance(spec, dict):
            raise LocalizedError("Invalid script parameter declarations")
        # Only a choice declares its options.
        if spec.keys() - {"name", "type", "default", *(("choices",) if spec.get("type") == "choice" else ())}:
            raise LocalizedError("Invalid script parameter declarations")
        name = spec.get("name")
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,31}", name):
            raise LocalizedError("Invalid script parameter name")
        if name in result:
            raise LocalizedError("Duplicate script parameter name")
        if not isinstance(spec.get("type"), str) or spec["type"] not in KINDS:
            raise LocalizedError("Unknown script parameter type")
        if spec["type"] == "choice":
            choices = spec.get("choices")
            if (not isinstance(choices, list) or not 1 <= len(choices) <= 32
                    or any(not isinstance(choice, str) or not 1 <= len(choice) <= 64 for choice in choices)
                    or len(set(choices)) != len(choices)):
                raise LocalizedError("Invalid script parameter declarations")
        if "default" in spec:
            default, kind = spec["default"], spec["type"]
            valid = (
                (kind == "number" and type(default) in (int, float) and math.isfinite(default))
                or (kind == "boolean" and type(default) is bool)
                or (kind in {"group", "bot", "pos", "string"} and isinstance(default, str))
                or (kind == "choice" and default in spec["choices"])
            )
            if not valid:
                raise LocalizedError("Invalid script parameter default")
        result[name] = spec
    return result


def split_named(schema, values):
    """Command arguments: positional values first, then `name=value` for declared parameters in any order."""
    positional, named = [], {}
    for value in values:
        match = NAMED.fullmatch(value) if isinstance(value, str) else None
        if match and match[1] in schema:
            if match[1] in named:
                raise LocalizedError("Script argument {name} is given twice", name=match[1])
            named[match[1]] = match[2]
        elif named:
            raise LocalizedError("Positional script arguments must come before name=value arguments")
        else:
            positional.append(value)
    if len(positional) > len(schema):
        raise LocalizedError("Too many script arguments")
    for name in list(schema)[: len(positional)]:
        if name in named:
            raise LocalizedError("Script argument {name} is given twice", name=name)
    return {**dict(zip(schema, positional)), **named}


def arguments(schema, supplied, *, groups, bots, origin=None):
    schema = declarations(schema)
    if isinstance(supplied, (list, tuple)):
        supplied = split_named(schema, supplied)
    if not isinstance(supplied, dict) or supplied.keys() - schema.keys():
        raise LocalizedError("Unknown script argument")
    result = {}
    for name, spec in schema.items():
        value = supplied.get(name, spec.get("default"))
        kind = spec["type"]
        try:
            if kind == "group":
                if not isinstance(value, str) or not value.startswith("#"):
                    raise ValueError()
                value = "#" + groups.get(value[1:]).name
            elif kind == "bot":
                if not isinstance(value, str):
                    raise ValueError()
                value = next(bot["name"] for bot in bots if bot["name"].casefold() == value.casefold())
            elif kind == "pos":
                tokens = value.split() if isinstance(value, str) else []
                if len(tokens) != 3:
                    raise ValueError()
                resolved = {}
                for axis, token in zip(("x", "y", "z"), tokens):
                    relative = token.startswith("~")
                    if relative and origin is None:
                        raise ValueError()
                    coordinate = float(token[1:] or "0") if relative else float(token)
                    resolved[axis] = coordinate + (origin[axis] if relative else 0)
                    if not math.isfinite(resolved[axis]):
                        raise ValueError()
                value = resolved
            elif kind == "number":
                if type(value) is bool or value is None:
                    raise ValueError()
                value = float(value)
                if not math.isfinite(value):
                    raise ValueError()
            elif kind == "boolean":
                if isinstance(value, str):
                    if value.lower() not in {"true", "false"}:
                        raise ValueError()
                    value = value.lower() == "true"
                if type(value) is not bool:
                    raise ValueError()
            elif kind == "choice":
                if value not in spec["choices"]:
                    raise ValueError()
            elif not isinstance(value, str) or len(value.encode("utf-8")) > 4096:
                raise ValueError()
        except (ValueError, TypeError, StopIteration, AttributeError) as error:
            raise LocalizedError("Invalid script argument: {name} ({kind})", name=name, kind=kind) from error
        result[name] = value
    return result
