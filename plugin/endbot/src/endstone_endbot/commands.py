"""Pure command grammar and service for the /bot surface."""

from __future__ import annotations

import math
import re
import shlex
import threading
from dataclasses import dataclass, field

from endstone_endbot.control import RuntimeControlError, bot_profiles
from endstone_endbot.i18n import LocalizedError, Text, localize_error, tr
from endstone_endbot.limits import BotLimits
from endstone_endbot.operations import ALIASES, OPERATIONS, Operation
from endstone_endbot.principal import as_job


class CommandSyntaxError(LocalizedError):
    pass


# Outcome codes, coarse by what the user should do next; refined only when a
# caller needs to tell two cases apart. "ok" is the only success code.
OUTCOME_CODES = frozenset({"ok", "unauthorized", "not_found", "invalid_argument", "unavailable", "conflict"})
# Runtime and member error codes by outcome. The runtime's own "unauthorized"
# is a rejected plugin token, an installation problem rather than the user's.
ERROR_CODES = {
    "not_found": "not_found", "missing_profile": "not_found", "server_not_found": "not_found",
    "name_conflict": "conflict", "online": "conflict", "offline": "conflict", "not_online": "conflict",
    "identity_changed": "conflict",
    "invalid_name": "invalid_argument", "invalid_request": "invalid_argument", "unknown_method": "invalid_argument",
    "unauthorized": "unavailable", "unavailable": "unavailable", "protocol": "unavailable",
    "runtime_error": "unavailable", "disconnect_failed": "unavailable",
}


def error_code(error: BaseException) -> str:
    """The outcome code for an error that ended an operation."""
    code = getattr(error, "code", None)
    if isinstance(error, RuntimeControlError) or code in ERROR_CODES:
        return ERROR_CODES.get(code, "unavailable")
    if code in OUTCOME_CODES:
        return code
    return "unavailable" if isinstance(error, RuntimeError) else "invalid_argument"


# Bedrock formatting codes, equal to endstone.ColorFormat values; spelled out
# so this module stays importable without Endstone. Only players see them:
# console and log delivery strip them (see strip_colors).
GOLD = "§6"
DARK_AQUA = "§3"
GRAY = "§7"
AQUA = "§b"
RED = "§c"
YELLOW = "§e"
RESET = "§r"

HEADING = GOLD  # help page titles and topic headings
SYNTAX = AQUA  # command syntax
DIM = GRAY  # <placeholders>, [optional] parts and separators
PREFIX = DARK_AQUA  # "Endbot: <Bot>:" reply prefix
ERROR = RED
WARNING = YELLOW

_COLOR_CODE = re.compile("§.?", re.DOTALL)


def strip_colors(text: str) -> str:
    """Return ``text`` without Bedrock formatting codes."""
    return _COLOR_CODE.sub("", text)


def reply(detail: str, name: str | None = None, color: str = "") -> str:
    """Format one reply as ``Endbot: <Bot>: <detail>`` with a colored prefix."""
    prefix = f"Endbot: {name}:" if name else "Endbot:"
    return f"{PREFIX}{prefix}{RESET} {color}{detail}{RESET if color else ''}"


def _language(source) -> str:
    """The reply language a command source captured; console and tests default to English."""
    return getattr(source, "language", None) or "en"


def color_syntax(text: str) -> str:
    """Color command syntax, dimming ``<placeholder>`` and ``[optional]`` parts."""
    parts, depth = [SYNTAX], 0
    for character in text:
        if character in "<[":
            if depth == 0:
                parts.append(DIM)
            depth += 1
        parts.append(character)
        if character in ">]" and depth:
            depth -= 1
            if depth == 0:
                parts.append(SYNTAX)
    if parts[-1] == SYNTAX:
        parts.pop()
    parts.append(RESET)
    return "".join(parts)


@dataclass(frozen=True, slots=True)
class BotCommand:
    operation: str
    name: str | None = None
    parameters: dict[str, object] = field(default_factory=dict)


# States in which a Bot already holds (or is about to hold) a server slot.
ACTIVE_BOT_STATES = frozenset({"online", "connecting", "reconnecting"})


@dataclass(frozen=True, slots=True)
class CommandResult:
    handled: bool
    messages: tuple[str, ...] = ()
    # Set for results worth a server console log entry (e.g. destroy continuous/stop).
    log_level: str | None = None
    # A management screen (/botgroup, /botscript) for a player. Operations
    # never set it: a menu outcome is data, and menu_views builds its screen.
    view: object | None = None
    # English wording for the log when ``messages`` are in another language.
    log_messages: tuple[str, ...] | None = None
    accepted: bool = True
    data: object | None = None
    # One of OUTCOME_CODES; "ok" exactly when accepted.
    code: str = "ok"

    @property
    def message(self) -> str | None:
        return self.messages[0] if self.messages else None


@dataclass(frozen=True, slots=True)
class HelpTopic:
    """Structured help for one /bot operation.

    ``syntaxes`` must stay in lockstep with :func:`parse_command`; the paginated
    index shows ``syntaxes[0]`` while the detail page lists every form.
    """

    operation: str
    summary: str
    syntaxes: tuple[str, ...] = ()
    details: tuple[str, ...] = ()
    named: bool = True


HELP_TOPICS: dict[str, HelpTopic] = {
    "spawn": HelpTopic(
        "spawn",
        "create or connect a Bot",
        ("spawn", "spawn at <x> <y> <z> [facing <yaw> <pitch>] [in <dimension>]"),
        (
            "First spawn creates a profile and places the arriving Bot at its caller.",
            "From the server console, spawn without coordinates uses the world spawn.",
            "Spawning an offline profile reuses its identity with the new placement.",
        ),
    ),
    "resume": HelpTopic(
        "resume",
        "reconnect a profile without moving it",
        ("resume",),
        ("Keeps the stored identity and lets world persistence win.",),
    ),
    "reconnect": HelpTopic(
        "reconnect",
        "restart the session in place",
        ("reconnect",),
        ("Tears down and reconnects without changing identity or location.",),
    ),
    "despawn": HelpTopic(
        "despawn",
        "take a Bot offline (stays registered)",
        ("despawn",),
        ("Cancels automatic reconnect; resume or spawn brings it back.",),
    ),
    "forget": HelpTopic(
        "forget",
        "remove an offline profile registration",
        ("forget",),
        ("Requires an offline Bot; BDS player data is left alone.",),
    ),
    "rename": HelpTopic(
        "rename",
        "rename a Bot, keeping its identity",
        ("rename <new-name>",),
        ("Reconnects automatically so the login name changes.",),
    ),
    "status": HelpTopic(
        "status",
        "show lifecycle state and position",
        ("status",),
        ("Combines runtime state with the live position when spawned.",),
    ),
    "teleport": HelpTopic(
        "teleport",
        "teleport a Bot",
        (
            "tp <player>",
            "tp <x> <y> <z> [yaw pitch]",
            "tp <x> <y> <z> facing <player|x y z>",
            "tp in <dimension> <x> <y> <z>",
        ),
        (
            "Uses the server teleport; never dispatches vanilla /tp.",
            "~ coordinates are relative to the command source.",
        ),
    ),
    "move": HelpTopic(
        "move",
        "walk a Bot in a direction",
        ("move forward|backward|left|right|stop", "move forward|backward|left|right for <ticks>"),
        ("Movement acts as held input; stop halts it. for stops it after that many ticks.",),
    ),
    "look": HelpTopic(
        "look",
        "turn a Bot's head",
        ("look <yaw> <pitch>", "look at <x> <y> <z>"),
        ("look at turns toward world coordinates.",),
    ),
    "jump": HelpTopic(
        "jump",
        "make a Bot jump",
        ("jump [once|continuous|interval <ticks>|stop]", "jump [continuous|interval <ticks>] for <ticks>"),
        (
            "Omitting the mode means once, or continuous with for; ticks are 1-72000.",
            "for is how long the input keeps going; once cannot take it.",
        ),
    ),
    "attack": HelpTopic(
        "attack",
        "make a Bot attack",
        ("attack [once|continuous|interval <ticks>|stop]", "attack [continuous|interval <ticks>] for <ticks>",
         "attack @<type|id|nearest>"),
        ("Continuous actions run every input tick; for stops them after that many ticks.",
         "@ turns to the nearest matching entity within 4.5 blocks and attacks it once; players are never targets."),
    ),
    "use": HelpTopic(
        "use",
        "use the held item in air",
        (
            "use [once|continuous|interval <ticks>|stop]",
            "use for <ticks>",
            "use hold <ticks> [interval <ticks>] [for <ticks>]",
        ),
        (
            "use for holds use that long, counted from the first press, then releases.",
            "hold charges each press (bow, trident) and releases; for limits the repeats.",
            "With an empty hand it is a safe no-op; blocks need interact.",
        ),
    ),
    "sprint": HelpTopic(
        "sprint",
        "toggle sprinting",
        ("sprint on|off",),
        ("Emits Bedrock start/stop transitions plus held state.",),
    ),
    "sneak": HelpTopic(
        "sneak",
        "toggle sneaking",
        ("sneak on|off",),
        ("Emits Bedrock start/stop transitions plus held state.",),
    ),
    "hotbar": HelpTopic(
        "hotbar",
        "show or select the hotbar slot",
        ("hotbar [1-9]",),
        ("Slots use human-facing 1-9 numbering through Bedrock equipment.",),
    ),
    "swap": HelpTopic(
        "swap", "exchange two inventory stacks", ("swap <a> <b>",),
        ("Slots are 1-36. An empty side is a move; equipment is excluded. BDS validates the request.",),
    ),
    "hold": HelpTopic(
        "hold", "select an item by exact identifier", ("hold <item>",),
        ("Selects the lowest matching hotbar slot, or swaps the lowest main stack into the selected hotbar slot.",
         "Item IDs accept minecraft: or no prefix. Replies describe the request; BDS validates it.",),
    ),
    "inventory": HelpTopic(
        "inventory",
        "show non-empty inventory and equipment slots",
        ("inventory",),
        ("Slots are hotbar 1-9, main 10-36, offhand, head, chest, legs and feet.",),
    ),
    "radius": HelpTopic(
        "radius",
        "show or set the Bot chunk radius",
        ("radius", "radius <2-32|default>"),
        (
            "Overrides this Bot only, persists across reconnect/restart, and applies live.",
            "default restores the shared runtime setting; BDS may clamp the requested range.",
        ),
    ),
    "interact": HelpTopic(
        "interact",
        "right-click a block or an entity",
        ("interact", "interact <x> <y> <z> [face]", "interact <block type>", "interact @<type|id|nearest>"),
        (
            "No argument right-clicks what the Bot looks at. A block type is the nearest visible one within 6 blocks.",
            "@ picks an entity within 6 blocks, turns to it and uses the held item: feed, breed, shear, name, ride.",
            "Without a face, the side facing the Bot. BDS decides reach and outcome; players are never targets.",
        ),
    ),
    "entities": HelpTopic(
        "entities",
        "list nearby entities",
        ("entities",),
        ("Shows up to 10 non-player entities within 16 blocks with their id and distance.",),
    ),
    "entity": HelpTopic(
        "entity",
        "show one nearby entity in detail",
        ("entity [type|nearest|id]",),
        ("Position, distance, health and name tag of the nearest match within 16 blocks; players are excluded.",),
    ),
    "target": HelpTopic(
        "target",
        "show the block or entity the Bot is looking at",
        ("target",),
        ("Within 6 blocks of the Bot's eyes; the nearer of a block and a non-player entity.",),
    ),
    "block": HelpTopic(
        "block",
        "show one block's type and states",
        ("block <x> <y> <z>",),
        ("~ is relative to the Bot. Any loaded position can be read.",),
    ),
    "blocks": HelpTopic(
        "blocks",
        "find the nearest exposed blocks of a type",
        ("blocks <type> [radius <1-16>] [limit <1-64>] [at <x> <y> <z>]",),
        (
            "Only blocks with a face open to air, water or plants count, as a player could see them.",
            "Radius (1-16) defaults to 8 and limit to 10; results are nearest first.",
            "at <x> <y> <z> scans a sphere around that point instead of the Bot; ~ is relative to the Bot.",
        ),
    ),
    "dismount": HelpTopic(
        "dismount",
        "get off a minecart, boat or animal",
        ("dismount",),
        ("Riding Bots follow their vehicle; status shows what the Bot is riding.",),
    ),
    "destroy": HelpTopic(
        "destroy",
        "break reachable blocks with the held tool (hold left-click)",
        (
            "destroy [once|continuous|stop]",
            "destroy [continuous] for <ticks> [count <blocks>]",
            "destroy [continuous] count <blocks>",
            "destroy <x> <y> <z> [down|up|north|south|west|east]",
            "destroy <block type>",
        ),
        (
            "once (default) breaks one block in sight; x y z breaks that block. ~ is relative to the Bot.",
            "A block type breaks the nearest one the Bot can see within 5 blocks.",
            "continuous holds left-click along the Bot's sight, also after you log off; waits when empty.",
            "Other commands do not cancel it; only destroy stop, stop, death or disconnect end it.",
            "for ends continuous mining after that many ticks, aborting a block in progress.",
            "count ends it after that many broken blocks; waiting costs nothing, and both limits combine.",
            "No interval mode. No automatic walking; BDS controls speed, drops and durability.",
        ),
    ),
    "drop": HelpTopic(
        "drop",
        "request drops from inventory and equipment",
        ("drop [stack|all|<slot>|<item> [count]]",),
        (
            "drop drops one selected item; stack drops the selected stack; a slot drops its whole stack.",
            "Slots: 1-36, offhand, head, chest, legs, feet. Item IDs accept minecraft: or no prefix.",
            "An item without count drops every matching stack; count takes up to that many, lowest slots first.",
            "drop all immediately requests every inventory and equipment stack. BDS validates every request.",
        ),
    ),
    "stop": HelpTopic(
        "stop",
        "halt movement and actions",
        ("stop",),
        ("Clears movement, actions, sprint and sneak; stays connected.",),
    ),
    "ping": HelpTopic(
        "ping",
        "check the command path",
        ("ping",),
        ("Answers pong without touching the runtime.",),
        named=False,
    ),
    "list": HelpTopic(
        "list",
        "list every Bot profile",
        ("list",),
        ("Shows each profile name with its lifecycle state.",),
        named=False,
    ),
    "menu": HelpTopic(
        "menu",
        "open the Bot control forms (players only)",
        ("menu", "<name>", "<name> menu"),
        (
            "/bot or /bot menu lists every Bot; /bot <name> (or <name> menu) opens that Bot's actions directly.",
            "Forms run the same /bot commands, so replies and permissions match typed commands.",
            "Bot and Group operation menus share the same controls; Group management uses /botgroup.",
        ),
        named=False,
    ),
    "help": HelpTopic(
        "help",
        "show this help",
        ("help [page|command]",),
        ("Pages summarize commands; a command name shows detail.",),
        named=False,
    ),
}

HELP_TOPIC_ALIASES = {"tp": "teleport"}

INTERACT_USAGE = ("Use interact, interact <x> <y> <z> [face], interact <block type>, "
                  "or interact @<entity> [continuous|interval <ticks>] [for <ticks>] [count <n>]")
TARGET_REPEAT_USAGE = "Repeat {action} @<entity> with continuous, interval <ticks>, for <ticks> or count <n>"

BLOCKS_USAGE = "Use blocks <type> [radius <1-16>] [limit <1-64>] [at <x> <y> <z>]"
BLOCKS_CLAUSES = {"radius": (1, 16), "limit": (1, 64)}

HELP_PAGES: tuple[tuple[str, ...], ...] = (
    ("spawn", "resume", "reconnect", "despawn", "status", "teleport"),
    ("move", "look", "jump", "attack", "use", "sprint", "sneak", "stop"),
    ("inventory", "hotbar", "swap", "hold", "interact", "destroy", "drop", "radius", "rename", "forget"),
    ("entities", "entity", "target", "block", "blocks", "dismount"),
)

HELP_PAGE_TITLES = (
    "lifecycle, status and teleport",
    "movement and actions",
    "inventory, blocks and Bot settings",
    "looking around, entities and vehicles",
)


def _help_prefix(topic: HelpTopic) -> str:
    return "/bot <name>" if topic.named else "/bot"


def help_page(page: int, lang: str = "en") -> tuple[str, ...]:
    """Render one paginated help index, modeled on vanilla Bedrock /help."""
    total = len(HELP_PAGES)
    title = Text(HELP_PAGE_TITLES[page - 1])
    heading = tr(lang, "--- Endbot help page {page} of {total}: {title} ---", page=page, total=total, title=title)
    lines = [f"{HEADING}{heading}{RESET}"]
    separator = f"{DIM} - {RESET}"
    if page == 1:
        commands = f"{DIM} | {RESET}".join(color_syntax(f"/bot {key}") for key in ("ping", "list", "help"))
        lines.append(f"{commands}{separator}{tr(lang, 'connectivity, profiles, this help')}")
        lines.append(f"{color_syntax('/bot menu')}{separator}{tr(lang, 'open the Bot control forms (players only)')}")
    for key in HELP_PAGES[page - 1]:
        topic = HELP_TOPICS[key]
        syntax = color_syntax(f"{_help_prefix(topic)} {topic.syntaxes[0]}")
        lines.append(f"{syntax}{separator}{tr(lang, topic.summary)}")
    if page < total:
        lines.append(
            f"{DIM}{tr(lang, 'Next page:')}{RESET} {color_syntax(f'/bot help {page + 1}')}"
            f"{DIM}{tr(lang, ' - detail:')}{RESET} {color_syntax('/bot help <command>')}"
        )
    else:
        lines.append(
            f"{DIM}{tr(lang, 'Detail:')}{RESET} {color_syntax('/bot help <command>')}"
            f" {DIM}{tr(lang, '(for example')}{RESET} {color_syntax('/bot help move')}{DIM}){RESET}"
        )
    return tuple(lines)


def help_detail(key: str, lang: str = "en") -> tuple[str, ...]:
    """Render detailed help for one operation, listing every supported syntax."""
    topic = HELP_TOPICS[key]
    lines = [
        f"{HEADING}{tr(lang, 'Endbot help: {operation}', operation=topic.operation)}{RESET}",
        *(color_syntax(f"{_help_prefix(topic)} {syntax}") for syntax in topic.syntaxes),
        *(tr(lang, detail) for detail in topic.details),
    ]
    return tuple(lines)


def _for_suffix(parameters: dict[str, object], lang: str = "en") -> str:
    if "durationTicks" not in parameters:
        return ""
    return tr(lang, " for {ticks} ticks", ticks=parameters["durationTicks"])


def _count_suffix(parameters: dict[str, object], lang: str = "en") -> str:
    if "blockCount" not in parameters:
        return ""
    return tr(lang, " until {blocks} blocks", blocks=parameters["blockCount"])


BLOCK_FACES = {"down": 0, "up": 1, "north": 2, "south": 3, "west": 4, "east": 5}

# Polls (every 4 ticks) a loop may fail to reach the runtime before it ends:
# about one 200-tick runtime lease.
UNREACHABLE_POLL_LIMIT = 50
# Polls a loop may wait for a target before it tells the operator once (about 2 seconds);
# the short gap after each broken block never reaches it.
WAITING_NOTICE_POLLS = 10

# Mining ends caused by a command; their reply already told the operator.
COMMANDED_MINING_STOPS = {"stopped", "continuous mining restarted", "target changed"}

# Runtime mining reasons are protocol-level; status shows player-facing wording.
MINING_REASONS = {
    "BDS changed target block": "block broken or changed",
    "stopped": "stopped",
    "target changed": "replaced by a new target",
    "continuous mining restarted": "replaced by destroy continuous",
    "movement changed": "canceled by move",
    "look changed": "canceled by look",
    "attack started": "canceled by attack",
    "use started": "canceled by use",
    "hotbar changed": "canceled by hotbar change",
    "held item changed": "held item changed",
    "block interaction started": "canceled by interact",
    "item dropped": "canceled by drop",
    "teleported": "canceled by teleport",
    "dimension changed": "canceled by dimension change",
    "respawning": "canceled by death",
    "disconnected": "Bot disconnected",
    "connection closed": "Bot disconnected",
    "target moved out of reach": "target out of reach",
    "mining controller timed out": "lost contact with the Endbot plugin",
    "timed out waiting for BDS to change target": "block did not break within 10 minutes; protected or unbreakable?",
    "duration elapsed": "duration elapsed",
}

# Mining ends that are the requested outcome: notify at info level, not warning.
EXPECTED_MINING_ENDS = {"duration elapsed", "block count reached"}


COMPACT_RELATIVE = re.compile(r"(?:~[-+]?(?:\d+(?:\.\d*)?|\.\d+)?){2,3}")
RELATIVE_PART = re.compile(r"~[-+]?(?:\d+(?:\.\d*)?|\.\d+)?")


def _tokens(arguments: list[str]) -> list[str]:
    tokens: list[str] = []
    for argument in arguments:
        tokens.extend(shlex.split(argument) if any(character.isspace() for character in argument) else [argument])
    # Like vanilla commands, `~~-1~` is three relative coordinates.
    return [part for token in tokens
            for part in (RELATIVE_PART.findall(token) if COMPACT_RELATIVE.fullmatch(token) else [token])]


def _number(value: str, label: str) -> float:
    try:
        result = float(value)
    except ValueError as error:
        raise CommandSyntaxError("{label} must be a number", label=label) from error
    if not math.isfinite(result):
        raise CommandSyntaxError("{label} must be finite", label=label)
    return result


def _coordinate(value: str) -> float | tuple[str, float]:
    if value.startswith("~"):
        return ("relative", _number(value[1:] or "0", Text("relative coordinate")))
    return _number(value, Text("coordinate"))


def _block_type(raw: str) -> str:
    block_type = raw.lower() if ":" in raw else f"minecraft:{raw.lower()}"
    if not re.fullmatch(r"[a-z0-9_.-]+:[a-z0-9_./-]+", block_type):
        raise CommandSyntaxError("Invalid block type")
    return block_type


def _target_token(raw: str) -> dict[str, object]:
    """`@<type|id|nearest>` is an entity; anything else is a block type."""
    if raw.startswith("@"):
        return _entity_selector(raw[1:])
    if raw.isascii() and raw.isdecimal():
        raise CommandSyntaxError("Entities take @: use @{id}", id=raw)
    return {"blockType": _block_type(raw)}


def _selector_text(parameters: dict[str, object]) -> str:
    """The `@` form a selector was typed as, for replies."""
    if "runtimeId" in parameters:
        return f"@{parameters['runtimeId']}"
    if "type" in parameters:
        return "@" + str(parameters["type"]).removeprefix("minecraft:")
    return "@nearest"


def _runtime_selector(parameters: dict[str, object]) -> dict[str, object]:
    return {key: parameters[key] for key in ("runtimeId", "type") if key in parameters}


def _entity_view(entity: dict) -> dict:
    """An entity entry with the `@` selector a command or script passes back."""
    return {**entity, "selector": f"@{entity['runtimeId']}"}


def _targeted_action(name: str, operation: str, remaining: list[str]) -> BotCommand:
    """`attack|interact @<sel> [once|continuous|interval <ticks>] [for <ticks>] [count <n>]`."""
    selector = _entity_selector(remaining[0][1:])
    tokens = list(remaining[1:])
    modes = [token.lower() for token in tokens]
    if not tokens or modes == ["once"]:
        return BotCommand(operation, name, selector)
    if modes == ["stop"]:
        return BotCommand("action", name, {"action": operation, "mode": "stop"})
    count = None
    lowered = [token.lower() for token in tokens]
    if "count" in lowered:
        index = lowered.index("count")
        if index + 1 >= len(tokens) or "count" in lowered[index + 1:]:
            raise CommandSyntaxError(TARGET_REPEAT_USAGE, action=operation)
        value = tokens[index + 1]
        if not value.isascii() or not value.isdecimal() or not 1 <= int(value) <= 10000:
            raise CommandSyntaxError("Count must be an integer from 1 to 10000")
        count = int(value)
        del tokens[index:index + 2]
    if count is not None and (not tokens or tokens == ["once"]):
        tokens = ["continuous"] if not tokens else tokens
    command = _action(name, operation, tokens)
    if command.parameters["mode"] in {"once", "stop"}:
        raise CommandSyntaxError(TARGET_REPEAT_USAGE, action=operation)
    target = _runtime_selector(selector)
    return BotCommand("action", name, {**command.parameters, "target": target,
                                       **({} if count is None else {"count": count})})


def _entity_selector(raw: str) -> dict[str, object]:
    selector = raw.lower()
    if selector == "nearest":
        return {"entity": "nearest"}
    if selector.isascii() and selector.isdecimal() and len(selector) <= 19:
        return {"entity": "id", "runtimeId": str(int(selector))}
    entity_type = selector if ":" in selector else f"minecraft:{selector}"
    if not re.fullmatch(r"[a-z0-9_.-]+:[a-z0-9_./-]+", entity_type):
        raise CommandSyntaxError("Invalid entity type")
    return {"entity": "type", "type": entity_type}


def _item_text(item: dict) -> str:
    text = f"{item['name'].removeprefix('minecraft:')} x{item['count']}"
    durability = item.get("durability")
    return text + (f" ({durability['remaining']}/{durability['max']})" if durability else "")


def _is_coordinate(value: str) -> bool:
    return bool(re.fullmatch(r"~?[-+]?(?:\d+(?:\.\d*)?|\.\d+)?", value)) and value not in {"", "+", "-"}


def _position(tokens: list[str]) -> tuple[dict[str, object], list[str]]:
    if len(tokens) < 3:
        raise CommandSyntaxError("Expected x y z coordinates")
    return {"coordinates": [_coordinate(value) for value in tokens[:3]]}, tokens[3:]


def _placement(tokens: list[str]) -> dict[str, object]:
    position, remaining = _position(tokens)
    while remaining:
        clause = remaining.pop(0).lower()
        if clause == "facing" and len(remaining) >= 2:
            position["rotation"] = [_number(remaining.pop(0), "yaw"), _number(remaining.pop(0), "pitch")]
        elif clause == "in" and remaining:
            position["dimension"] = remaining.pop(0)
        else:
            raise CommandSyntaxError("Spawn syntax: at <x> <y> <z> [facing <yaw> <pitch>] [in <dimension>]")
    return position


def _ticks(value: str, label: str) -> int:
    try:
        ticks = int(value)
    except ValueError as error:
        raise CommandSyntaxError("{label} must be an integer number of ticks", label=label) from error
    if not 1 <= ticks <= 72_000:
        raise CommandSyntaxError("{label} must be between 1 and 72000 ticks", label=label)
    return ticks


def _blocks(value: str, label: str) -> int:
    try:
        blocks = int(value)
    except ValueError as error:
        raise CommandSyntaxError("{label} must be an integer number of blocks", label=label) from error
    if not 1 <= blocks <= 72_000:
        raise CommandSyntaxError("{label} must be between 1 and 72000 blocks", label=label)
    return blocks


TICK_CLAUSES = {
    "interval": ("intervalTicks", Text("Action interval")),
    "hold": ("holdTicks", Text("Hold")),
    "for": ("durationTicks", Text("Duration")),
}

# destroy takes limits instead of a target block when it is continuous: `for`
# counts input ticks from the command, `count` counts completed blocks. Order
# is free, each at most once, and both imply continuous when no mode is given.
DESTROY_CLAUSES = {
    "for": ("durationTicks", Text("Duration"), _ticks),
    "count": ("blockCount", Text("Block count"), _blocks),
}

DESTROY_USAGE = "destroy accepts once, continuous, stop, [continuous] for <ticks> [count <blocks>], or x y z [face]"
DESTROY_SINGLE_BLOCK = "destroy for <ticks> and count <blocks> run continuous mining; one block has no duration"


def _action(name: str, action: str, remaining: list[str]) -> BotCommand:
    """Parse ``[once|continuous|stop]`` plus ``interval``/``hold``/``for`` clauses.

    ``for`` is how long the input keeps going, so it implies continuous when no
    mode is given and is rejected with once. ``hold`` (use only) presses use for
    that many ticks per trigger, then releases. Clauses may appear in any order.
    """
    hold = " [hold <ticks>]" if action == "use" else ""
    usage = "{action} accepts [once|continuous|interval <ticks>|stop]{hold} [for <ticks>]"
    tokens = [value.lower() for value in remaining]
    mode = tokens.pop(0) if tokens and tokens[0] in {"once", "continuous", "stop"} else None
    parameters: dict[str, object] = {}
    while tokens:
        clause = tokens.pop(0)
        if clause not in TICK_CLAUSES or (clause == "hold" and action != "use"):
            raise CommandSyntaxError(usage, action=action, hold=hold)
        key, label = TICK_CLAUSES[clause]
        if key in parameters or not tokens:
            raise CommandSyntaxError(usage, action=action, hold=hold)
        parameters[key] = _ticks(tokens.pop(0), label)
    if mode == "stop" and parameters:
        raise CommandSyntaxError("{action} stop takes no ticks", action=action)
    if "intervalTicks" in parameters:
        if mode is not None:
            raise CommandSyntaxError("{action} interval replaces once/continuous", action=action)
        mode = "interval"
    elif mode is None:
        mode = "continuous" if "durationTicks" in parameters else "once"
    if mode == "once" and "durationTicks" in parameters:
        raise CommandSyntaxError(
            "{action} once runs one time; use continuous or interval with for <ticks>", action=action
        )
    if parameters.get("intervalTicks", math.inf) <= parameters.get("holdTicks", 0):
        raise CommandSyntaxError("Use interval must be longer than hold")
    return BotCommand("action", name, {"action": action, "mode": mode, **parameters})


def _teleport(name: str, tokens: list[str]) -> BotCommand:
    if not tokens:
        raise CommandSyntaxError("Teleport requires a player or destination")
    dimension = None
    if tokens[0].lower() == "in":
        if len(tokens) < 5:
            raise CommandSyntaxError("Teleport dimension syntax: in <dimension> <x> <y> <z>")
        dimension, tokens = tokens[1], tokens[2:]
    if len(tokens) == 1:
        return BotCommand("teleport", name, {"target": tokens[0]})
    destination, remaining = _position(tokens)
    if dimension:
        destination["dimension"] = dimension
    if len(remaining) == 2 and remaining[0].lower() != "facing":
        destination["rotation"] = [_number(remaining[0], "yaw"), _number(remaining[1], "pitch")]
    elif remaining and remaining[0].lower() == "facing":
        facing = remaining[1:]
        if len(facing) == 1:
            destination["facingTarget"] = facing[0]
        elif len(facing) == 3:
            destination["facingCoordinates"] = [_coordinate(value) for value in facing]
        else:
            raise CommandSyntaxError("facing requires a player or x y z")
    elif remaining:
        raise CommandSyntaxError("Invalid teleport suffix")
    return BotCommand("teleport", name, destination)


def _help_topic(raw: str) -> BotCommand:
    token = raw.lower()
    if token == "advanced":
        # Unadvertised compatibility alias for the retired dense help mode.
        return BotCommand("help", parameters={"page": len(HELP_PAGES)})
    if token.isdigit():
        page = int(token)
        if 1 <= page <= len(HELP_PAGES):
            return BotCommand("help", parameters={"page": page})
        raise CommandSyntaxError("Help page must be from 1 to {total}", total=len(HELP_PAGES))
    key = HELP_TOPIC_ALIASES.get(token, token)
    if key in HELP_TOPICS:
        return BotCommand("help", parameters={"topic": key})
    raise CommandSyntaxError("Unknown help topic '{topic}'. Use /bot help <page> or /bot help <command>", topic=raw)


def parse_command(arguments: list[str]) -> BotCommand:
    tokens = _tokens(arguments)
    if not tokens:
        return BotCommand("menu", parameters={"default": True})
    first = tokens[0].lower()
    if first == "ping" and len(tokens) == 1:
        return BotCommand("ping")
    if first == "help":
        if len(tokens) == 1:
            return BotCommand("help", parameters={"page": 1})
        if len(tokens) == 2:
            return _help_topic(tokens[1])
        raise CommandSyntaxError("Use /bot help <page> or /bot help <command>")
    if first == "list" and len(tokens) == 1:
        return BotCommand("list")
    if first == "menu" and len(tokens) == 1:
        return BotCommand("menu")
    if len(tokens) == 1:
        # A bare target opens its menu, matching `/bot` and `/bot menu` for the
        # list. Group targets reach this parser too (`/bot #miners`).
        return BotCommand("menu", tokens[0])
    if len(tokens) < 2:
        raise CommandSyntaxError("Expected /bot <name> <action>")
    name, operation, remaining = tokens[0], tokens[1].lower(), tokens[2:]
    operation = ALIASES.get(operation, operation)
    if operation not in OPERATIONS:
        raise CommandSyntaxError("Unknown or invalid Bot action: {operation}", operation=operation)
    if operation in {
        "status", "inventory", "entities", "entity", "target", "resume", "reconnect", "despawn", "forget", "stop",
        "menu", "dismount",
    } and not remaining:
        return BotCommand(operation, name)
    if operation == "spawn":
        if not remaining:
            return BotCommand(operation, name)
        if remaining[0].lower() == "at":
            return BotCommand(operation, name, _placement(remaining[1:]))
        raise CommandSyntaxError("Spawn accepts no arguments or: at <x> <y> <z> ...")
    if operation == "rename" and len(remaining) == 1:
        return BotCommand(operation, name, {"newName": remaining[0]})
    if operation == "tp":
        return _teleport(name, remaining)
    if operation == "move" and remaining and remaining[0].lower() in {"forward", "backward", "left", "right", "stop"}:
        direction = remaining[0].lower()
        if len(remaining) == 1:
            return BotCommand(operation, name, {"direction": direction})
        if len(remaining) == 3 and remaining[1].lower() == "for" and direction != "stop":
            duration = _ticks(remaining[2], Text("Duration"))
            return BotCommand(operation, name, {"direction": direction, "durationTicks": duration})
        raise CommandSyntaxError("move accepts forward|backward|left|right [for <ticks>], or stop")
    if operation == "look" and len(remaining) == 2:
        return BotCommand(
            operation,
            name,
            {"yaw": _number(remaining[0], "yaw"), "pitch": _number(remaining[1], "pitch")},
        )
    if operation == "look" and len(remaining) == 4 and remaining[0].lower() == "at":
        return BotCommand(operation, name, {"targetCoordinates": [_coordinate(value) for value in remaining[1:]]})
    if operation == "swap":
        if len(remaining) != 2:
            raise CommandSyntaxError("Use swap <a> <b>")
        if any(not re.fullmatch(r"[1-9]|[12][0-9]|3[0-6]", value) for value in remaining):
            raise CommandSyntaxError("Inventory slots must be from 1 to 36")
        a, b = map(int, remaining)
        if a == b:
            raise CommandSyntaxError("Swap requires two different slots")
        return BotCommand(operation, name, {"a": a, "b": b})
    if operation == "hold":
        if len(remaining) != 1:
            raise CommandSyntaxError("Use hold <item>")
        item = remaining[0]
        if not re.fullmatch(r"(?:minecraft:)?[a-z0-9_./-]+", item):
            raise CommandSyntaxError("Invalid hold item identifier")
        return BotCommand(operation, name, {"item": item})
    if operation == "hotbar" and not remaining:
        return BotCommand(operation, name)
    if operation == "hotbar" and len(remaining) == 1:
        try:
            slot = int(remaining[0])
        except ValueError as error:
            raise CommandSyntaxError("Hotbar slot must be an integer from 1 to 9") from error
        if not 1 <= slot <= 9:
            raise CommandSyntaxError("Hotbar slot must be from 1 to 9")
        return BotCommand(operation, name, {"slot": slot})
    if operation == "radius":
        if not remaining:
            return BotCommand(operation, name)
        if len(remaining) != 1:
            raise CommandSyntaxError("radius accepts an integer from 2 to 32, or default")
        if remaining[0].lower() == "default":
            return BotCommand(operation, name, {"value": "default"})
        try:
            radius = int(remaining[0])
        except ValueError as error:
            raise CommandSyntaxError("radius accepts an integer from 2 to 32, or default") from error
        if not 2 <= radius <= 32:
            raise CommandSyntaxError("Chunk radius must be from 2 to 32")
        return BotCommand(operation, name, {"value": radius})
    if operation == "block":
        if len(remaining) != 3:
            raise CommandSyntaxError("Use block <x> <y> <z>")
        position, unused = _position(remaining)
        assert not unused
        return BotCommand(operation, name, position)
    if operation == "blocks":
        if not remaining:
            raise CommandSyntaxError(BLOCKS_USAGE)
        block_type = remaining[0].lower()
        block_type = block_type if ":" in block_type else f"minecraft:{block_type}"
        if not re.fullmatch(r"[a-z0-9_.-]+:[a-z0-9_./-]+", block_type):
            raise CommandSyntaxError("Invalid block type")
        parameters = {"type": block_type, "radius": 8, "limit": 10}
        tokens = remaining[1:]
        seen = set()
        while tokens:
            clause = tokens.pop(0).lower()
            if clause not in BLOCKS_CLAUSES and clause != "at" or clause in seen or not tokens:
                raise CommandSyntaxError(BLOCKS_USAGE)
            seen.add(clause)
            if clause == "at":
                # Scan centre: ~ is relative to the Bot, as for block.
                if len(tokens) < 3:
                    raise CommandSyntaxError(BLOCKS_USAGE)
                parameters["coordinates"] = [_coordinate(tokens.pop(0)) for _ in range(3)]
                continue
            low, high = BLOCKS_CLAUSES[clause]
            value = tokens.pop(0)
            if not value.isascii() or not value.isdecimal() or not low <= int(value) <= high:
                raise CommandSyntaxError("{clause} must be an integer from {low} to {high}",
                                         clause=clause, low=low, high=high)
            parameters[clause] = int(value)
        return BotCommand(operation, name, parameters)
    # Like tp, the argument shape picks the target: one token is an entity,
    # coordinates and a face are a block.
    # attack, destroy and interact share target forms: nothing is the crosshair,
    # `<x> <y> <z> [face]` a block, a bare type the nearest visible block and
    # `@<type|id|nearest>` an entity.
    if operation == "entity" and len(remaining) == 1:
        if not remaining[0].startswith("@"):
            raise CommandSyntaxError("Use entity @<type|id|nearest>")
        return BotCommand(operation, name, _entity_selector(remaining[0][1:]))
    if operation == "interact" and not remaining:
        return BotCommand(operation, name, {"crosshair": True})
    if operation == "interact" and [value.lower() for value in remaining] == ["stop"]:
        return BotCommand("action", name, {"action": "interact", "mode": "stop"})
    if operation in {"attack", "interact"} and remaining and remaining[0].startswith("@"):
        return _targeted_action(name, operation, remaining)
    if operation == "interact" and len(remaining) == 1:
        return BotCommand(operation, name, _target_token(remaining[0]))
    if operation == "interact" and len(remaining) == 3:
        target, unused = _position(remaining)
        assert not unused
        return BotCommand(operation, name, target)
    if operation == "interact" and len(remaining) == 4 and remaining[-1].lower() in BLOCK_FACES:
        target, unused = _position(remaining[:3])
        assert not unused
        return BotCommand(
            operation,
            name,
            {**target, "face": BLOCK_FACES[remaining[-1].lower()], "faceName": remaining[-1].lower()},
        )
    if operation == "interact":
        raise CommandSyntaxError(INTERACT_USAGE)
    if operation == "destroy":
        modes = [value.lower() for value in remaining]
        if not remaining or modes == ["once"]:
            return BotCommand(operation, name)
        if len(remaining) == 1 and remaining[0].startswith("@"):
            raise CommandSyntaxError("destroy breaks blocks; use attack @<entity>")
        if modes[0] not in {"once", "continuous", "stop", "interval", *DESTROY_CLAUSES} and not (
                len(remaining) in {3, 4} and _is_coordinate(remaining[0])):
            block_type = _block_type(remaining[0])
            if len(remaining) == 1:
                return BotCommand(operation, name, {"blockType": block_type})
            loop = parse_command([name, "destroy", *remaining[1:]])
            if loop.parameters.get("mode") != "continuous":
                raise CommandSyntaxError("Use destroy <block type> [continuous] [for <ticks>] [count <blocks>]")
            return BotCommand(operation, name, {**loop.parameters, "blockType": block_type})
        if modes in (["continuous"], ["stop"]):
            return BotCommand(operation, name, {"mode": modes[0]})
        if modes and modes[0] == "interval":
            raise CommandSyntaxError(
                "destroy has no interval mode; destroy continuous waits for the next block automatically"
            )
        # for and count limit continuous mining; both imply it without a mode.
        tokens = list(remaining)
        mode = tokens.pop(0).lower() if tokens[0].lower() in {"once", "continuous", "stop"} else None
        parameters: dict[str, object] = {}
        while tokens and tokens[0].lower() in DESTROY_CLAUSES:
            key, label, measure = DESTROY_CLAUSES[tokens.pop(0).lower()]
            if key in parameters or not tokens or tokens[0].lower() in DESTROY_CLAUSES:
                raise CommandSyntaxError(DESTROY_USAGE)
            parameters[key] = measure(tokens.pop(0), label)
        if parameters:
            if mode not in (None, "continuous"):
                raise CommandSyntaxError(DESTROY_SINGLE_BLOCK)
            if tokens:
                raise CommandSyntaxError(DESTROY_USAGE)
            return BotCommand(operation, name, {"mode": "continuous", **parameters})
        # Coordinates are their own command shape: a leading mode word must not
        # turn `stop 1 64 2` into a single-block break.
        if mode is None and len(tokens) in {3, 4} and not any(value.lower() in DESTROY_CLAUSES for value in tokens):
            target, unused = _position(tokens[:3])
            assert not unused
            if len(tokens) == 4:
                if tokens[3].lower() not in BLOCK_FACES:
                    raise CommandSyntaxError("Invalid block face")
                target["face"] = BLOCK_FACES[tokens[3].lower()]
            return BotCommand(operation, name, target)
        if any(value.lower() in DESTROY_CLAUSES for value in tokens):
            raise CommandSyntaxError(DESTROY_SINGLE_BLOCK)
        raise CommandSyntaxError(DESTROY_USAGE)
    if operation == "drop":
        if not remaining or remaining == ["stack"]:
            return BotCommand(operation, name, {"stack": bool(remaining)})
        if len(remaining) > 2:
            raise CommandSyntaxError("Use drop [stack|all|<slot>|<item> [count]]")
        selector = remaining[0]
        slots = {str(slot) for slot in range(1, 37)} | {"offhand", "head", "chest", "legs", "feet"}
        if selector in slots or selector == "all":
            if len(remaining) != 1:
                raise CommandSyntaxError("A slot or all does not accept a count")
            return BotCommand(operation, name, {"selector": selector})
        if selector == "stack":
            raise CommandSyntaxError("A slot or all does not accept a count")
        if selector.isascii() and selector.isdecimal():
            # `drop 0` or `drop 37` is a slot typo, never an item called "0".
            raise CommandSyntaxError("Drop slots are 1 to 36, offhand, head, chest, legs or feet")
        item = selector if ":" in selector else f"minecraft:{selector}"
        if not re.fullmatch(r"minecraft:[a-z0-9_./-]+", item):
            raise CommandSyntaxError("Invalid drop item identifier")
        parameters = {"selector": item}
        if len(remaining) == 2:
            token = remaining[1]
            if not token.isascii() or not token.isdecimal() or len(token) > 5 or not 1 <= int(token) <= 10455:
                raise CommandSyntaxError("Drop count must be an integer from 1 to 10455")
            parameters["count"] = int(token)
        return BotCommand(operation, name, parameters)
    if operation in {"jump", "attack", "use"}:
        return _action(name, operation, remaining)
    if operation in {"sprint", "sneak"} and len(remaining) == 1 and remaining[0].lower() in {"on", "off"}:
        return BotCommand("flag", name, {"flag": operation, "value": remaining[0].lower() == "on"})
    raise CommandSyntaxError("Unknown or invalid Bot action: {operation}", operation=operation)


class BotCommandService:
    """Execute parsed commands against transport and world adapters."""

    def __init__(self, control=None, world=None, limits=None, capacity=None) -> None:
        self.control = control
        self.world = world
        # Slot budget: `capacity(bot_ids)` returns (max_players, humans_online).
        # Without it (tests, unusual embeddings) no seat check is applied.
        self.limits = limits if limits is not None else BotLimits()
        self.capacity = capacity
        # Shared by the command worker and the mining poll worker. Entries are
        # (name, identity, loopId, source, position): a continuous loop has a
        # loopId; a single-block job has loopId None and is watched only to
        # report an abnormal end. Entries are replaced, never mutated, so
        # identity checks detect a newer loop or job.
        self._mining_loops = {}
        self._mining_lock = threading.Lock()
        # Consecutive polls that could not reach the runtime, per loop entry.
        self._mining_unreachable = {}
        # Consecutive polls a loop found nothing to break, per loop entry.
        self._mining_waiting = {}

    def check_seat(self, name: str) -> None:
        """Refuse a new Bot session that would take a slot kept for players.

        Counts online/connecting Bots (excluding the addressed one) plus online
        humans, so a Group or script spawn fails per member instead of filling
        the server. Existing Bots are never despawned here; the budget applies
        to new sessions only.
        """
        limits, capacity, control = self.limits, self.capacity, self.control
        if capacity is None or control is None or not limits.enabled:
            return
        profiles = bot_profiles(control)
        addressed = name.casefold()
        identity = ""
        for profile in profiles:
            if str(profile.get("name", "")).casefold() == addressed:
                identity = str(profile.get("identityId", ""))
                break
        active = sum(
            1
            for profile in profiles
            if str(profile.get("identityId", "")) != identity
            and profile.get("connectionState") in ACTIVE_BOT_STATES
        )
        if limits.max_bots > 0 and active + 1 > limits.max_bots:
            raise LocalizedError(
                "Bot limit reached: {bots} online or connecting, max-bots is {limit}",
                bots=active,
                limit=limits.max_bots,
            )
        bot_ids = frozenset(str(profile["identityId"]) for profile in profiles if profile.get("identityId"))
        max_players, humans = capacity(bot_ids)
        room = max_players - limits.reserved_player_slots
        if room <= 0:
            raise LocalizedError(
                "No Bot can join: all {players} slots are kept for players (reserved-player-slots = {reserved})",
                players=max_players,
                reserved=limits.reserved_player_slots,
            )
        used = active + humans
        if used + 1 > room:
            raise LocalizedError(
                "No free slot for another Bot: {used} of {players} slots in use, {reserved} kept for players",
                used=used,
                players=max_players,
                reserved=limits.reserved_player_slots,
            )

    def poll_mining(self, authorize) -> list[tuple[object, str, str, str]]:
        """Renew/retarget loops; return (source, level, message, log message) notices.

        Notices cover abnormal ends and a loop that has started waiting for a
        target. Commanded stops already replied, and each successfully broken
        block would be far too chatty. The message is in the source's language;
        the log message is English.
        """
        notices = []
        with self._mining_lock:
            loops = tuple(self._mining_loops.items())
        for key, entry in loops:
            name, identity, loop_id, source, position = entry
            try:
                if not authorize(source):
                    if self._end_mining_loop(key, entry):
                        notices.append(self._mining_notice(entry, "warning", "control permission revoked"))
                    continue
                status = self.control.request("status", name=name)
                self._mining_unreachable.pop(key, None)  # reached the runtime again
                mining = status.get("mining") or {}
                if status["identityId"] != identity:
                    if self._take_mining_loop(key, entry):
                        notices.append(self._mining_notice(entry, "warning", "Bot was renamed or removed"))
                    continue
                if loop_id is None:
                    reason = self._single_mining_end(status, mining, position)
                elif mining.get("loopId") != loop_id:
                    reason = self._mining_end_reason(status, mining)
                else:
                    waiting = self._continue_mining_loop(name, identity, loop_id, source, mining, position)
                    if self._waited(key, entry, waiting) == WAITING_NOTICE_POLLS:
                        notices.append(self._waiting_notice(entry))
                    continue
                if reason is False:
                    continue  # still breaking
                if self._take_mining_loop(key, entry) and reason:
                    level = "info" if reason in EXPECTED_MINING_ENDS else "warning"
                    notices.append(self._mining_notice(entry, level, reason))
            except RuntimeControlError as error:
                # Windows loopback can refuse a fresh connection (WinError 10048)
                # under rapid reconnects. Retry for about one runtime lease
                # (200 ticks = 50 polls) before giving up on the runtime.
                if error.code == "unavailable" and self._unreachable(key, entry) < UNREACHABLE_POLL_LIMIT:
                    continue
                if self._end_mining_loop(key, entry):
                    notices.append(self._mining_notice(entry, "warning", error))
            except (RuntimeError, ValueError) as error:
                if self._end_mining_loop(key, entry):
                    notices.append(self._mining_notice(entry, "warning", error))
        return notices

    @staticmethod
    def _mining_notice(entry, level: str, reason) -> tuple[object, str, str, str]:
        """A notice for an ended job; ``reason`` is an English reason or an error."""
        name, _, loop_id, source, _ = entry
        command = "destroy continuous" if loop_id else "destroy"
        color = WARNING if level == "warning" else ""

        def render(lang: str) -> str:
            text = localize_error(reason, lang) if isinstance(reason, BaseException) else tr(lang, reason)
            return reply(tr(lang, "{command} stopped: {reason}", command=command, reason=text), name, color)

        return (source, level, render(_language(source)), render("en"))

    @staticmethod
    def _waiting_notice(entry) -> tuple[object, str, str, str]:
        """A one-time notice that a loop is idle: nothing in sight, or the runtime refused what was."""
        name, _, _, source, block_type = entry

        def render(lang: str) -> str:
            if block_type:
                text = tr(lang, "destroy is waiting: no {block} it can break within 5 blocks; move the Bot closer",
                          block=block_type.removeprefix("minecraft:"))
            else:
                text = tr(lang, "destroy continuous is waiting: no block it can break within 5 blocks")
            return reply(text, name, "")

        return (source, "info", render(_language(source)), render("en"))

    def _waited(self, key, entry, waiting) -> int:
        """Consecutive waiting polls of this loop entry; any target resets it."""
        previous, count = self._mining_waiting.get(key, (None, 0))
        count = count + 1 if waiting and previous is entry else int(bool(waiting))
        self._mining_waiting[key] = (entry, count)
        return count

    def _unreachable(self, key, entry) -> int:
        previous, count = self._mining_unreachable.get(key, (None, 0))
        count = count + 1 if previous is entry else 1
        self._mining_unreachable[key] = (entry, count)
        return count

    def _continue_mining_loop(self, name, identity, loop_id, source, mining, block_type=None) -> bool:
        """Renew the loop's lease and retarget it; True when it has nothing to break."""
        target = None
        if mining.get("state") != "active":
            # Sight retargeting needs no controller; it may have logged off.
            if block_type:
                # A typed loop turns to the nearest visible block of its type; none in
                # reach means waiting, like an empty sight.
                try:
                    cell = self.world.reachable_block(identity, block_type, 5.0)
                    target = {**self.world.resolve_mining(identity, {"coordinates": cell}, None), "turn": True}
                except ValueError:
                    target = None
            else:
                target = self.world.resolve_mining(identity, {"continuous": True}, None)
        try:
            self.control.request("mine", name=name, mode="continue", loopId=loop_id, **(target or {}))
        except RuntimeControlError as error:
            if not target or error.code in {"unavailable", "protocol", "unauthorized"}:
                raise
            # The runtime rechecks reach/dimension from its own predicted
            # position. A rejected target means nothing is mineable yet:
            # keep the lease and retarget on the next poll.
            self.control.request("mine", name=name, mode="continue", loopId=loop_id)
            return True
        return mining.get("state") != "active" and target is None

    @staticmethod
    def _mining_end_reason(status, mining) -> str | None:
        """Player-facing reason a job ended, or None for a commanded stop."""
        reason = str(mining.get("reason") or "")
        if reason in COMMANDED_MINING_STOPS:
            return None
        if not mining and status.get("connectionState") != "online":
            return "Bot disconnected"
        return str(MINING_REASONS.get(reason, reason or "ended"))

    @classmethod
    def _single_mining_end(cls, status, mining, position):
        """False while the watched block is still breaking; else an end reason."""
        if mining.get("loopId") or list(mining.get("blockPosition") or []) != position:
            return None  # replaced by another mining command
        if mining.get("state") == "active":
            return False
        if mining.get("state") == "completed":
            return None
        return cls._mining_end_reason(status, mining)

    def _take_mining_loop(self, key, entry=None):
        with self._mining_lock:
            if entry is not None and self._mining_loops.get(key) is not entry:
                return None
            return self._mining_loops.pop(key, None)

    def _end_mining_loop(self, key, entry=None):
        entry = self._take_mining_loop(key, entry)
        if entry and entry[2]:
            name, _, loop_id, _, _ = entry
            try:
                self.control.request("mine", name=name, mode="stop", loopId=loop_id)
            except (RuntimeError, ValueError):
                pass  # Runtime lease expiry covers an unreachable control port.
        return entry

    def close(self) -> None:
        with self._mining_lock:
            keys = tuple(self._mining_loops)
        for key in keys:
            self._end_mining_loop(key)

    def execute_operation(self, operation: Operation, sender=None) -> CommandResult:
        """The typed entry: run ``operation`` for the principal ``sender``.

        A malformed operation raises ValueError (or CommandSyntaxError) before
        anything runs; once it is well formed, failures come back as a result,
        exactly as for typed commands. The command grammar stays the semantic
        validator, so both entries accept the same combinations.
        """
        arguments = operation.argv()
        parse_command(arguments)
        return self.execute(arguments, sender)

    def execute(self, arguments: list[str], sender=None) -> CommandResult:
        """The command-text entry: parse ``arguments``, then run them for ``sender``."""
        command = None
        lang = _language(sender)
        try:
            command = parse_command(arguments)
            return self._execute(command, sender, lang)
        except (CommandSyntaxError, RuntimeError, ValueError) as error:
            detail = localize_error(error, lang)
            if command is not None and command.name:
                operation = command.operation
                if operation in {"action", "flag"}:
                    operation = str(command.parameters[operation])
                name = command.name
                detail = tr(lang, "{operation} failed: {error}", operation=operation, error=detail)
            else:
                name = None
                try:
                    tokens = _tokens(arguments)
                except ValueError:
                    tokens = []
                if len(tokens) >= 2 and tokens[0].lower() != "help":
                    # Syntax errors still name the Bot the operator addressed.
                    name = tokens[0]
            return CommandResult(True, (reply(detail, name, ERROR),), accepted=False, code=error_code(error))

    def _execute(self, command: BotCommand, sender, lang: str = "en", *, control=None) -> CommandResult:
        control = self.control if control is None else control

        def say(template: str, bot: str | None = None, /, **params) -> CommandResult:
            return CommandResult(True, (reply(tr(lang, template, **params), bot),))

        def logged(name: str, render) -> CommandResult:
            """A reply also written to the console log, which stays English."""
            return CommandResult(
                True, (reply(render(lang), name),), log_level="info", log_messages=(reply(render("en"), name),)
            )

        if command.operation == "ping":
            # Keep the server-side safety probe independent of runtime health.
            return CommandResult(True, (reply("pong"),))
        if command.operation == "help":
            if "topic" in command.parameters:
                return CommandResult(True, help_detail(command.parameters["topic"], lang))
            return CommandResult(True, help_page(int(command.parameters.get("page", 1)), lang))
        if command.operation == "menu" and getattr(sender, "player_id", None) is None:
            if command.parameters.get("default"):
                return CommandResult(True, help_page(1, lang))
            return say("menu is available only to players; use /bot help")
        if command.operation == "inventory":
            if self.world is None:
                raise ValueError("Bot is not present in the world")
            identity = command.parameters.get("identityId")
            if identity is None and control is not None:
                # Pin the Bot profile's UUID, as other commands do, rather than any player with that name.
                identity = control.request("status", name=command.name)["identityId"]
            snapshot = self.world.inventory(command.name, identity)
            data = {"name": snapshot["name"], "slots": snapshot["slots"]}
            entries = [
                f"{tr(lang, slot['slot'])}: {_item_text(slot)}"
                for slot in snapshot["slots"]
            ]
            text = (tr(lang, "inventory: {items}", items=", ".join(entries)) if entries
                    else tr(lang, "inventory is empty"))
            return CommandResult(True, (reply(text, snapshot["name"]),), data=data)
        if self.control is None:
            raise RuntimeError("runtime control is not configured")
        if command.operation == "menu":
            if command.name is None:
                return CommandResult(True, data={"menu": "bots", "bots": list(bot_profiles(control))})
            status = control.request("status", name=command.name)
            observed = self.world.observe(status["identityId"]) if self.world else None
            return CommandResult(True, data={"menu": "bot", "name": status["name"], "status": status,
                                             "observed": observed,
                                             "summary": self._status_reply(status, observed, lang)})
        if command.operation == "list":
            bots = bot_profiles(control)
            data = {"bots": [{"name": bot["name"], "identityId": bot.get("identityId"),
                              "connectionState": bot["connectionState"]} for bot in bots]}
            if not bots:
                return CommandResult(True, (reply(tr(lang, "no Bot profiles")),), data=data)
            summary = ", ".join(f"{bot['name']} {DIM}[{tr(lang, bot['connectionState'])}]{RESET}" for bot in bots)
            return CommandResult(True, (reply(summary),), data=data)
        if command.operation == "status":
            status = control.request("status", name=command.name)
            observed = self.world.observe(status["identityId"]) if self.world else None
            return CommandResult(True, (self._status_reply(status, observed, lang),),
                                 data=self.script_status(status, observed))
        if command.operation == "spawn":
            existing = None
            try:
                existing = control.request("status", name=command.name)
            except RuntimeControlError as error:
                if error.code != "not_found":
                    raise
            # An already active Bot keeps its seat; only a new session is budgeted.
            if existing is None or existing.get("connectionState") not in ACTIVE_BOT_STATES:
                self.check_seat(command.name)
            placement = command.parameters or self.world.default_spawn(sender)
            if placement is not None:
                placement = self.world.resolve_spawn_placement(placement, sender)
            self.world.assert_name_available(command.name, existing.get("identityId") if existing else None)
            result = control.request("spawn", name=command.name)
            if result.get("alreadyOnline"):
                state = result.get("connectionState")
                if state == "connecting":
                    return say("already connecting", result["name"])
                if state == "reconnecting":
                    return say("already reconnecting", result["name"])
                if state == "online":
                    return say("already online", result["name"])
                # Missing or unknown states stay neutral rather than claiming online.
                return say("already active", result["name"])
            verb = Text("created and connecting" if result.get("created") else "connecting")
            if placement is None:
                # Console spawn without coordinates: BDS places the Bot.
                self.world.clear_pending_placement(result["identityId"])
                where = Text("at the world spawn" if result.get("created") else "where it last was")
                return say("{verb} ({where})", result["name"], verb=verb, where=where)
            self.world.queue_spawn_placement(result["identityId"], placement)
            return say(verb, result["name"])
        if command.operation == "teleport":
            status = control.request("status", name=command.name)
            location = self.world.teleport(status["identityId"], command.parameters, sender)
            return say("teleported to {location}", status["name"], location=location)
        if command.operation == "radius":
            result = control.request("radius", name=command.name, **command.parameters)
            data = {"name": result["name"], "chunkRadius": result["chunkRadius"],
                    "source": "bot" if result.get("chunkRadiusSource") == "bot" else "default",
                    "serverChunkRadius": result.get("serverChunkRadius")}
            return CommandResult(True, (reply(self._radius_summary(result, lang), result["name"]),), data=data)
        if command.operation == "destroy":
            mode = command.parameters.get("mode")
            if mode == "continuous":
                target = {"mode": "start"}  # Runtime IPC name for a continuous loop.
                for limit in ("durationTicks", "blockCount"):
                    if limit in command.parameters:
                        target[limit] = command.parameters[limit]
            elif mode == "stop":
                target = {"mode": "stop"}
            else:
                status = control.request("status", name=command.name)
                parameters = command.parameters
                if "blockType" in parameters:
                    cell = self.world.reachable_block(status["identityId"], parameters["blockType"], 5.0)
                    parameters = {"coordinates": cell}
                target = self.world.resolve_mining(status["identityId"], parameters, sender)
            result = control.request("mine", name=command.name, **target)
            key = command.name.casefold()
            with self._mining_lock:
                self._mining_loops.pop(key, None)
                if mode == "continuous":
                    # The last slot is the block type a typed loop retargets to (None: sight).
                    self._mining_loops[key] = (
                        result["name"],
                        result["identityId"],
                        result["mining"]["loopId"],
                        as_job(sender),
                        command.parameters.get("blockType"),
                    )
                elif mode != "stop":
                    self._mining_loops[key] = (
                        result["name"],
                        result["identityId"],
                        None,
                        as_job(sender),
                        list(target["blockPosition"]),
                    )
            if mode == "continuous":
                return logged(
                    result["name"],
                    lambda lang: tr(
                        lang,
                        "destroy continuous started{duration}{count}; destroy stop to cancel",
                        duration=(f" {command.parameters['blockType'].removeprefix('minecraft:')}"
                                  if "blockType" in command.parameters else "") + _for_suffix(command.parameters, lang),
                        count=_count_suffix(command.parameters, lang),
                    ),
                )
            if mode == "stop":
                return logged(result["name"], lambda lang: tr(lang, "destroy stopped"))
            position = " ".join(str(value) for value in target["blockPosition"])
            return say(
                "destroying {block} at {position}; see status for the result",
                result["name"],
                block=target["blockName"],
                position=position,
            )
        if command.operation == "entities":
            result = control.request("entities", name=command.name)
            entities = result["entities"]
            if entities and self.world is not None:
                ids = [entity["runtimeId"] for entity in entities]
                details = self.world.describe_entities(result["identityId"], ids)
                entities = [{**entity, **details.get(entity["runtimeId"], {})} for entity in entities]
            entities = [_entity_view(entity) for entity in entities]
            data = {"name": result["name"], "identityId": result["identityId"], "entities": entities}
            if not entities:
                return CommandResult(True, (reply(tr(lang, "no entities nearby"), result["name"]),), data=data)
            listed = ", ".join(
                f"{entity['type'].removeprefix('minecraft:')} @{entity['runtimeId']} "
                f"{DIM}({entity['distance']}m){RESET}"
                for entity in entities
            )
            return CommandResult(True, (reply(tr(lang, "nearby: {entities}", entities=listed), result["name"]),),
                                 data=data)
        if command.operation == "entity":
            selector = {key: command.parameters[key] for key in ("runtimeId", "type") if key in command.parameters}
            result = control.request("entities", name=command.name, **selector)
            entities = result["entities"]
            if not entities:
                raise LocalizedError("No matching entity within 16 blocks")
            entity = _entity_view(entities[0])
            if self.world is not None:
                entity = {**entity, **self.world.describe_entities(result["identityId"], [entity["runtimeId"]]).get(
                    entity["runtimeId"], {})}
            fields = [f"{entity['type'].removeprefix('minecraft:')} @{entity['runtimeId']}",
                      f"{entity['distance']}m"]
            position = entity.get("position")
            if position:
                fields.append(f"{position['x']:.1f} {position['y']:.1f} {position['z']:.1f}")
            if entity.get("health") is not None:
                fields.append(tr(lang, "health {health}/{max}", health=f"{entity['health']:g}",
                                 max=f"{entity['maxHealth']:g}"))
            if entity.get("nameTag"):
                fields.append(tr(lang, "named {name}", name=entity["nameTag"]))
            return CommandResult(True, (reply(f"{DIM} | {RESET}".join(fields), result["name"]),),
                                 data={"name": result["name"], "entity": entity})
        if command.operation == "interact" and command.parameters.get("crosshair"):
            if self.world is None:
                raise ValueError("Bot is not present in the world")
            status = control.request("status", name=command.name)
            seen = self.world.target(status["identityId"])
            if seen["kind"] == "entity":
                command = BotCommand("interact", command.name, {"entity": "id",
                                                                "runtimeId": seen["entity"]["runtimeId"]})
            elif seen["kind"] == "block":
                block = seen["block"]
                command = BotCommand("interact", command.name, {"coordinates": [block["x"], block["y"], block["z"]],
                                                                "face": BLOCK_FACES[block["face"]]})
            else:
                raise LocalizedError("looking at nothing within 6 blocks")
        if command.operation in {"interact", "attack"} and "entity" in command.parameters:
            selector = {key: command.parameters[key] for key in ("runtimeId", "type") if key in command.parameters}
            method = "attackEntity" if command.operation == "attack" else "interactEntity"
            result = control.request(method, name=command.name, **selector)
            entity = result["entity"]
            template = ("attack on {entity} @{id} requested" if command.operation == "attack"
                        else "interaction with {entity} @{id} requested")
            return say(template, result["name"], entity=entity["type"].removeprefix("minecraft:"),
                       id=entity["runtimeId"])
        if command.operation == "interact" and "blockType" in command.parameters:
            if self.world is None:
                raise ValueError("Bot is not present in the world")
            status = control.request("status", name=command.name)
            cell = self.world.reachable_block(status["identityId"], command.parameters["blockType"], 6.0)
            command = BotCommand("interact", command.name, {"coordinates": cell})
        if command.operation in {"target", "block", "blocks"}:
            if self.world is None:
                raise ValueError("Bot is not present in the world")
            status = control.request("status", name=command.name)
            identity, name = status["identityId"], status["name"]
            if command.operation == "target":
                data = self.world.target(identity)
                if data["kind"] == "block":
                    block = data["block"]
                    text = tr(lang, "looking at {block} at {position} face {face} ({distance}m)",
                              block=block["type"].removeprefix("minecraft:"), face=block["face"],
                              position=f"{block['x']} {block['y']} {block['z']}", distance=data["distance"])
                elif data["kind"] == "entity":
                    entity = data["entity"]
                    data["entity"] = _entity_view(entity)
                    text = tr(lang, "looking at {entity} @{id} ({distance}m)", id=entity["runtimeId"],
                              entity=entity["type"].removeprefix("minecraft:"), distance=data["distance"])
                else:
                    text = tr(lang, "looking at nothing within 6 blocks")
            elif command.operation == "block":
                data = self.world.block_info(identity, command.parameters["coordinates"])
                states = " ".join(f"{key}={value}" for key, value in data["states"].items())
                text = tr(lang, "block at {position}: {block}", position=f"{data['x']} {data['y']} {data['z']}",
                          block=data["type"].removeprefix("minecraft:"))
                if states:
                    text += f" {DIM}[{states}]{RESET}"
            else:
                parameters = command.parameters
                found = self.world.find_blocks(identity, parameters["type"], parameters["radius"], parameters["limit"],
                                            parameters.get("coordinates"))
                data = {"type": parameters["type"], "radius": parameters["radius"], "blocks": found}
                block_name = parameters["type"].removeprefix("minecraft:")
                if found:
                    listed = ", ".join(f"{entry['x']} {entry['y']} {entry['z']} {DIM}({entry['distance']}m){RESET}"
                                       for entry in found[:10])
                    more = "" if len(found) <= 10 else f" (+{len(found) - 10})"
                    text = tr(lang, "{block}: {positions}", block=block_name, positions=listed) + more
                else:
                    text = tr(lang, "no exposed {block} within {radius} blocks", block=block_name,
                              radius=parameters["radius"])
            return CommandResult(True, (reply(text, name),), data={"name": name, **data})
        if command.operation == "dismount":
            status = control.request("status", name=command.name)
            vehicle = status.get("vehicle") or {}
            exit_position = (self.world.resolve_dismount(status["identityId"], vehicle.get("runtimeId"))
                             if self.world is not None and vehicle else None)
            result = control.request("dismount", name=command.name,
                                     **({"exit": exit_position} if exit_position else {}))
            return say("dismount from {vehicle} requested", result["name"],
                       vehicle=result["vehicle"]["type"].removeprefix("minecraft:"))
        if command.operation == "interact":
            status = control.request("status", name=command.name)
            interaction = self.world.resolve_interaction(status["identityId"], command.parameters, sender)
            result = control.request("interact", name=command.name, **interaction)
        elif command.operation == "hotbar":
            result = control.request("hotbar", name=command.name, **command.parameters)
            text = tr(lang, "hotbar slot {slot} selected" if command.parameters else "hotbar slot {slot}",
                      slot=result["selectedHotbarSlot"])
            data = {"name": result["name"], "selectedSlot": result["selectedHotbarSlot"]}
            if not command.parameters and self.world is not None:
                try:
                    slots = self.world.inventory(result["name"], result.get("identityId"))["slots"]
                except ValueError:
                    slots = None
                if slots is not None:
                    data["slots"] = [slot for slot in slots if slot["slot"].isdecimal() and int(slot["slot"]) <= 9]
                    items = ", ".join(f"{slot['slot']}: {_item_text(slot)}" for slot in data["slots"])
                    text += ": " + (items or tr(lang, "empty"))
            return CommandResult(True, (reply(text, result["name"]),), data=data)
        elif command.operation == "swap":
            result = control.request("swap", name=command.name, **command.parameters)
            return say("swap {a} {b} requested", result["name"], **command.parameters)
        elif command.operation == "hold":
            result = control.request("hold", name=command.name, **command.parameters)
            item = command.parameters["item"]
            if not result["found"]:
                raise LocalizedError("Item {item} was not found", item=item)
            if result["sourceSlot"] <= 9:
                return say("hold {item}: selected hotbar {slot} requested", result["name"],
                           item=item, slot=result["selectedHotbarSlot"])
            return say("hold {item}: moved slot {source} to hotbar {slot} requested", result["name"],
                       item=item, source=result["sourceSlot"], slot=result["selectedHotbarSlot"])
        elif command.operation == "drop":
            result = control.request("drop", name=command.name, **command.parameters)
            selector = command.parameters.get("selector", "selected")
            if selector == "all":
                return say("drop all requested for {slots} slots", result["name"], slots=result["droppedSlots"])
            if selector.startswith("minecraft:"):
                if not result["droppedCount"]:
                    raise LocalizedError("No {item} in the inventory", item=selector.removeprefix("minecraft:"))
                if "count" in command.parameters:
                    return say("drop {item} x{count} requested (asked for {asked})", result["name"],
                               item=selector.removeprefix("minecraft:"), count=result["droppedCount"],
                               asked=command.parameters["count"])
                return say("drop {item} x{count} requested from {slots} slots", result["name"],
                           item=selector.removeprefix("minecraft:"), count=result["droppedCount"],
                           slots=result["droppedSlots"])
            if selector != "selected":
                drops = result.get("drops", [])
                if not drops:
                    return say("drop slot {slot} requested; slot is empty", result["name"], slot=Text(selector))
                drop = drops[0]
                return say("drop slot {slot} {item} x{count} requested", result["name"], slot=Text(selector),
                           item=drop["item"].removeprefix("minecraft:"), count=drop["count"])
            return say(
                "drop stack requested" if command.parameters["stack"] else "drop one item requested", result["name"]
            )
        elif command.operation == "look" and "targetCoordinates" in command.parameters:
            status = control.request("status", name=command.name)
            yaw, pitch = self.world.look_at(status["identityId"], command.parameters["targetCoordinates"])
            result = control.request("look", name=command.name, yaw=yaw, pitch=pitch)
        elif command.operation == "rename":
            status = control.request("status", name=command.name)
            self.world.assert_name_available(command.parameters["newName"], status["identityId"])
            self.world.clear_pending_placement(status["identityId"])
            result = control.request("rename", name=command.name, **command.parameters)
        elif command.operation in {"resume", "reconnect", "despawn", "forget"}:
            status = control.request("status", name=command.name)
            self.world.clear_pending_placement(status["identityId"])
            if (
                command.operation in {"resume", "reconnect"}
                and status.get("connectionState") not in ACTIVE_BOT_STATES
            ):
                self.check_seat(command.name)
            result = control.request(command.operation, name=command.name, **command.parameters)
        else:
            result = control.request(command.operation, name=command.name, **command.parameters)
        name = result.get("name", command.name)
        state = Text(result.get("connectionState", command.operation))
        parameters = command.parameters
        duration = _for_suffix(parameters, lang)
        if command.operation == "move":
            if parameters["direction"] == "stop":
                return say("movement stopped", name)
            direction = parameters["direction"]
            return say("movement set to {direction}{duration}", name, direction=direction, duration=duration)
        if command.operation == "look":
            if "targetCoordinates" not in parameters:
                yaw, pitch = parameters["yaw"], parameters["pitch"]
            return say("look set to yaw={yaw} pitch={pitch}", name, yaw=f"{yaw:.1f}", pitch=f"{pitch:.1f}")
        if command.operation == "action":
            action, mode = parameters["action"], parameters["mode"]
            if "target" in parameters:
                action = f"{action} {_selector_text(parameters['target'])}"
            if "count" in parameters:
                duration += tr(lang, " up to {count} times", count=parameters["count"])
            hold = (
                tr(lang, ": hold {ticks} ticks, then release", ticks=parameters["holdTicks"])
                if "holdTicks" in parameters
                else ""
            )
            if mode == "stop":
                return say("{action} stopped", name, action=action)
            if mode == "interval":
                return say(
                    "{action} started every {interval} ticks{hold}{duration}",
                    name,
                    action=action,
                    interval=parameters["intervalTicks"],
                    hold=hold,
                    duration=duration,
                )
            if mode == "continuous" and hold:
                return say("{action} repeating{hold}{duration}", name, action=action, hold=hold, duration=duration)
            if mode == "continuous" and action == "use" and "durationTicks" in parameters:
                return say(
                    "{action} started for {ticks} ticks, then releases",
                    name,
                    action=action,
                    ticks=parameters["durationTicks"],
                )
            if mode == "continuous":
                return say("{action} continuous started{duration}", name, action=action, duration=duration)
            return say("{action} once requested{hold}", name, action=action, hold=hold)
        if command.operation == "flag":
            return say("{flag} on" if parameters["value"] else "{flag} off", name, flag=parameters["flag"])
        if command.operation == "stop":
            return say("movement and actions stopped (sprint/sneak off)", name)
        if command.operation == "interact":
            position = " ".join(str(value) for value in interaction["blockPosition"])
            face = next(key for key, value in BLOCK_FACES.items() if value == interaction["face"])
            return say("block interaction requested at {position} face={face}", name, position=position, face=face)
        if command.operation == "rename":
            return say("renamed to {name} ({state})", command.name, name=name, state=state)
        if command.operation == "forget":
            return say("profile registration removed", name)
        return say("{operation} requested ({state})", name, operation=command.operation, state=state)

    @classmethod
    def _status_reply(cls, status: dict[str, object], observed: dict[str, object] | None, lang: str = "en") -> str:
        """The status reply line; the Bot menu form shows the same text."""
        fields = [
            tr(lang, str(status["connectionState"])),
            tr(lang, "desired={state}", state=Text(status["desiredState"])),
        ]
        if observed:
            fields.append(
                f"{observed['dimension']} {observed['x']:.2f} {observed['y']:.2f} {observed['z']:.2f} "
                f"yaw={observed['yaw']:.1f} pitch={observed['pitch']:.1f}"
            )
        if status.get("lastError"):
            fields.append(f"{ERROR}{tr(lang, 'error={error}', error=status['lastError'])}{RESET}")
        if "chunkRadius" in status:
            fields.append(cls._radius_summary(status, lang))
        timed = cls._timed_inputs_summary(status.get("inputs") or {}, lang)
        if timed:
            fields.append(timed)
        if status.get("mining"):
            fields.append(cls._mining_summary(status["mining"], lang))
        if status.get("vehicle"):
            vehicle = str(status["vehicle"].get("type") or "?").removeprefix("minecraft:")
            fields.append(tr(lang, "riding {vehicle}", vehicle=vehicle))
        if observed and observed.get("health") is not None:
            hunger = (status.get("vitals") or {}).get("hunger")
            fields.append(tr(lang, "health {health}/{max}", health=f"{observed['health']:g}",
                             max=f"{observed['maxHealth']:g}")
                          + ("" if hunger is None else " " + tr(lang, "food {food}", food=f"{hunger:g}")))
        return reply(f"{DIM} | {RESET}".join(fields), status["name"])

    @staticmethod
    def _radius_summary(status: dict[str, object], lang: str = "en") -> str:
        source = Text("Bot override" if status.get("chunkRadiusSource") == "bot" else "shared default")
        confirmed = status.get("serverChunkRadius")
        if confirmed is not None:
            server = tr(lang, "server confirmed {radius}", radius=confirmed)
        elif status.get("connectionState") == "online":
            server = tr(lang, "server confirmation pending")
        else:
            server = tr(lang, "applies when online")
        return tr(
            lang,
            "chunk radius {radius} ({source}); {server}",
            radius=status["chunkRadius"],
            source=source,
            server=server,
        )

    @staticmethod
    def _timed_inputs_summary(inputs: dict[str, object], lang: str = "en") -> str | None:
        """Inputs that end on their own (for/hold), with ticks left."""
        parts = []
        if inputs.get("movementRemainingTicks") is not None:
            parts.append(
                tr(
                    lang,
                    "move {direction} {ticks} ticks left",
                    direction=inputs.get("movement"),
                    ticks=inputs["movementRemainingTicks"],
                )
            )
        for action, state in (inputs.get("actions") or {}).items():
            target = state.get("target")
            if state.get("remainingTicks") is None and state.get("holdTicks") is None and target is None:
                continue
            words = [action]
            if target is not None:
                words.append(_selector_text(target))
            words.append(str(state.get("mode")))
            if state.get("mode") == "interval":
                words.append(str(state.get("intervalTicks")))
            if state.get("remainingCount") is not None:
                words.append(tr(lang, "{count} left", count=state["remainingCount"]))
            if state.get("holdTicks") is not None:
                words.append(f"hold {state['holdTicks']}")
            if state.get("remainingTicks") is not None:
                words.append(tr(lang, "{ticks} ticks left", ticks=state["remainingTicks"]))
            parts.append(" ".join(words))
        return tr(lang, "timed: {inputs}", inputs=", ".join(parts)) if parts else None

    @staticmethod
    def script_status(status, observed=None):
        """Stable script fields: runtime state and jobs, plus BDS observation when the Bot is in the world."""
        observed = observed or {}
        vitals = status.get("vitals") or {}
        position = ({axis: observed[axis] for axis in ("x", "y", "z")} if "x" in observed else None)
        mining = status.get("mining") or {}
        active = mining.get("state") == "active" or bool(mining.get("loopId"))
        continuous = bool(mining.get("continuous")) or "blocksBroken" in mining
        return {
            "name": status["name"], "identityId": status["identityId"],
            "connectionState": status["connectionState"],
            "vehicle": status.get("vehicle"),
            "position": position,
            "dimension": observed.get("dimension"),
            "look": {"yaw": observed["yaw"], "pitch": observed["pitch"]} if "yaw" in observed else None,
            "health": observed.get("health"),
            "maxHealth": observed.get("maxHealth"),
            "hunger": vitals.get("hunger"),
            "saturation": vitals.get("saturation"),
            "onGround": observed.get("onGround"),
            "inWater": observed.get("inWater"),
            "selectedSlot": observed.get("selectedSlot"),
            "held": observed.get("held"),
            "mining": {
                "active": active,
                "mode": ("continuous" if continuous else "once") if mining else None,
                "state": mining.get("state"),
                "blocksBroken": mining.get("blocksBroken", 1 if mining.get("state") == "completed" else 0),
                "blockCount": mining.get("blockCount"),
                "reason": None if active else mining.get("reason"),
            },
        }

    @staticmethod
    def _mining_summary(mining: dict[str, object], lang: str = "en") -> str | None:
        position = " ".join(str(value) for value in mining.get("blockPosition") or [])
        block = mining.get("blockName")
        if block and block != "unknown" and position:
            target = tr(lang, "{block} at {position}", block=block, position=position)
        else:
            target = position
        state = mining.get("state")
        left = (
            tr(lang, "{ticks} ticks left", ticks=mining["remainingTicks"])
            if mining.get("remainingTicks") is not None
            else ""
        )
        blocks = BotCommandService._blocks_summary(mining, lang)
        if state == "active":
            if mining.get("continuous"):
                notes = ", ".join(note for note in (blocks, left) if note)
                mode = f" (continuous{', ' + notes if notes else ''})"
            else:
                mode = ""
            return tr(lang, "destroying {target}{mode}", target=target, mode=mode).rstrip()
        if mining.get("continuous"):
            last = tr(lang, "last: {target}", target=target) if target else ""
            notes = ", ".join(note for note in (last, blocks, left) if note)
            return tr(
                lang, "destroy continuous waiting for a block in sight{notes}", notes=f" ({notes})" if notes else ""
            )
        # A finished job stays visible until the next one, labelled as past.
        reason = str(MINING_REASONS.get(str(mining.get("reason", "")), mining.get("reason") or ""))
        if state == "completed":
            outcome = tr(lang, reason or "completed")
        elif state == "failed":
            outcome = tr(lang, "failed: {reason}", reason=Text(reason)) if reason else tr(lang, "failed")
        elif reason.startswith(("canceled", "replaced", "stopped")):
            outcome = tr(lang, reason)
        else:
            outcome = tr(lang, "canceled: {reason}", reason=Text(reason)) if reason else tr(lang, "canceled")
        details = ", ".join(note for note in (blocks, target) if note)
        return tr(lang, "last destroy: {outcome}{target}", outcome=outcome, target=f" ({details})" if details else "")

    @staticmethod
    def _blocks_summary(mining: dict[str, object], lang: str = "en") -> str:
        """Broken blocks of a continuous run, against its count limit when set."""
        done = mining.get("blocksBroken")
        if done is None:
            return ""
        total = mining.get("blockCount")
        if total is None:
            return tr(lang, "{done} blocks", done=done)
        return tr(lang, "{done}/{total} blocks", done=done, total=total)
