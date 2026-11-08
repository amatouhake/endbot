"""One area value for every operation (BOT-027).

An area narrows the blocks an operation may pick. Chat and the typed command
grammar spell it as clauses: ``within <area>`` and ``except <area>``, where the
number of values selects the form:

========================  ===============================================
``<x> <y> <z>``           one cell
``<x1> <y1> <z1> <x2> <y2> <z2>``  a box, corners included (as ``/fill``)
``<r>``                   a sphere of radius ``r`` around the Bot
``<r> of <x> <y> <z>``    a sphere of radius ``r`` around a point
========================  ===============================================

``~`` is relative to the Bot and resolves when the command is accepted, so a
continuous loop keeps its area while the Bot moves. Chat takes one ``except``
like every other clause; a typed operation (Luau ``area:minus``) may chain
several. An area only narrows candidates: it never widens reach or sight.

Shapes are JSON-safe dicts so they cross the endbotd link unchanged:
``{"shape": "cell", "at": [x, y, z]}``, ``{"shape": "box", "from": [...],
"to": [...]}`` and ``{"shape": "sphere", "radius": r[, "at": [x, y, z]]}``
(no ``at``: around the Bot). An area is ``{"within": shape | None,
"except": [shape, ...]}``. A coordinate is a number or ``("relative", n)``
(a list after JSON); a resolved area holds numbers only.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterator

from endbot_core.i18n import LocalizedError

# About a radius-16 sphere: 4/3 * pi * 16^3 cells. Larger areas are refused.
MAX_RADIUS = 16
MAX_CELLS = round(4 / 3 * math.pi * MAX_RADIUS ** 3)
WORLD_LIMIT = 30_000_000
CLAUSES = ("within", "except")
AREA_FORMS = "<x> <y> <z>, <x1> <y1> <z1> <x2> <y2> <z2>, <r> or <r> of <x> <y> <z>"
AREA_USAGE = "{clause} takes an area: " + AREA_FORMS
COORDINATE = re.compile(r"~?[-+]?(?:\d+(?:\.\d*)?|\.\d+)?")


class AreaError(LocalizedError):
    """A malformed or oversized area; the command parser reports it as a syntax error."""


def _is_value(token: str) -> bool:
    return bool(COORDINATE.fullmatch(token)) and token not in {"", "+", "-"}


def _number(token: str) -> float:
    value = float(token)
    if not math.isfinite(value) or abs(value) > WORLD_LIMIT:
        raise AreaError("Area values must be within the world (at most 30000000 from 0)")
    return value


def _coordinate(token: str):
    if token.startswith("~"):
        return ("relative", _number(token[1:] or "0"))
    return _number(token)


def _radius(token: str, clause: str) -> float:
    if token.startswith("~"):
        raise AreaError("An area radius is a plain number, not a ~ coordinate")
    radius = _number(token)
    if radius <= 0:
        raise AreaError("An area radius must be greater than 0")
    if clause == "within" and radius > MAX_RADIUS:
        raise AreaError("An area may be at most a radius-{radius} sphere ({cells} blocks)", radius=MAX_RADIUS,
                        cells=MAX_CELLS)
    return radius


def _shape(clause: str, values: list[str], of: bool) -> dict:
    if of:
        if len(values) != 4:
            raise AreaError(AREA_USAGE, clause=clause)
        return {"shape": "sphere", "radius": _radius(values[0], clause), "at": [_coordinate(v) for v in values[1:]]}
    if len(values) == 1:
        return {"shape": "sphere", "radius": _radius(values[0], clause)}
    if len(values) == 3:
        return {"shape": "cell", "at": [_coordinate(value) for value in values]}
    if len(values) == 6:
        shape = {"shape": "box", "from": [_coordinate(v) for v in values[:3]],
                 "to": [_coordinate(v) for v in values[3:]]}
        if clause == "within":
            _check_box(shape)
        return shape
    raise AreaError(AREA_USAGE, clause=clause)


def _check_box(shape: dict) -> None:
    """Refuse a box over the cap when its size is known: corners both absolute or both relative."""
    first, second = shape["from"], shape["to"]
    size = 1
    for a, b in zip(first, second, strict=True):
        relative_a, relative_b = isinstance(a, (list, tuple)), isinstance(b, (list, tuple))
        if relative_a != relative_b:
            return  # decided when ~ resolves
        a, b = (a[1], b[1]) if relative_a else (a, b)
        size *= abs(math.floor(a) - math.floor(b)) + 1
    if size > MAX_CELLS:
        raise AreaError("An area may be at most a radius-{radius} sphere ({cells} blocks)", radius=MAX_RADIUS,
                        cells=MAX_CELLS)


def take_area(tokens: list[str], *, chain: bool = False) -> tuple[dict | None, list[str]]:
    """Split ``within``/``except`` clauses out of ``tokens``: ``(area or None, other tokens in order)``.

    ``chain`` allows several ``except`` clauses (typed operations); chat takes one.
    """
    area: dict = {"within": None, "except": []}
    rest: list[str] = []
    seen = False
    index = 0
    while index < len(tokens):
        clause = tokens[index].lower()
        if clause not in CLAUSES:
            rest.append(tokens[index])
            index += 1
            continue
        seen = True
        if clause == "within" and area["within"] is not None:
            raise AreaError("within can be given once")
        if clause == "except" and area["except"] and not chain:
            raise AreaError("except can be given once; scripts chain area:minus()")
        index += 1
        values, of = [], False
        while index < len(tokens):
            token = tokens[index]
            if _is_value(token):
                values.append(token)
            elif token.lower() == "of" and len(values) == 1 and not of:
                of = True
            else:
                break
            index += 1
        if not values and index < len(tokens) and tokens[index].isalpha() and tokens[index].lower() not in {
                "for", "count", "limit", "continuous", "once", "stop", *CLAUSES}:
            raise AreaError("Unknown area shape '{shape}'", shape=tokens[index])
        shape = _shape(clause, values, of)
        if clause == "within":
            area["within"] = shape
        else:
            area["except"].append(shape)
    return (area if seen else None), rest


def parse_area(text: str, *, chain: bool = True) -> dict:
    """An area from its clause text alone (``"within 6 except ~ ~-1 ~"``); AreaError otherwise."""
    if not isinstance(text, str) or not text.strip():
        raise AreaError("An area must be built with area.cell, area.box or area.sphere")
    area, rest = take_area(text.split(), chain=chain)
    if area is None or rest:
        raise AreaError("An area must be built with area.cell, area.box or area.sphere")
    return area


def clause_from_value(value: str) -> str:
    """An ``area=`` filter value as the ``within ...`` clause text ``validate_filter`` expects.

    The value is the area's values as ``within`` takes them (``"0 64 0"``, ``"0 64 0 3 65 3"``,
    ``"5 of 0 64 0"``), optionally followed by ``except <values>``; a value already written with
    ``within`` is kept as it is. The shape is validated later, with the filter.
    """
    if not isinstance(value, str) or not value.strip():
        raise AreaError("An area must be built with area.cell, area.box or area.sphere")
    text = value.strip()
    if text.lower() == "within" or text.lower().startswith("within "):
        return text
    return f"within {text}"


def _format(value) -> str:
    if isinstance(value, (list, tuple)):
        offset = float(value[1])
        return "~" if offset == 0 else "~" + _format(offset)
    value = float(value)
    if value == int(value):
        return str(int(value))
    return repr(value)


def _shape_tokens(shape: dict) -> list[str]:
    if shape["shape"] == "cell":
        return [_format(value) for value in shape["at"]]
    if shape["shape"] == "box":
        return [_format(value) for value in (*shape["from"], *shape["to"])]
    tokens = [_format(shape["radius"])]
    if "at" in shape:
        tokens += ["of", *(_format(value) for value in shape["at"])]
    return tokens


def area_tokens(area: dict) -> list[str]:
    """The canonical clause tokens of an area."""
    tokens = []
    if area.get("within") is not None:
        tokens += ["within", *_shape_tokens(area["within"])]
    for shape in area.get("except") or ():
        tokens += ["except", *_shape_tokens(shape)]
    return tokens


def area_text(area: dict) -> str:
    return " ".join(area_tokens(area))


# -- resolution and membership -------------------------------------------------


def _resolve_point(values, reference) -> list[float]:
    resolved = []
    for index, value in enumerate(values):
        if isinstance(value, (list, tuple)):
            if value[0] != "relative":
                raise AreaError("Invalid area coordinate")
            resolved.append(float(reference[index]) + float(value[1]))
        else:
            resolved.append(float(value))
    if not all(math.isfinite(value) and abs(value) <= WORLD_LIMIT for value in resolved):
        raise AreaError("Area values must be within the world (at most 30000000 from 0)")
    return resolved


def _resolve_shape(shape: dict, reference, within: bool) -> dict:
    kind = shape.get("shape") if isinstance(shape, dict) else None
    if kind == "cell":
        return {"shape": "cell", "at": [math.floor(v) for v in _resolve_point(shape["at"], reference)]}
    if kind == "box":
        first = [math.floor(v) for v in _resolve_point(shape["from"], reference)]
        second = [math.floor(v) for v in _resolve_point(shape["to"], reference)]
        low = [min(a, b) for a, b in zip(first, second, strict=True)]
        high = [max(a, b) for a, b in zip(first, second, strict=True)]
        if within and math.prod(b - a + 1 for a, b in zip(low, high, strict=True)) > MAX_CELLS:
            raise AreaError("An area may be at most a radius-{radius} sphere ({cells} blocks)", radius=MAX_RADIUS,
                            cells=MAX_CELLS)
        return {"shape": "box", "from": low, "to": high}
    if kind == "sphere":
        radius = float(shape["radius"])
        if not math.isfinite(radius) or radius <= 0 or (within and radius > MAX_RADIUS):
            raise AreaError("Invalid area radius")
        centre = _resolve_point(shape.get("at", [("relative", 0.0)] * 3), reference)
        return {"shape": "sphere", "radius": radius, "at": centre}
    raise AreaError("Invalid area")


def resolve_area(area: dict, reference) -> dict:
    """``area`` with every ``~`` (and the Bot-centred sphere) fixed against ``reference`` ``[x, y, z]``.

    Boxes come back with ordered integer corners, cells as integers; a resolved
    area resolves to itself.
    """
    if not isinstance(area, dict) or not isinstance(area.get("except", []), list):
        raise AreaError("Invalid area")
    within = area.get("within")
    return {"within": None if within is None else _resolve_shape(within, reference, True),
            "except": [_resolve_shape(shape, reference, False) for shape in area.get("except") or ()]}


def _shape_contains(shape: dict, cell) -> bool:
    kind = shape["shape"]
    if kind == "cell":
        return list(cell) == list(shape["at"])
    if kind == "box":
        return all(low <= value <= high for low, value, high in zip(shape["from"], cell, shape["to"], strict=True))
    centre = shape["at"]
    return math.dist(centre, [value + 0.5 for value in cell]) <= shape["radius"]


def contains(area: dict | None, cell) -> bool:
    """Whether a resolved area admits block ``cell`` ``[x, y, z]``; no area admits everything."""
    if area is None:
        return True
    within = area.get("within")
    if within is not None and not _shape_contains(within, cell):
        return False
    return not any(_shape_contains(shape, cell) for shape in area.get("except") or ())


def bounds(area: dict | None) -> tuple[list[int], list[int]] | None:
    """Inclusive integer cell bounds of a resolved area's ``within`` shape; None when it has none."""
    within = None if area is None else area.get("within")
    if within is None:
        return None
    kind = within["shape"]
    if kind == "cell":
        return list(within["at"]), list(within["at"])
    if kind == "box":
        return list(within["from"]), list(within["to"])
    centre, radius = within["at"], within["radius"]
    return ([math.floor(value - radius - 0.5) for value in centre],
            [math.floor(value + radius - 0.5) for value in centre])


def cells(area: dict) -> Iterator[tuple[int, int, int]]:
    """Every cell of a resolved area's ``within`` shape that the area admits."""
    low, high = bounds(area)
    for x in range(low[0], high[0] + 1):
        for y in range(low[1], high[1] + 1):
            for z in range(low[2], high[2] + 1):
                if contains(area, (x, y, z)):
                    yield x, y, z


def has_relative(area: dict | None) -> bool:
    """True when resolving ``area`` needs the Bot's position."""
    if area is None:
        return False
    for shape in (area.get("within"), *(area.get("except") or ())):
        if shape is None:
            continue
        if shape["shape"] == "sphere" and "at" not in shape:
            return True
        for key in ("at", "from", "to"):
            if any(isinstance(value, (list, tuple)) for value in shape.get(key) or ()):
                return True
    return False
