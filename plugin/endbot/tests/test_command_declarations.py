"""Bedrock declaration coverage for the /bot command surface.

Unit tests cannot execute the closed-source BDS command parser, so this module
uses a small conservative model of the actual Endstone/BDS grammar instead:

- each usage string in ``COMMAND_USAGES`` becomes one native overload, parsed
  with the same bracket rules as Endstone's ``CommandUsageParser``;
- ``message`` is ``MessageRoot`` (rest of line, must be last) and ``pos`` /
  ``block_pos`` consume three tokens, matching the pinned registry mapping;
- optional *enum* parameters consume nothing on the pinned BDS build, while
  optional ``string`` / ``message`` / ``int`` parameters behave normally.

The model deliberately cannot prove live BDS matching. Its job is narrower:

1. every usage string must satisfy Endstone's registration rules, and
2. every documented /bot form must match at least one overload *without
   relying on enum resolution* (the ``test_named_forms_survive_enum_failure``
   case). That enum-independent fallback is what the typed-only declaration
   set removed, which live Bedrock rejected with a syntax error before the
   Python parser ran (notably ``/bot Alice move forward`` and
   ``/bot Alice tp me``).

The required live smoke cases are listed in docs/COMMANDS.md.
"""

import importlib
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


def _head_matches(param_type, token):
    if param_type == "int":
        return _is_int(token)
    if param_type == "float":
        return _is_float(token)
    return True


def _match(parameters, tokens, allow_enums=True):
    """Try to match words against one overload's parameters."""
    if not parameters:
        return not tokens
    first, rest = parameters[0], parameters[1:]

    def match_rest(remaining):
        return _match(rest, remaining, allow_enums)

    if first["kind"] == "enum":
        if first["optional"]:
            # Pinned BDS does not consume values for optional enum parameters.
            return match_rest(tokens)
        if not allow_enums:
            return False
        if tokens and tokens[0] in first["values"]:
            return match_rest(tokens[1:])
        return False

    param_type = first["type"]
    if param_type == "message":
        if tokens:
            return match_rest([])
        return match_rest(tokens) if first["optional"] else False
    arity = KNOWN_TYPES[param_type]
    if len(tokens) >= arity and _head_matches(param_type, tokens[0]) and match_rest(tokens[arity:]):
        return True
    if first["optional"]:
        return match_rest(tokens)
    return False


def _matches_any(tokens, allow_enums=True):
    return any(_match(_parse_usage(usage), tokens, allow_enums) for usage in COMMAND_USAGES)


class DeclarationCoverageTests(unittest.TestCase):
    def test_usages_satisfy_endstone_registration_rules(self) -> None:
        for usage in COMMAND_USAGES:
            with self.subTest(usage=usage):
                self.assertTrue(_parse_usage(usage))

    def test_every_documented_form_matches_an_overload(self) -> None:
        for tokens in DOCUMENTED_ROOT_FORMS + DOCUMENTED_NAMED_FORMS:
            with self.subTest(tokens=tokens):
                self.assertTrue(
                    _matches_any(tokens, allow_enums=True),
                    f"no overload matches documented form: /bot {' '.join(tokens)}",
                )

    def test_named_forms_survive_enum_failure(self) -> None:
        """Every named form must match an overload without enum resolution.

        This is the regression case: the typed-only declaration set matched on
        paper yet live Bedrock rejected ``move forward`` and ``tp me`` before
        Python ran. The string/message fallbacks keep those forms parseable.
        """
        for tokens in DOCUMENTED_ROOT_FORMS + DOCUMENTED_NAMED_FORMS:
            with self.subTest(tokens=tokens):
                self.assertTrue(
                    _matches_any(tokens, allow_enums=False),
                    f"documented form depends on enum resolution: /bot {' '.join(tokens)}",
                )


if __name__ == "__main__":
    unittest.main()
