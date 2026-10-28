import itertools
import unittest

from endstone_endbot.commands import parse_command
from endstone_endbot.operations import (
    GROUPS,
    OPERATIONS,
    ROOT_OPERATIONS,
    Operation,
    command_text,
    parse_operation,
)

# Sample values per field type; strings are named per field so they stay plausible.
STRINGS = {"target": "@zombie", "type": "oak_log", "name": "Bob", "direction": "forward", "item": "torch",
           "dimension": "nether", "facing": "Alice", "topic": "move", "face": "up", "selector": "dirt"}
SAMPLES = {
    "number": (90, 12.5, -3),
    "integer": (20, 1),
    "coordinate": (5, -1.5, "~", "~-1"),
    "boolean": (True, False),
    "stringOrInteger": (3, "dirt"),
    "integerOrDefault": (8, "default"),
}


def samples(key, spec):
    if "choices" in spec:
        return tuple(spec["choices"])
    if spec["type"] == "string":
        return (STRINGS[key],)
    return SAMPLES[spec["type"]]


def generated():
    """Every shape with each value sample, with no options, each option alone and all options.

    Only operations the command grammar accepts count: the registry alone allows
    halves of pairs (``pitch`` without ``yaw``) that no command can express.
    """
    for op, shapes in OPERATIONS.items():
        for shape in shapes:
            options = shape["options"]
            groups = [group for group in GROUPS if group[0] in options]
            groups += [(key,) for key in options if not any(key in group for group in GROUPS)]
            for subset in ((), *groups, tuple(key for group in groups for key in group)):
                fields = (*shape["positional"], *((key, options[key]) for key in subset))
                for values in itertools.product(*(samples(key, spec) for key, spec in fields)):
                    params = dict(zip((key for key, _ in fields), values, strict=True))
                    for target in ((None,) if op in ROOT_OPERATIONS else ("Alice", "#miners", "#miners[2]")):
                        operation = Operation(op, target, params)
                        try:
                            parse_command(operation.argv())
                        except ValueError:
                            continue
                        yield operation


class OperationRoundTripTests(unittest.TestCase):
    def test_parse_of_format_is_the_operation(self):
        count = 0
        for operation in generated():
            with self.subTest(operation=operation):
                argv = operation.argv()
                self.assertEqual(parse_operation(argv), operation)
                self.assertEqual(operation.text(), "/bot " + " ".join(argv))
            count += 1
        self.assertGreater(count, 1000)

    def test_well_formed_examples_parse_as_commands_from_their_text(self):
        for operation in (
            Operation("destroy", "Alice", {"type": "oak_log", "count": 5}),
            Operation("tp", "#miners[2]", {"dimension": "nether", "x": 1, "y": "~", "z": -2.5}),
            Operation("spawn", "Alice", {"x": 1, "y": 64, "z": 2, "yaw": 90, "pitch": 0}),
            Operation("use", "Alice", {"mode": "continuous", "hold": 10, "ticks": 200}),
            Operation("help", None, {"topic": "move"}),
        ):
            with self.subTest(operation=operation):
                text = operation.text().removeprefix("/bot ").split(" ")
                self.assertEqual(parse_command(text), parse_command(operation.argv()))
                self.assertEqual(parse_operation(text), operation)

    def test_only_canonical_text_reads_back(self):
        for argv in (
            ["Alice", "destroy", "count", "5", "for", "20"],  # the parser accepts any clause order
            ["Alice", "look", "at", "1", "2"],
            ["Alice", "move", "forward", "for", "20.0"],
            ["Alice", "fly"],
            ["Alice", "status", "now"],
            ["'Some Name'", "status"],
            ["Alice", "teleport", "Bob"],
            [],
        ):
            with self.subTest(argv=argv), self.assertRaises(ValueError):
                parse_operation(argv)
        # Aliases are accepted by commands, but the canonical name is what reads back.
        self.assertEqual(parse_operation(["Alice", "tp", "Bob"]), Operation("tp", "Alice", {"target": "Bob"}))
        self.assertEqual(Operation("teleport", "Alice", {"target": "Bob"}).argv(), ["Alice", "tp", "Bob"])


    def test_form_echoes_render_operations_and_keep_other_text(self):
        move = ("Alice", "move", "forward", "for", "20")
        self.assertEqual(command_text("bot", move), "/bot Alice move forward for 20")
        self.assertEqual(command_text("bot", ("Alice", "tp", "'Some Name'")), "/bot Alice tp 'Some Name'")
        self.assertEqual(command_text("botgroup", ("add", "miners", "Alice")), "/botgroup add miners Alice")


if __name__ == "__main__":
    unittest.main()
