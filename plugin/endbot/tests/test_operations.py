import copy
import itertools
import json
import re
import unittest
from pathlib import Path

from endbot_core import operations
from endbot_core.commands import parse_command, typed_areas
from endbot_core.operations import (
    CAPABILITIES,
    EVENTS,
    GROUPS,
    HOST_REQUESTS,
    OPERATIONS,
    ROOT_OPERATIONS,
    Operation,
    command_text,
    guarantee_text,
    parse_operation,
)
from endbot_core.script_params import BASIC_CAPABILITIES, DECLARED_CAPABILITIES

# Every capability a run may name: the basic set plus what scripts must declare (PERMISSIONS S8).
KNOWN_CAPABILITIES = BASIC_CAPABILITIES | DECLARED_CAPABILITIES

# Sample values per field type; strings are named per field so they stay plausible.
STRINGS = {"target": "@zombie", "type": "oak_log", "name": "Bob", "direction": "forward", "item": "torch",
           "dimension": "nether", "facing": "Alice", "topic": "move", "face": "up", "selector": "dirt",
           "gamertag": "Alice"}
SAMPLES = {
    "number": (90, 12.5, -3),
    "integer": (20, 1),
    "coordinate": (5, -1.5, "~", "~-1"),
    "boolean": (True, False),
    "stringOrInteger": (3, "dirt"),
    "player": ("Alice", "Major Nelson"),
    "integerOrDefault": (8, "default"),
    "area": ("within 6", "within ~ ~-1 ~ 3 4 5 except 1 2 3 except 2 of ~ ~ ~"),
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
                            with typed_areas():
                                parse_command(operation.argv())
                        except ValueError:
                            continue
                        yield operation


class PlayerNameTests(unittest.TestCase):
    def test_a_gamertag_with_a_space_is_one_argument(self):
        argv = operations.operation_argv("A", "tp", {"target": "Major Nelson"})
        self.assertEqual(argv, ["A", "tp", "'Major Nelson'"])
        self.assertEqual(parse_command(argv).parameters, {"target": "Major Nelson"})
        self.assertEqual(operations.parse_operation(argv).params, {"target": "Major Nelson"})

    def test_nothing_else_gets_past_as_one_name(self):
        for name in ("~", "~~~", "~1", "A~", "", " ", " A", "A ", "A  B", "A\tB", "A\nB", "A'B", 'A"B', "A\\B",
                     "A' 1 2 3 '"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                operations.operation_argv("A", "tp", {"target": name})

    def test_facing_a_player_takes_the_same_names(self):
        argv = operations.operation_argv("A", "tp", {"x": 1, "y": 2, "z": 3, "facing": "Major Nelson"})
        self.assertEqual(argv[-2:], ["facing", "'Major Nelson'"])
        self.assertEqual(parse_command(argv).parameters["facingTarget"], "Major Nelson")

    def test_other_arguments_still_refuse_spaces(self):
        with self.assertRaises(ValueError):
            operations.operation_argv("A", "rename", {"name": "Major Nelson"})


class BotNameTests(unittest.TestCase):
    def test_a_bot_name_may_start_with_a_digit_as_the_runtime_allows(self):
        self.assertEqual(operations.operation_argv("2b", "status", {}), ["2b", "status"])
        self.assertEqual(operations.parse_operation(["2b", "status"]).target, "2b")

    def test_a_group_name_may_not(self):
        self.assertEqual(operations.operation_argv("#a2", "status", {}), ["#a2", "status"])
        for target in ("#2b", "a-b", "a b", "a" * 17, ""):
            with self.subTest(target=target), self.assertRaises(ValueError):
                operations.operation_argv(target, "status", {})


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


def _matches_type(instance, kind):
    return {
        "object": isinstance(instance, dict),
        "array": isinstance(instance, list),
        "string": isinstance(instance, str),
        "integer": type(instance) is int,
        "number": type(instance) in (int, float),
        "boolean": type(instance) is bool,
        "null": instance is None,
    }[kind]


def heading_slug(heading):
    """The GitHub-style anchor EVENTS E2 names: lowercase, spaces to -, punctuation other than - and _ removed."""
    return re.sub(r"[^a-z0-9 _-]", "", heading.lower()).replace(" ", "-")


def headings(text):
    """Every Markdown ATX heading outside a fenced code block, without the leading and closing hashes."""
    fenced = False
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith(("```", "~~~")):
            fenced = not fenced
            continue
        if fenced:
            continue
        match = re.match(r"#{1,6}\s+(.*)$", line)
        if match:
            yield re.sub(r"\s+#+\s*$", "", match[1].strip())


def check_events(registry):
    """The rules EVENTS E1 and E2 add beyond the JSON schema, for every listed kind."""
    for kind, spec in registry["events"].items():
        filters = spec["filters"]
        required_sets = set()
        for names in spec["require"]:
            if not names:
                raise AssertionError(f"events.{kind}: a require set is empty")
            if frozenset(names) in required_sets:
                raise AssertionError(f"events.{kind}: duplicate require set {names!r}")
            required_sets.add(frozenset(names))
            for name in names:
                if name not in filters:
                    raise AssertionError(f"events.{kind}: require names the unknown filter {name!r}")
        if spec["visibility"] != "public":
            raise AssertionError(f"events.{kind}: visibility {spec['visibility']!r} may not be listed")
        capability = spec["capability"]
        if capability not in KNOWN_CAPABILITIES:
            raise AssertionError(f"events.{kind}: unknown capability {capability!r}")
        if capability != "read.world" and not capability.startswith("observe."):
            raise AssertionError(f"events.{kind}: capability {capability!r} is not read.world or observe.*")
        guarantee = spec["guarantee"]
        if guarantee["class"] == "persist":
            ticks = guarantee["ticks"]
            if type(ticks) is not int or ticks < 1:
                raise AssertionError(f"events.{kind}: persist needs a positive integer ticks, not {ticks!r}")
            each = guarantee["each"]
            if any(not isinstance(item, str) or not item for item in each):
                raise AssertionError(f"events.{kind}: each entries must be non-empty strings")
            if len(set(each)) != len(each):
                raise AssertionError(f"events.{kind}: each entries must be unique")
        else:
            if guarantee["ticks"] is not None:
                raise AssertionError(f"events.{kind}: class each needs ticks null")
            if guarantee["each"]:
                raise AssertionError(f"events.{kind}: class each needs an empty each")
        for delivered in ("kind", "missed"):
            if delivered in spec["payload"]:
                raise AssertionError(f"events.{kind}: payload may not name the delivery field {delivered!r}")
        record = guarantee["record"]
        file, _, anchor = record.partition("#")
        document = REPOSITORY / "docs" / file
        if not file or not anchor or not document.is_file():
            raise AssertionError(f"events.{kind}: record {record!r} does not name a document in docs/")
        if anchor not in {heading_slug(heading) for heading in headings(document.read_text(encoding="utf-8"))}:
            raise AssertionError(f"events.{kind}: record {record!r} names no Markdown heading anchor")


def check(instance, schema, root, path="registry"):
    """Validate with the JSON Schema keywords operations.schema.json uses; raise AssertionError on the first fault."""
    if schema is False:
        raise AssertionError(f"{path}: not allowed")
    if "$ref" in schema:
        check(instance, root["$defs"][schema["$ref"].removeprefix("#/$defs/")], root, path)
    kind = schema.get("type")
    if kind:
        kinds = kind if isinstance(kind, list) else [kind]
        if not any(_matches_type(instance, name) for name in kinds):
            raise AssertionError(f"{path}: expected {' or '.join(kinds)}")
    if "const" in schema and instance != schema["const"]:
        raise AssertionError(f"{path}: {instance!r} is not {schema['const']!r}")
    if "enum" in schema and instance not in schema["enum"]:
        raise AssertionError(f"{path}: {instance!r} is not one of {schema['enum']}")
    if "pattern" in schema and not re.search(schema["pattern"], instance):
        raise AssertionError(f"{path}: {instance!r} does not match {schema['pattern']}")
    if "minimum" in schema and instance < schema["minimum"]:
        raise AssertionError(f"{path}: {instance!r} is below {schema['minimum']}")
    for subschema in schema.get("allOf", ()):
        check(instance, subschema, root, path)
    if "if" in schema:
        try:
            check(instance, schema["if"], root, path)
        except AssertionError:
            if "else" in schema:
                check(instance, schema["else"], root, path)
        else:
            check(instance, schema.get("then", {}), root, path)
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
    if path == "registry":
        check_events(instance)


VALID_EVENT = {
    "source": "plugin",
    "capability": "read.world",
    "visibility": "public",
    "filters": {
        "cause": {"type": "string"},
        "box": {"type": "area", "maxBlocks": 512},
        "dimension": {"type": "dimension", "default": "overworld"},
    },
    "require": [["cause"], ["box"]],
    "payload": {"cause": "string", "bot": "string?"},
    "guarantee": {"class": "persist", "ticks": 4, "each": ["player-break", "player-place"],
                  "record": "EVENTS.md#target"},
}
# The sentence Python and JS must both produce for VALID_EVENT (runner/runner.test.mjs pins the same string).
GUARANTEE_FIXTURE = ("At most once, after the fact, in source order. A change that lasts at least 4 ticks is "
                     "delivered; player-break and player-place are delivered individually.")


def registry_with(events):
    """The real registry with only its events table replaced, so a case proves one rule."""
    return {**operations.REGISTRY, "events": events}


def event_kind(**changes):
    event = copy.deepcopy(VALID_EVENT)
    event.update(changes)
    return {"block.change": event}


class EventRegistryTests(unittest.TestCase):
    def setUp(self):
        self.schema = json.loads((SOURCE / "operations.schema.json").read_text(encoding="utf-8"))

    def assertRegistryAccepted(self, events):
        check(registry_with(events), self.schema, self.schema)

    def assertRegistryRejected(self, events, message=None):
        with self.assertRaises(AssertionError) as caught:
            check(registry_with(events), self.schema, self.schema)
        if message:
            self.assertIn(message, str(caught.exception))

    def test_block_change_is_listed_with_the_measured_guarantee(self):
        self.assertLessEqual({"block.change", "player.join", "player.leave", "player.chat"}, set(EVENTS))
        self.assertEqual(EVENTS["player.chat"]["capability"], "observe.chat")
        self.assertEqual(EVENTS["player.join"]["capability"], "observe.presence")
        guarantee = EVENTS["block.change"]["guarantee"]
        self.assertEqual((guarantee["class"], guarantee["ticks"], guarantee["each"], guarantee["record"]),
                         ("persist", 4, ["player-break", "player-place"], "BLOCK_EVENTS_SPIKE.md#result"))
        self.assertEqual(EVENTS["block.change"]["filters"]["area"], {"type": "area", "maxBlocks": 512})

    def test_the_registry_requires_an_events_table(self):
        registry = {key: value for key, value in operations.REGISTRY.items() if key != "events"}
        with self.assertRaises(AssertionError):
            check(registry, self.schema, self.schema)

    def test_a_valid_kind_passes_the_schema_and_the_rules(self):
        self.assertRegistryAccepted(event_kind())
        for source in ("plugin", "runtime", "endbotd"):
            with self.subTest(source=source):
                self.assertRegistryAccepted(event_kind(source=source))
        for capability in ("read.world", "observe.chat", "observe.presence"):
            with self.subTest(capability=capability):
                self.assertRegistryAccepted(event_kind(capability=capability))
        for guarantee in (
            {"class": "persist", "ticks": 1, "each": [], "record": "EVENTS.md#target"},
            {"class": "persist", "ticks": 8, "each": ["player-break"], "record": "EVENTS.md#target"},
            {"class": "each", "ticks": None, "each": [], "record": "EVENTS.md#target"},
        ):
            with self.subTest(guarantee=guarantee):
                self.assertRegistryAccepted(event_kind(guarantee=guarantee))

    def test_the_kind_name_is_category_action(self):
        for name in ("block", "Block.change", "block.Change", "block..change", "block.change.more", "block. change"):
            with self.subTest(name=name):
                self.assertRegistryRejected({name: copy.deepcopy(VALID_EVENT)})

    def test_every_kind_field_is_required_and_nothing_else_is_allowed(self):
        for field in ("source", "capability", "visibility", "filters", "require", "payload", "guarantee"):
            event = copy.deepcopy(VALID_EVENT)
            del event[field]
            with self.subTest(field=field):
                self.assertRegistryRejected({"block.change": event})
        self.assertRegistryRejected(event_kind(extra=True))
        for field, value in (("source", "game"), ("source", "Plugin"), ("visibility", "everyone")):
            with self.subTest(field=field, value=value):
                self.assertRegistryRejected(event_kind(**{field: value}))

    def test_filters_name_a_type_and_only_an_area_carries_a_bound(self):
        for filters in (
            {"x": {"type": "vector"}},
            {"x": {"type": "area", "maxBlocks": 0}},
            {"x": {"type": "area", "maxBlocks": -1}},
            {"x": {"type": "area", "maxBlocks": 1.5}},
            {"x": {"type": "block", "maxBlocks": 2}},
            {"x": {"type": "string", "clue": "x"}},
            {"x": {}},
            {"Bad": {"type": "string"}},
        ):
            with self.subTest(filters=filters):
                self.assertRegistryRejected(event_kind(filters=filters, require=[]))
        self.assertRegistryAccepted(event_kind(
            filters={"box": {"type": "area", "maxBlocks": 1}, "dimension": {"type": "dimension"}}, require=[]))

    def test_payload_fields_name_a_type_and_optional_fields_end_in_a_question_mark(self):
        for payload in ({"x": "vector"}, {"x": "string??"}, {"x": "block "}, {"x": "integer|string"}):
            with self.subTest(payload=payload):
                self.assertRegistryRejected(event_kind(payload=payload))
        self.assertRegistryAccepted(event_kind(
            payload={"x": "integer", "ratio": "number", "name": "string", "flag": "boolean", "block": "block",
                     "box": "area", "bot": "string?", "block_id": "string"}))
        self.assertRegistryAccepted(event_kind(payload={}))

    def test_the_events_table_and_kind_entries_are_objects(self):
        self.assertRegistryRejected([])
        self.assertRegistryRejected({"block.change": []})

    def test_require_and_guarantee_have_their_exact_shapes(self):
        for require in ("cause", ["cause"], [["cause"], "box"]):
            with self.subTest(require=require):
                self.assertRegistryRejected(event_kind(require=require))
        for field in ("class", "ticks", "each", "record"):
            guarantee = copy.deepcopy(VALID_EVENT["guarantee"])
            del guarantee[field]
            with self.subTest(missing=field):
                self.assertRegistryRejected(event_kind(guarantee=guarantee))
        extra = copy.deepcopy(VALID_EVENT["guarantee"])
        extra["extra"] = True
        self.assertRegistryRejected(event_kind(guarantee=extra))
        for field, value in (("class", "sometimes"), ("each", [1])):
            guarantee = copy.deepcopy(VALID_EVENT["guarantee"])
            guarantee[field] = value
            with self.subTest(field=field, value=value):
                self.assertRegistryRejected(event_kind(guarantee=guarantee))

    def test_require_sets_name_existing_filters_and_are_neither_empty_nor_duplicated(self):
        for require in ([["missing"]], [[]], [["cause"], ["cause"]], [["cause", "box"], ["box", "cause"]]):
            with self.subTest(require=require):
                self.assertRegistryRejected(event_kind(require=require))
        self.assertRegistryAccepted(event_kind(require=[]))
        self.assertRegistryAccepted(event_kind(require=[["cause", "dimension"]]))

    def test_only_public_visibility_may_be_listed(self):
        for visibility in ("not-visible", "private"):
            with self.subTest(visibility=visibility):
                self.assertRegistryRejected(event_kind(visibility=visibility), "visibility")

    def test_capability_is_read_world_or_a_known_observe_class(self):
        self.assertRegistryRejected(event_kind(capability="act.bot"), "read.world or observe")
        self.assertRegistryRejected(event_kind(capability="read.other"), "unknown capability")
        self.assertRegistryRejected(event_kind(capability="observe.unknown"), "unknown capability")
        self.assertEqual({"observe.chat", "observe.presence"} - KNOWN_CAPABILITIES, set())

    def test_record_names_an_existing_heading_in_docs(self):
        for record in ("MISSING.md#target", "EVENTS.md#missing", "EVENTS.md#Target", "EVENTS.md", "#target"):
            guarantee = copy.deepcopy(VALID_EVENT["guarantee"])
            guarantee["record"] = record
            with self.subTest(record=record):
                self.assertRegistryRejected(event_kind(guarantee=guarantee))
        punctuation = copy.deepcopy(VALID_EVENT["guarantee"])
        punctuation["record"] = "EVENTS.md#e1-event-kinds-live-in-the-registry-under-events"
        self.assertRegistryAccepted(event_kind(guarantee=punctuation))

    def test_persist_needs_a_positive_integer_ticks(self):
        for ticks in (None, 0, -4, 4.0, True):
            guarantee = copy.deepcopy(VALID_EVENT["guarantee"])
            guarantee["ticks"] = ticks
            with self.subTest(ticks=ticks):
                self.assertRegistryRejected(event_kind(guarantee=guarantee))

    def test_persist_each_entries_are_unique_non_empty_strings(self):
        for each in (["player-break", "player-break"], [""], ["player-break", ""]):
            guarantee = copy.deepcopy(VALID_EVENT["guarantee"])
            guarantee["each"] = each
            with self.subTest(each=each):
                self.assertRegistryRejected(event_kind(guarantee=guarantee))

    def test_class_each_needs_null_ticks_and_an_empty_each(self):
        for ticks, each in ((4, []), (None, ["player-break"]), (4, ["player-break"])):
            guarantee = {"class": "each", "ticks": ticks, "each": each, "record": "EVENTS.md#target"}
            with self.subTest(ticks=ticks, each=each):
                self.assertRegistryRejected(event_kind(guarantee=guarantee))

    def test_payload_may_not_name_the_fields_delivery_adds(self):
        for name in ("kind", "missed"):
            with self.subTest(name=name):
                self.assertRegistryRejected(event_kind(payload={**VALID_EVENT["payload"], name: "string"}))


class GuaranteeTextTests(unittest.TestCase):
    def test_persist_names_the_ticks_and_the_causes_delivered_individually(self):
        self.assertEqual(guarantee_text(copy.deepcopy(VALID_EVENT)), GUARANTEE_FIXTURE)
        self.assertEqual(guarantee_text(copy.deepcopy(VALID_EVENT)["guarantee"]), GUARANTEE_FIXTURE)

    def test_persist_without_each_causes_states_only_the_tick_guarantee(self):
        guarantee = {"class": "persist", "ticks": 8, "each": [], "record": "EVENTS.md#target"}
        self.assertEqual(guarantee_text(guarantee),
                         "At most once, after the fact, in source order. "
                         "A change that lasts at least 8 ticks is delivered.")

    def test_persist_with_one_cause_says_it_is_delivered_individually(self):
        guarantee = {"class": "persist", "ticks": 2, "each": ["player-break"], "record": "EVENTS.md#target"}
        self.assertEqual(guarantee_text(guarantee),
                         "At most once, after the fact, in source order. "
                         "A change that lasts at least 2 ticks is delivered; "
                         "player-break is delivered individually.")

    def test_each_states_that_every_occurrence_is_delivered(self):
        guarantee = {"class": "each", "ticks": None, "each": [], "record": "EVENTS.md#target"}
        self.assertEqual(guarantee_text({"guarantee": guarantee}),
                         "At most once, after the fact, in source order. "
                         "Every occurrence is delivered while the source is connected.")


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
            registry = {"operations": operations_table, "capabilities": CAPABILITIES,
                        "hostRequests": HOST_REQUESTS, "reservedBotNames": []}
            with self.subTest(registry=registry), self.assertRaises(AssertionError):
                check(registry, self.schema, self.schema)
        for registry in ({"operations": OPERATIONS, "capabilities": CAPABILITIES, "hostRequests": HOST_REQUESTS},
                         {**operations.REGISTRY, "reservedBotNames": ["List"]},
                         {**operations.REGISTRY, "extra": 1}):
            with self.subTest(registry=registry), self.assertRaises(AssertionError):
                check(registry, self.schema, self.schema)

    def test_the_checker_requires_capabilities_and_host_requests(self):
        for registry in (
            {key: value for key, value in operations.REGISTRY.items() if key != "capabilities"},
            {key: value for key, value in operations.REGISTRY.items() if key != "hostRequests"},
            {**operations.REGISTRY, "capabilities": {**CAPABILITIES, "status": "act.everything"}},
            {**operations.REGISTRY, "hostRequests": {**HOST_REQUESTS, "params": "none"}},
        ):
            with self.subTest(registry=registry), self.assertRaises(AssertionError):
                check(registry, self.schema, self.schema)

    def test_every_operation_names_its_capability(self):
        self.assertEqual(set(CAPABILITIES), set(OPERATIONS))

    def test_every_luau_host_request_names_its_capability(self):
        api = (REPOSITORY / "runner" / "api.luau").read_text(encoding="utf-8")
        # log() sends through emitLog instead of request(); that is why the set adds "log" below.
        self.assertIn("emitLog(", api)
        sent = set(re.findall(r'request\(\{type = "(\w+)"', api)) | {"log"}
        self.assertEqual(sent, set(HOST_REQUESTS))
        scripting = (SOURCE / "scripting.py").read_text(encoding="utf-8")
        run_loop = scripting[scripting.index("    def _run("):scripting.index("    def _chat_log(")]
        handled = set(re.findall(r'kind == "(\w+)"', run_loop))
        handled |= {name for group in re.findall(r"kind in \{([^}]*)\}", run_loop)
                    for name in re.findall(r'"(\w+)"', group)}
        # done and error end the run rather than ask the host for anything; params is the declaration,
        # which _declaration reads before the request loop starts.
        self.assertIn('event.get("type") != "params"', scripting)
        self.assertEqual((handled - {"done", "error"}) | {"params"}, set(HOST_REQUESTS))

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
