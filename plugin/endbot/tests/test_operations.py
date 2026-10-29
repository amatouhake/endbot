import itertools
import json
import re
import unittest
from pathlib import Path

from endbot_core import operations
from endbot_core.commands import parse_command
from endbot_core.operations import (
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


SOURCE = Path(operations.__file__).parent
REPOSITORY = Path(__file__).resolve().parents[3]


def check(instance, schema, root, path="registry"):
    """Validate with the JSON Schema keywords operations.schema.json uses; raise AssertionError on the first fault."""
    if schema is False:
        raise AssertionError(f"{path}: not allowed")
    if "$ref" in schema:
        check(instance, root["$defs"][schema["$ref"].removeprefix("#/$defs/")], root, path)
    kind = schema.get("type")
    types = {"object": dict, "array": list, "string": str}
    if kind and not isinstance(instance, types[kind]):
        raise AssertionError(f"{path}: expected {kind}")
    if "enum" in schema and instance not in schema["enum"]:
        raise AssertionError(f"{path}: {instance!r} is not one of {schema['enum']}")
    if "pattern" in schema and not re.search(schema["pattern"], instance):
        raise AssertionError(f"{path}: {instance!r} does not match {schema['pattern']}")
    if isinstance(instance, dict):
        if len(instance) < schema.get("minProperties", 0):
            raise AssertionError(f"{path}: too few properties")
        for key in schema.get("required", ()):
            if key not in instance:
                raise AssertionError(f"{path}: missing {key}")
        for key, value in instance.items():
            check(key, schema.get("propertyNames", {}), root, f"{path} key {key}")
            properties = schema.get("properties", {})
            check(value, properties[key] if key in properties else schema.get("additionalProperties", {}), root,
                  f"{path}.{key}")
    if isinstance(instance, list):
        if not schema.get("minItems", 0) <= len(instance) <= schema.get("maxItems", len(instance)):
            raise AssertionError(f"{path}: wrong number of items")
        if schema.get("uniqueItems") and len(set(map(json.dumps, instance))) != len(instance):
            raise AssertionError(f"{path}: duplicate items")
        prefix = schema.get("prefixItems", [])
        for index, value in enumerate(instance):
            check(value, prefix[index] if index < len(prefix) else schema.get("items", {}), root, f"{path}[{index}]")


class RegistrySchemaTests(unittest.TestCase):
    def setUp(self):
        self.schema = json.loads((SOURCE / "operations.schema.json").read_text(encoding="utf-8"))
        self.types = set(self.schema["$defs"]["field"]["properties"]["type"]["enum"])

    def test_the_registry_follows_its_schema(self):
        check(operations.REGISTRY, self.schema, self.schema)

    def test_the_checker_rejects_what_the_schema_forbids(self):
        for operations_table in (
            {},
            {"move": []},
            {"move": [{"positional": [], "options": {}}]},
            {"move": [{"positional": [["direction", {"type": "vector"}]], "options": {}, "prefix": []}]},
            {"move": [{"positional": [["direction"]], "options": {}, "prefix": []}]},
            {"move": [{"positional": [], "options": {"for": {"type": "integer", "clause": "For"}}, "prefix": []}]},
            {"move": [{"positional": [], "options": {"mode": {"type": "string", "choices": ["a", "a"]}},
                       "prefix": []}]},
            {"Move": [{"positional": [], "options": {}, "prefix": []}]},
        ):
            registry = {"operations": operations_table, "reservedBotNames": []}
            with self.subTest(registry=registry), self.assertRaises(AssertionError):
                check(registry, self.schema, self.schema)
        for registry in ({"operations": OPERATIONS}, {**operations.REGISTRY, "reservedBotNames": ["List"]},
                         {**operations.REGISTRY, "extra": 1}):
            with self.subTest(registry=registry), self.assertRaises(AssertionError):
                check(registry, self.schema, self.schema)

    def test_python_validates_exactly_the_schema_types(self):
        self.assertEqual({spec["type"] for shapes in OPERATIONS.values() for shape in shapes
                          for _, spec in (*shape["positional"], *shape["options"].items())}, self.types)
        # scalar() keeps one validator per type in a table; it must name exactly the schema's types.
        source = Path(operations.__file__).read_text(encoding="utf-8")
        table = source[source.index("def scalar"):source.index("def operation_argv")]
        checked = set(re.findall(r'^\s+"(\w+)": ', table, re.MULTILINE))
        self.assertEqual(checked, self.types)

    def test_the_luau_bootstrap_validates_exactly_the_schema_types(self):
        api = (REPOSITORY / "runner" / "api.luau").read_text(encoding="utf-8")
        valid = api[api.index("local function valid("):api.index("local function arguments(")]
        self.assertEqual(set(re.findall(r'kind == "(\w+)"', valid)), self.types)


if __name__ == "__main__":
    unittest.main()
