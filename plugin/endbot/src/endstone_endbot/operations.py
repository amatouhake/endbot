"""Shared command vocabulary and typed script-to-command grammar.

Variants describe positional fields, named options and their literal argv clauses.
The ordinary parser remains the final semantic validator (including combinations).
No script-supplied token can become another command or an untyped suffix.
"""

import json
import math
import re
from pathlib import Path

OPERATIONS = json.loads(Path(__file__).with_name("operations.json").read_text(encoding="utf-8"))
ALIASES = {"teleport": "tp"}
ROOT_OPERATIONS = {"list", "ping", "help"}


def scalar(value, spec):
    kind = spec["type"]
    number = type(value) in (int, float) and math.isfinite(value)
    valid = {
        "string": isinstance(value, str),
        "number": number,
        "integer": number and int(value) == value,
        "boolean": type(value) is bool,
        "coordinate": number or (isinstance(value, str) and bool(re.fullmatch(r"~(?:-?\d+(?:\.\d+)?)?", value))),
        "stringOrInteger": isinstance(value, str) or (number and int(value) == value),
        "integerOrDefault": value == "default" or (number and int(value) == value),
    }.get(kind, False)
    if not valid or ("choices" in spec and value not in spec["choices"]):
        raise ValueError("Invalid typed operation argument")
    if isinstance(value, str) and (not value or any(c.isspace() or c in "\"'\\" for c in value)):
        raise ValueError("Operation arguments must be single tokens")
    if type(value) is bool:
        return "on" if value else "off"
    return str(int(value)) if number and int(value) == value else str(value)


def operation_argv(target, operation, parameters):
    """Derive argv exclusively from the shared table; reject surplus fields."""
    operation = ALIASES.get(operation, operation)
    if operation not in OPERATIONS or not isinstance(parameters, dict):
        raise ValueError("Unknown typed operation")
    if not isinstance(target, str) or not re.fullmatch(
        r"[A-Za-z_][A-Za-z0-9_]{0,15}|#[A-Za-z_][A-Za-z0-9_]{0,15}(?:\[[1-9][0-9]*\])?", target
    ):
        raise ValueError("Invalid script target")
    for shape in OPERATIONS[operation]:
        required = dict(shape["positional"])
        allowed = {**required, **shape["options"]}
        if not required.keys() <= parameters.keys() or not parameters.keys() <= allowed.keys():
            continue
        tokens = list(shape["prefix"])
        # Teleport's dimension is a leading clause, unlike spawn's suffix.
        if operation == "tp" and "dimension" in parameters:
            tokens = ["in", scalar(parameters["dimension"], allowed["dimension"]), *tokens]
        for key, spec in (*shape["positional"], *shape["options"].items()):
            if key not in parameters or (operation == "tp" and key == "dimension"):
                continue
            if "clause" in spec:
                tokens.append(spec["clause"])
            tokens.append(scalar(parameters[key], spec))
        return [operation, *tokens] if operation in ROOT_OPERATIONS else [target, operation, *tokens]
    raise ValueError("Invalid typed operation fields")
