"""Bedrock declaration coverage for the /bot command surface.

This module models how the pinned BDS build actually matches Endstone usage
strings. The rules below were established experimentally with a throwaway
probe plugin that dispatched 42 documented /bot forms through the real BDS
``compileCommand`` path across four declaration variants (typed-only tree,
shared-enum optional tail, shared-enum required tail, and the current
enum-free string tails):

- R1: only a FIRST-position optional consumes (strings take one
  non-numeric token, message takes the rest); every other optional is
  omit-only.
- R2: ``string`` (Bedrock Id) rejects pure-numeric tokens (``help 2`` fails
  where ``help dance`` passes at the same position).
- R3: params after an enum never match. Only overloads ending at (or without)
  an enum consume a tail, so tailed named inputs require an enum-free
  overload such as ``/bot <name> <operation> <arguments: message>``.

What this cannot do: it cannot prove client-side autocomplete rendering.
It is a regression tripwire — any declaration change that breaks a
documented form under these rules fails here without needing live BDS —
plus a record of the live behavior the rules were calibrated against. The
required live smoke cases are listed in docs/COMMANDS.md.
"""

import importlib
import re
import sys
import types
import unittest


class _StubPlugin:
    pass


class _StubCommand:
    def __init__(self, name: str) -> None:
        self.name = name


class _StubSender:
    pass


def _load_usages():
    endstone = types.ModuleType("endstone")
    command = types.ModuleType("endstone.command")
    event = types.ModuleType("endstone.event")
    level = types.ModuleType("endstone.level")
    plugin = types.ModuleType("endstone.plugin")
    command.Command = _StubCommand
    command.CommandSender = _StubSender
    event.PlayerJoinEvent = type("PlayerJoinEvent", (), {})
    event.event_handler = lambda function: function
    level.Location = type("Location", (), {})
    plugin.Plugin = _StubPlugin
    sys.modules.update(
        {
            "endstone": endstone,
            "endstone.command": command,
            "endstone.event": event,
            "endstone.level": level,
            "endstone.plugin": plugin,
        }
    )
    module = importlib.import_module("endstone_endbot.plugin")
    return list(module.COMMAND_USAGES)


COMMAND_USAGES = _load_usages()

KNOWN_TYPES = {
    "string": 1,
    "str": 1,
    "int": 1,
    "float": 1,
    "bool": 1,
    "block": 1,
    "entity_type": 1,
    "json": 1,
    "block_states": 1,
    "pos": 3,
    "vec3": 3,
    "vec3f": 3,
    "block_pos": 3,
    "vec3i": 3,
}

ENUM_VALUE = re.compile(r"[A-Za-z_-][A-Za-z0-9_-]*")

# Documented /bot inputs (words after "/bot"). Forms that Python rejects with a
# usage error are still listed: Bedrock must accept them so the rejection comes
# from the Python parser, not from a client-visible syntax error.
DOCUMENTED_ROOT_FORMS = [
    [],
    ["ping"],
    ["list"],
    ["help"],
    ["help", "advanced"],
    ["help", "2"],
    ["help", "move"],
    ["help", "teleport"],
    ["help", "ping"],
    ["help", "dance"],
    ["help", "99"],
    ["Alice"],
]

DOCUMENTED_NAMED_FORMS = [
    ["Alice", "status"],
    ["Alice", "resume"],
    ["Alice", "reconnect"],
    ["Alice", "despawn"],
    ["Alice", "forget"],
    ["Alice", "stop"],
    ["Alice", "spawn"],
    ["Alice", "spawn", "at", "1", "64", "2"],
    ["Alice", "spawn", "at", "1", "64", "2", "facing", "90", "0", "in", "nether"],
    ["Alice", "rename", "Bob"],
    ["Alice", "tp", "me"],
    ["Alice", "tp", "Steve"],
    ["Alice", "tp", "100", "64", "-20"],
    ["Alice", "tp", "100", "64", "-20", "90", "0"],
    ["Alice", "tp", "100", "64", "-20", "facing", "Steve"],
    ["Alice", "tp", "100", "64", "-20", "facing", "100", "70", "-10"],
    ["Alice", "tp", "in", "nether", "100", "64", "-20"],
    ["Alice", "move", "forward"],
    ["Alice", "move", "backward"],
    ["Alice", "move", "left"],
    ["Alice", "move", "right"],
    ["Alice", "move", "stop"],
    ["Alice", "look", "90", "0"],
    ["Alice", "look", "at", "1", "64", "2"],
    ["Alice", "jump"],
    ["Alice", "jump", "once"],
    ["Alice", "attack", "continuous"],
    ["Alice", "attack", "interval", "20"],
    ["Alice", "use", "stop"],
    ["Alice", "sprint", "on"],
    ["Alice", "sprint", "off"],
    ["Alice", "sneak", "on"],
    ["Alice", "sneak", "off"],
    ["Alice", "hotbar"],
    ["Alice", "hotbar", "9"],
    ["Alice", "interact", "1", "64", "2", "up"],
    ["Alice", "interact", "~1", "64", "-2", "down"],
    ["Alice", "drop"],
    ["Alice", "drop", "stack"],
    ["Alice", "dance"],
    ["Alice", "move"],
    ["Alice", "tp"],
]


def _split_usage(usage):
    segments = []
    depth = 0
    current = ""
    for character in usage:
        if character in "<[(":
            depth += 1
            current += character
        elif character in ">])":
            depth -= 1
            current += character
        elif character == " " and depth == 0:
            if current:
                segments.append(current)
                current = ""
        else:
            current += character
    if current:
        segments.append(current)
    return segments


def _parse_usage(usage):
    """Mirror Endstone's CommandUsageParser bracket rules for one usage string."""
    segments = _split_usage(usage)
    assert segments and segments[0] == "/bot", f"usage must start with /bot: {usage}"
    parameters = []
    for segment in segments[1:]:
        enum_values = None
        rest = segment
        if rest.startswith("("):
            closing = rest.index(")")
            enum_values = rest[1:closing].split("|")
            for value in enum_values:
                assert ENUM_VALUE.fullmatch(value), (
                    f"enum value {value!r} is not a BDS identifier in: {usage}"
                )
            rest = rest[closing + 1 :]
            assert rest, f"enum values need a parameter in: {usage}"
        assert rest[0] in "<[" and rest[-1] in ">]", f"bad parameter segment {segment!r} in: {usage}"
        optional = rest[0] == "["
        inner = rest[1:-1]
        if ":" in inner:
            name, param_type = (part.strip() for part in inner.split(":", 1))
            assert name, f"parameter needs a name in: {usage}"
            if enum_values is not None:
                parameters.append(
                    {"kind": "enum", "values": enum_values, "type": param_type, "optional": optional}
                )
            else:
                assert param_type in KNOWN_TYPES or param_type == "message", (
                    f"unsupported type {param_type!r} in: {usage}"
                )
                parameters.append({"kind": "typed", "type": param_type, "optional": optional})
        else:
            values = enum_values if enum_values is not None else inner.split("|")
            parameters.append({"kind": "enum", "values": values, "type": None, "optional": optional})
    for parameter in parameters[:-1]:
        assert not (parameter["kind"] == "typed" and parameter["type"] == "message"), (
            f"message parameter must be last in: {usage}"
        )
    return parameters


def _is_int(token):
    try:
        int(token)
    except ValueError:
        return False
    return True


def _is_float(token):
    try:
        float(token)
    except ValueError:
        return False
    return True


def _is_numeric(token):
    return _is_int(token) or _is_float(token)


def _match(parameters, tokens, allow_enums=True):
    """Try to match words against one overload under the live BDS rules.

    R1: only a FIRST-position optional consumes (strings: one non-numeric
    token; message: the rest); every other optional is omit-only. R2: string
    rejects numeric tokens. R3: nothing after an enum consumes.
    """
    if not parameters:
        return not tokens
    return _match_rest(parameters, tokens, seen_enum=False, allow_enums=allow_enums, is_first=True)


def _match_rest(parameters, tokens, seen_enum, allow_enums=True, is_first=False):
    if not parameters:
        return not tokens
    first, rest = parameters[0], parameters[1:]

    def skip():
        return _match_rest(rest, tokens, seen_enum, allow_enums)

    if first["kind"] == "enum":
        if first["optional"]:
            return skip()
        if not allow_enums:
            return False
        if seen_enum:
            return False
        if tokens and tokens[0] in first["values"]:
            return _match_rest(rest, tokens[1:], True, allow_enums)
        return False
    if first["optional"]:
        if seen_enum:
            return skip()
        consumed = _consume_optional(first, tokens, is_first)
        if consumed is not None:
            return _match_rest(rest, consumed, seen_enum, allow_enums)
        return skip()
    if seen_enum:
        # R3: live BDS never matches a param positioned after an enum.
        return False
    return _consume(first, tokens, rest, allow_enums)


def _consume_optional(parameter, tokens, is_first):
    """Consume one optional param, or None if it must be omitted."""
    if not tokens:
        return None
    param_type = parameter["type"]
    if param_type == "message":
        # Only a first-position optional message consumes on live BDS.
        return [] if is_first else None
    if param_type in {"string", "str"}:
        return None if _is_numeric(tokens[0]) else tokens[1:]
    return None


def _consume(parameter, tokens, rest, allow_enums=True):
    param_type = parameter["type"]
    if param_type == "message":
        return bool(tokens) and _match_rest(rest, [], False, allow_enums)
    arity = KNOWN_TYPES[param_type]
    if len(tokens) < arity:
        return False
    head = tokens[0]
    if param_type in {"string", "str"} and _is_numeric(head):
        return False
    if param_type == "int" and not _is_int(head):
        return False
    if param_type == "float" and not _is_float(head):
        return False
    return _match_rest(rest, tokens[arity:], False, allow_enums)


def _matches_any(tokens, allow_enums=True):
    return any(
        _match(_parse_usage(usage), tokens, allow_enums) for usage in COMMAND_USAGES
    )


class DeclarationCoverageTests(unittest.TestCase):
    def test_usages_satisfy_endstone_registration_rules(self) -> None:
        for usage in COMMAND_USAGES:
            with self.subTest(usage=usage):
                self.assertTrue(_parse_usage(usage))

    def test_every_documented_form_matches_under_live_rules(self) -> None:
        for tokens in DOCUMENTED_ROOT_FORMS + DOCUMENTED_NAMED_FORMS:
            with self.subTest(tokens=tokens):
                self.assertTrue(
                    _matches_any(tokens, allow_enums=True),
                    f"no overload matches documented form: /bot {' '.join(tokens)}",
                )

    def test_parseability_never_depends_on_enums(self) -> None:
        """Every documented form must match an overload with no enums at all.

        Tailed named inputs can only be consumed where no enum precedes the
        tail (live rule R3), and numeric pages cannot use strings (R2), so the
        enum-free string overloads are the parseability layer. The typed tree
        exists only for completion.
        """
        for tokens in DOCUMENTED_ROOT_FORMS + DOCUMENTED_NAMED_FORMS:
            with self.subTest(tokens=tokens):
                overloads = _parse_usage_matchers(tokens)
                self.assertTrue(
                    overloads,
                    f"documented form depends on enum resolution: /bot {' '.join(tokens)}",
                )


def _parse_usage_matchers(tokens):
    matches = []
    for usage in COMMAND_USAGES:
        parameters = _parse_usage(usage)
        if any(parameter["kind"] == "enum" for parameter in parameters):
            continue
        if _match(parameters, tokens, allow_enums=False):
            matches.append(usage)
    return matches


if __name__ == "__main__":
    unittest.main()
