"""Shared command vocabulary, the typed Operation and its command grammar.

An ``Operation`` is one typed request, spelled as the registry and Luau spell it
(``{op = "destroy", target = "Alice", params = {type = "oak_log", count = 5}}``).
Variants describe positional fields, named options and their literal argv clauses;
``operations.schema.json`` defines that structure for every reader (Python, Luau, docs, later the web).
The ordinary parser remains the final semantic validator (including combinations).
No script-supplied token can become another command or an untyped suffix.
"""

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

REGISTRY = json.loads(Path(__file__).with_name("operations.json").read_text(encoding="utf-8"))
OPERATIONS = REGISTRY["operations"]
CAPABILITIES = REGISTRY["capabilities"]
HOST_REQUESTS = REGISTRY["hostRequests"]
# Root command words that would shadow a Bot of the same name (interface guide D3).
RESERVED_BOT_NAMES = frozenset(REGISTRY["reservedBotNames"])
ALIASES = {"teleport": "tp"}
ROOT_OPERATIONS = {"list", "ping", "help"}
# Options that only mean something together; a reading never holds part of one.
GROUPS = (("yaw", "pitch"), ("facingX", "facingY", "facingZ"))
TARGET = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,15}|#[A-Za-z_][A-Za-z0-9_]{0,15}(?:\[[1-9][0-9]*\])?")


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
    # Root operations (list, ping, help) take no target.
    if not (operation in ROOT_OPERATIONS and target is None) and (
        not isinstance(target, str) or not TARGET.fullmatch(target)
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


@dataclass(frozen=True, slots=True)
class Operation:
    """One typed request: ``op`` on ``target`` (a Bot, ``#group`` or ``#group[n]``) with registry ``params``."""

    op: str
    target: str | None
    params: dict = field(default_factory=dict)

    def argv(self) -> list[str]:
        """The equivalent command arguments; raises ValueError for anything the registry does not allow."""
        return operation_argv(self.target, self.op, self.params)

    def text(self) -> str:
        """The equivalent typed command, as forms echo it and the web will show it."""
        return "/bot " + " ".join(self.argv())


def _value(token: str, spec):
    """The typed value of one canonical argv token; ValueError when it is not one."""
    kind = spec["type"]
    if kind == "boolean" and token in {"on", "off"}:
        value = token == "on"
    elif (kind == "string" or (kind == "coordinate" and token.startswith("~"))
          or (kind in {"stringOrInteger", "integerOrDefault"} and not re.fullmatch(r"-?\d+", token))):
        value = token
    elif kind in {"integer", "stringOrInteger", "integerOrDefault"}:
        if not re.fullmatch(r"-?\d+", token):
            raise ValueError("Expected an integer")
        value = int(token)
    elif kind in {"number", "coordinate"} and re.fullmatch(r"-?\d+(?:\.\d+)?", token):
        value = float(token) if "." in token else int(token)
    else:
        raise ValueError("Unexpected token")
    scalar(value, spec)
    return value


def _options(options: list, tokens: list[str]):
    """Every way to read ``tokens`` as the given options, in order; each reading consumes all tokens."""
    if not options:
        if not tokens:
            yield {}
        return
    (key, spec), rest = options[0], options[1:]
    clause = [spec["clause"]] if "clause" in spec else []
    if tokens[:len(clause)] == clause and len(tokens) > len(clause):
        try:
            value = _value(tokens[len(clause)], spec)
        except ValueError:
            pass
        else:
            for params in _options(rest, tokens[len(clause) + 1:]):
                yield {key: value, **params}
    yield from _options(rest, tokens)


def _match(operation: str, shape, tokens: list[str]):
    """Every reading of ``tokens`` as ``shape``, in the order ``operation_argv`` writes fields."""
    tokens, params = list(tokens), {}
    options = dict(shape["options"])
    if operation == "tp" and "dimension" in options:
        # Teleport's dimension is a leading clause.
        dimension = options.pop("dimension")
        if tokens[:1] == ["in"]:
            params["dimension"] = _value(tokens[1] if len(tokens) > 1 else "", dimension)
            tokens = tokens[2:]
    for word in shape["prefix"]:
        if not tokens or tokens.pop(0) != word:
            raise ValueError("Missing literal")
    for key, spec in shape["positional"]:
        if "clause" in spec and (not tokens or tokens.pop(0) != spec["clause"]):
            raise ValueError("Missing clause")
        if not tokens:
            raise ValueError("Missing field")
        params[key] = _value(tokens.pop(0), spec)
    for found in _options(list(options.items()), tokens):
        yield {**params, **found}


def parse_operation(arguments) -> Operation:
    """The Operation whose ``argv()`` is ``arguments``; ValueError for any other text.

    This reads only the canonical text ``Operation.argv`` writes; the command
    parser accepts many more spellings (any clause order, compact ``~~~``).
    """
    tokens = list(arguments)
    if tokens and tokens[0] in ROOT_OPERATIONS:
        target, operation, rest = None, tokens[0], tokens[1:]
    elif len(tokens) >= 2 and TARGET.fullmatch(tokens[0]):
        target, operation, rest = tokens[0], ALIASES.get(tokens[1], tokens[1]), tokens[2:]
    else:
        raise ValueError("Not an operation")
    for shape in OPERATIONS.get(operation, ()):
        try:
            readings = list(_match(operation, shape, rest))
        except ValueError:
            continue
        for params in readings:
            if any(0 < sum(key in params for key in group) < len(group) for group in GROUPS):
                continue
            result = Operation(operation, target, params)
            if result.argv() == tokens:
                return result
    raise ValueError("Not a canonical operation")


def command_text(root: str, arguments) -> str:
    """The typed command a form action equals; a /bot operation is rendered from its Operation."""
    if root == "bot":
        try:
            return parse_operation(arguments).text()
        except ValueError:
            pass  # Free text a form passes on for the parser to judge, such as a quoted player name.
    return f"/{root} {' '.join(arguments)}"
