"""Pure command grammar and service for the /bot surface."""

from __future__ import annotations

import math
import re
import shlex
import threading
from dataclasses import dataclass, field

from endstone_endbot.control import RuntimeControlError


class CommandSyntaxError(ValueError):
    pass


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


@dataclass(frozen=True, slots=True)
class CommandResult:
    handled: bool
    messages: tuple[str, ...] = ()
    # Set for results worth a server console log entry (e.g. destroy continuous/stop).
    log_level: str | None = None

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
        ("move forward|backward|left|right|stop",),
        ("Movement acts as held input; stop halts it.",),
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
        ("jump [once|continuous|interval <ticks>|stop]",),
        ("Omitting the mode means once; interval takes 1-72000 ticks.",),
    ),
    "attack": HelpTopic(
        "attack",
        "make a Bot attack",
        ("attack [once|continuous|interval <ticks>|stop]",),
        ("Continuous actions run every input tick.",),
    ),
    "use": HelpTopic(
        "use",
        "use the held item in air",
        ("use [once|continuous|interval <ticks>|stop]",),
        ("With an empty hand it is a safe no-op; blocks need interact.",),
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
        "right-click a block face",
        ("interact <x> <y> <z> <down|up|north|south|west|east>",),
        ("BDS decides reach, legality and inventory changes.",),
    ),
    "destroy": HelpTopic(
        "destroy",
        "break reachable blocks with the held tool (hold left-click)",
        (
            "destroy [once|continuous|stop]",
            "destroy <x> <y> <z> [down|up|north|south|west|east]",
        ),
        (
            "once (default) breaks one block in sight; x y z breaks that block. ~ is relative to the Bot.",
            "continuous holds left-click along the Bot's sight, also after you log off; waits when empty.",
            "Other commands do not cancel it; only destroy stop, stop, death or disconnect end it.",
            "No interval mode. No automatic walking; BDS controls speed, drops and durability.",
        ),
    ),
    "drop": HelpTopic(
        "drop",
        "drop the selected item",
        ("drop [stack]",),
        ("drop drops one item; drop stack drops the selected stack.",),
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
    "help": HelpTopic(
        "help",
        "show this help",
        ("help [page|command]",),
        ("Pages summarize commands; a command name shows detail.",),
        named=False,
    ),
}

HELP_TOPIC_ALIASES = {"tp": "teleport"}

HELP_PAGES: tuple[tuple[str, ...], ...] = (
    ("spawn", "resume", "reconnect", "despawn", "status", "teleport"),
    ("move", "look", "jump", "attack", "use", "sprint", "sneak", "stop"),
    ("hotbar", "interact", "destroy", "drop", "radius", "rename", "forget"),
)

HELP_PAGE_TITLES = (
    "lifecycle, status and teleport",
    "movement and actions",
    "inventory, blocks and Bot settings",
)


def _help_prefix(topic: HelpTopic) -> str:
    return "/bot <name>" if topic.named else "/bot"


def help_page(page: int) -> tuple[str, ...]:
    """Render one paginated help index, modeled on vanilla Bedrock /help."""
    total = len(HELP_PAGES)
    lines = [f"{HEADING}--- Endbot help page {page} of {total}: {HELP_PAGE_TITLES[page - 1]} ---{RESET}"]
    separator = f"{DIM} - {RESET}"
    if page == 1:
        commands = f"{DIM} | {RESET}".join(color_syntax(f"/bot {key}") for key in ("ping", "list", "help"))
        lines.append(f"{commands}{separator}connectivity, profiles, this help")
    for key in HELP_PAGES[page - 1]:
        topic = HELP_TOPICS[key]
        lines.append(f"{color_syntax(f'{_help_prefix(topic)} {topic.syntaxes[0]}')}{separator}{topic.summary}")
    if page < total:
        lines.append(
            f"{DIM}Next page:{RESET} {color_syntax(f'/bot help {page + 1}')}"
            f"{DIM} - detail:{RESET} {color_syntax('/bot help <command>')}"
        )
    else:
        lines.append(
            f"{DIM}Detail:{RESET} {color_syntax('/bot help <command>')}"
            f" {DIM}(for example{RESET} {color_syntax('/bot help move')}{DIM}){RESET}"
        )
    return tuple(lines)


def help_detail(key: str) -> tuple[str, ...]:
    """Render detailed help for one operation, listing every supported syntax."""
    topic = HELP_TOPICS[key]
    lines = [
        f"{HEADING}Endbot help: {topic.operation}{RESET}",
        *(color_syntax(f"{_help_prefix(topic)} {syntax}") for syntax in topic.syntaxes),
        *topic.details,
    ]
    return tuple(lines)


BLOCK_FACES = {"down": 0, "up": 1, "north": 2, "south": 3, "west": 4, "east": 5}

# Polls (every 4 ticks) a loop may fail to reach the runtime before it ends:
# about one 200-tick runtime lease.
UNREACHABLE_POLL_LIMIT = 50

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
}


def _tokens(arguments: list[str]) -> list[str]:
    tokens: list[str] = []
    for argument in arguments:
        tokens.extend(shlex.split(argument) if any(character.isspace() for character in argument) else [argument])
    return tokens


def _number(value: str, label: str) -> float:
    try:
        result = float(value)
    except ValueError as error:
        raise CommandSyntaxError(f"{label} must be a number") from error
    if not math.isfinite(result):
        raise CommandSyntaxError(f"{label} must be finite")
    return result


def _coordinate(value: str) -> float | tuple[str, float]:
    if value.startswith("~"):
        return ("relative", _number(value[1:] or "0", "relative coordinate"))
    return _number(value, "coordinate")


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


def _action(name: str, action: str, remaining: list[str]) -> BotCommand:
    mode = remaining[0].lower() if remaining else "once"
    if (mode in {"once", "continuous", "stop"} and len(remaining) == 1) or not remaining:
        return BotCommand("action", name, {"action": action, "mode": mode})
    if mode == "interval" and len(remaining) == 2:
        try:
            ticks = int(remaining[1])
        except ValueError as error:
            raise CommandSyntaxError("Action interval must be an integer number of ticks") from error
        if not 1 <= ticks <= 72_000:
            raise CommandSyntaxError("Action interval must be between 1 and 72000 ticks")
        return BotCommand("action", name, {"action": action, "mode": mode, "intervalTicks": ticks})
    raise CommandSyntaxError(f"{action} accepts once, continuous, interval <ticks>, or stop")


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
        raise CommandSyntaxError(f"Help page must be from 1 to {len(HELP_PAGES)}")
    key = HELP_TOPIC_ALIASES.get(token, token)
    if key in HELP_TOPICS:
        return BotCommand("help", parameters={"topic": key})
    raise CommandSyntaxError(f"Unknown help topic '{raw}'. Use /bot help <page> or /bot help <command>")


def parse_command(arguments: list[str]) -> BotCommand:
    tokens = _tokens(arguments)
    if not tokens:
        return BotCommand("help", parameters={"page": 1})
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
    if len(tokens) < 2:
        raise CommandSyntaxError("Expected /bot <name> <action>")
    name, operation, remaining = tokens[0], tokens[1].lower(), tokens[2:]
    if operation in {"status", "resume", "reconnect", "despawn", "forget", "stop"} and not remaining:
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
    if (
        operation == "move"
        and len(remaining) == 1
        and remaining[0].lower() in {"forward", "backward", "left", "right", "stop"}
    ):
        return BotCommand(operation, name, {"direction": remaining[0].lower()})
    if operation == "look" and len(remaining) == 2:
        return BotCommand(
            operation,
            name,
            {"yaw": _number(remaining[0], "yaw"), "pitch": _number(remaining[1], "pitch")},
        )
    if operation == "look" and len(remaining) == 4 and remaining[0].lower() == "at":
        return BotCommand(operation, name, {"targetCoordinates": [_coordinate(value) for value in remaining[1:]]})
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
    if operation == "interact" and len(remaining) == 4 and remaining[-1].lower() in BLOCK_FACES:
        target, unused = _position(remaining[:3])
        assert not unused
        return BotCommand(
            operation,
            name,
            {**target, "face": BLOCK_FACES[remaining[-1].lower()], "faceName": remaining[-1].lower()},
        )
    if operation == "destroy":
        modes = [value.lower() for value in remaining]
        if not remaining or modes == ["once"]:
            return BotCommand(operation, name)
        if modes in (["continuous"], ["stop"]):
            return BotCommand(operation, name, {"mode": modes[0]})
        if modes and modes[0] == "interval":
            raise CommandSyntaxError(
                "destroy has no interval mode; destroy continuous waits for the next block automatically"
            )
        if len(remaining) in {3, 4}:
            target, unused = _position(remaining[:3])
            assert not unused
            if len(remaining) == 4:
                if remaining[3].lower() not in BLOCK_FACES:
                    raise CommandSyntaxError("Invalid block face")
                target["face"] = BLOCK_FACES[remaining[3].lower()]
            return BotCommand(operation, name, target)
        raise CommandSyntaxError("destroy accepts once, continuous, stop, or x y z [face]")
    if operation == "drop" and (not remaining or remaining == ["stack"]):
        return BotCommand(operation, name, {"stack": bool(remaining)})
    if operation in {"jump", "attack", "use"}:
        return _action(name, operation, remaining)
    if operation in {"sprint", "sneak"} and len(remaining) == 1 and remaining[0].lower() in {"on", "off"}:
        return BotCommand("flag", name, {"flag": operation, "value": remaining[0].lower() == "on"})
    raise CommandSyntaxError(f"Unknown or invalid Bot action: {operation}")


class BotCommandService:
    """Execute parsed commands against transport and world adapters."""

    def __init__(self, control=None, world=None) -> None:
        self.control = control
        self.world = world
        # Shared by the command worker and the mining poll worker. Entries are
        # (name, identity, loopId, source, position): a continuous loop has a
        # loopId; a single-block job has loopId None and is watched only to
        # report an abnormal end. Entries are replaced, never mutated, so
        # identity checks detect a newer loop or job.
        self._mining_loops = {}
        self._mining_lock = threading.Lock()
        # Consecutive polls that could not reach the runtime, per loop entry.
        self._mining_unreachable = {}

    def poll_mining(self, authorize) -> list[tuple[object, str, str]]:
        """Renew/retarget loops; return (source, level, message) notices.

        Notices cover only abnormal ends. Commanded stops already replied, and
        each successfully broken block would be far too chatty.
        """
        notices = []
        with self._mining_lock:
            loops = tuple(self._mining_loops.items())
        for key, entry in loops:
            name, identity, loop_id, source, position = entry
            stopped = f"{'destroy continuous' if loop_id else 'destroy'} stopped: "
            try:
                if not authorize(source):
                    if self._end_mining_loop(key, entry):
                        notices.append(
                            (source, "warning", reply(stopped + "control permission revoked", name, WARNING))
                        )
                    continue
                status = self.control.request("status", name=name)
                self._mining_unreachable.pop(key, None)  # reached the runtime again
                mining = status.get("mining") or {}
                if status["identityId"] != identity:
                    if self._take_mining_loop(key, entry):
                        notices.append(
                            (source, "warning", reply(stopped + "Bot was renamed or removed", name, WARNING))
                        )
                    continue
                if loop_id is None:
                    reason = self._single_mining_end(status, mining, position)
                elif mining.get("loopId") != loop_id:
                    reason = self._mining_end_reason(status, mining)
                else:
                    self._continue_mining_loop(name, identity, loop_id, source, mining)
                    continue
                if reason is False:
                    continue  # still breaking
                if self._take_mining_loop(key, entry) and reason:
                    notices.append((source, "warning", reply(stopped + reason, name, WARNING)))
            except RuntimeControlError as error:
                # Windows loopback can refuse a fresh connection (WinError 10048)
                # under rapid reconnects. Retry for about one runtime lease
                # (200 ticks = 50 polls) before giving up on the runtime.
                if error.code == "unavailable" and self._unreachable(key, entry) < UNREACHABLE_POLL_LIMIT:
                    continue
                if self._end_mining_loop(key, entry):
                    notices.append((source, "warning", reply(stopped + str(error), name, WARNING)))
            except (RuntimeError, ValueError) as error:
                if self._end_mining_loop(key, entry):
                    notices.append((source, "warning", reply(stopped + str(error), name, WARNING)))
        return notices

    def _unreachable(self, key, entry) -> int:
        previous, count = self._mining_unreachable.get(key, (None, 0))
        count = count + 1 if previous is entry else 1
        self._mining_unreachable[key] = (entry, count)
        return count

    def _continue_mining_loop(self, name, identity, loop_id, source, mining) -> None:
        target = None
        if mining.get("state") != "active":
            # Sight retargeting needs no controller; it may have logged off.
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

    def execute(self, arguments: list[str], sender=None) -> CommandResult:
        command = None
        try:
            command = parse_command(arguments)
            return self._execute(command, sender)
        except (CommandSyntaxError, RuntimeError, ValueError) as error:
            if command is not None and command.name:
                operation = command.operation
                if operation in {"action", "flag"}:
                    operation = str(command.parameters[operation])
                name, context = command.name, f"{operation} failed: "
            else:
                name, context = None, ""
                try:
                    tokens = _tokens(arguments)
                except ValueError:
                    tokens = []
                if len(tokens) >= 2 and tokens[0].lower() != "help":
                    # Syntax errors still name the Bot the operator addressed.
                    name = tokens[0]
            return CommandResult(True, (reply(f"{context}{error}", name, ERROR),))

    def _execute(self, command: BotCommand, sender) -> CommandResult:
        if command.operation == "ping":
            # Keep the server-side safety probe independent of runtime health.
            return CommandResult(True, (reply("pong"),))
        if command.operation == "help":
            if "topic" in command.parameters:
                return CommandResult(True, help_detail(command.parameters["topic"]))
            return CommandResult(True, help_page(int(command.parameters.get("page", 1))))
        if self.control is None:
            raise RuntimeError("runtime control is not configured")
        if command.operation == "list":
            bots = self.control.request("list")
            if not bots:
                return CommandResult(True, (reply("no Bot profiles"),))
            summary = ", ".join(f"{bot['name']} {DIM}[{bot['connectionState']}]{RESET}" for bot in bots)
            return CommandResult(True, (reply(summary),))
        if command.operation == "status":
            status = self.control.request("status", name=command.name)
            observed = self.world.observe(status["identityId"]) if self.world else None
            fields = [status["connectionState"], f"desired={status['desiredState']}"]
            if observed:
                fields.append(
                    f"{observed['dimension']} {observed['x']:.2f} {observed['y']:.2f} {observed['z']:.2f} "
                    f"yaw={observed['yaw']:.1f} pitch={observed['pitch']:.1f}"
                )
            if status.get("lastError"):
                fields.append(f"{ERROR}error={status['lastError']}{RESET}")
            if "chunkRadius" in status:
                fields.append(self._radius_summary(status))
            if status.get("mining"):
                fields.append(self._mining_summary(status["mining"]))
            return CommandResult(True, (reply(f"{DIM} | {RESET}".join(fields), status["name"]),))
        if command.operation == "spawn":
            existing = None
            try:
                existing = self.control.request("status", name=command.name)
            except RuntimeControlError as error:
                if error.code != "not_found":
                    raise
            placement = command.parameters or self.world.default_spawn(sender)
            if placement is not None:
                placement = self.world.resolve_spawn_placement(placement, sender)
            self.world.assert_name_available(command.name, existing.get("identityId") if existing else None)
            result = self.control.request("spawn", name=command.name)
            if result.get("alreadyOnline"):
                state = result.get("connectionState")
                if state == "connecting":
                    return CommandResult(True, (reply("already connecting", result["name"]),))
                if state == "reconnecting":
                    return CommandResult(True, (reply("already reconnecting", result["name"]),))
                if state == "online":
                    return CommandResult(True, (reply("already online", result["name"]),))
                # Missing or unknown states stay neutral rather than claiming online.
                return CommandResult(True, (reply("already active", result["name"]),))
            verb = "created and connecting" if result.get("created") else "connecting"
            if placement is None:
                # Console spawn without coordinates: BDS places the Bot.
                self.world.clear_pending_placement(result["identityId"])
                where = "at the world spawn" if result.get("created") else "where it last was"
                return CommandResult(True, (reply(f"{verb} ({where})", result["name"]),))
            self.world.queue_spawn_placement(result["identityId"], placement)
            return CommandResult(True, (reply(verb, result["name"]),))
        if command.operation == "teleport":
            status = self.control.request("status", name=command.name)
            location = self.world.teleport(status["identityId"], command.parameters, sender)
            return CommandResult(True, (reply(f"teleported to {location}", status["name"]),))
        if command.operation == "radius":
            result = self.control.request("radius", name=command.name, **command.parameters)
            return CommandResult(True, (reply(self._radius_summary(result), result["name"]),))
        if command.operation == "destroy":
            mode = command.parameters.get("mode")
            if mode == "continuous":
                target = {"mode": "start"}  # Runtime IPC name for a continuous loop.
            elif mode == "stop":
                target = {"mode": "stop"}
            else:
                status = self.control.request("status", name=command.name)
                target = self.world.resolve_mining(status["identityId"], command.parameters, sender)
            result = self.control.request("mine", name=command.name, **target)
            key = command.name.casefold()
            with self._mining_lock:
                self._mining_loops.pop(key, None)
                if mode == "continuous":
                    self._mining_loops[key] = (
                        result["name"], result["identityId"], result["mining"]["loopId"], sender, None
                    )
                elif mode != "stop":
                    self._mining_loops[key] = (
                        result["name"], result["identityId"], None, sender, list(target["blockPosition"])
                    )
            if mode == "continuous":
                return CommandResult(
                    True,
                    (reply("destroy continuous started; destroy stop to cancel", result["name"]),),
                    log_level="info",
                )
            if mode == "stop":
                return CommandResult(True, (reply("destroy stopped", result["name"]),), log_level="info")
            position = " ".join(str(value) for value in target["blockPosition"])
            started = f"destroying {target['blockName']} at {position}"
            return CommandResult(True, (reply(f"{started}; see status for the result", result["name"]),))
        if command.operation == "interact":
            status = self.control.request("status", name=command.name)
            interaction = self.world.resolve_interaction(status["identityId"], command.parameters, sender)
            result = self.control.request("interact", name=command.name, **interaction)
        elif command.operation == "hotbar":
            result = self.control.request("hotbar", name=command.name, **command.parameters)
            selected = " selected" if command.parameters else ""
            return CommandResult(
                True,
                (reply(f"hotbar slot {result['selectedHotbarSlot']}{selected}", result["name"]),),
            )
        elif command.operation == "drop":
            result = self.control.request("drop", name=command.name, **command.parameters)
            amount = "stack" if command.parameters["stack"] else "one item"
            return CommandResult(True, (reply(f"drop {amount} requested", result["name"]),))
        elif command.operation == "look" and "targetCoordinates" in command.parameters:
            status = self.control.request("status", name=command.name)
            yaw, pitch = self.world.look_at(status["identityId"], command.parameters["targetCoordinates"])
            result = self.control.request("look", name=command.name, yaw=yaw, pitch=pitch)
        elif command.operation == "rename":
            status = self.control.request("status", name=command.name)
            self.world.assert_name_available(command.parameters["newName"], status["identityId"])
            self.world.clear_pending_placement(status["identityId"])
            result = self.control.request("rename", name=command.name, **command.parameters)
        elif command.operation in {"resume", "reconnect", "despawn", "forget"}:
            status = self.control.request("status", name=command.name)
            self.world.clear_pending_placement(status["identityId"])
            result = self.control.request(command.operation, name=command.name, **command.parameters)
        else:
            result = self.control.request(command.operation, name=command.name, **command.parameters)
        name = result.get("name", command.name)
        state = result.get("connectionState", command.operation)
        parameters = command.parameters
        if command.operation == "move":
            detail = (
                "movement stopped"
                if parameters["direction"] == "stop"
                else f"movement set to {parameters['direction']}"
            )
        elif command.operation == "look":
            if "targetCoordinates" not in parameters:
                yaw, pitch = parameters["yaw"], parameters["pitch"]
            detail = f"look set to yaw={yaw:.1f} pitch={pitch:.1f}"
        elif command.operation == "action":
            action, mode = parameters["action"], parameters["mode"]
            if mode == "stop":
                detail = f"{action} stopped"
            elif mode == "interval":
                detail = f"{action} started every {parameters['intervalTicks']} ticks"
            elif mode == "continuous":
                detail = f"{action} continuous started"
            else:
                detail = f"{action} once requested"
        elif command.operation == "flag":
            detail = f"{parameters['flag']} {'on' if parameters['value'] else 'off'}"
        elif command.operation == "stop":
            detail = "movement and actions stopped (sprint/sneak off)"
        elif command.operation == "interact":
            position = " ".join(str(value) for value in interaction["blockPosition"])
            face = next(key for key, value in BLOCK_FACES.items() if value == interaction["face"])
            detail = f"block interaction requested at {position} face={face}"
        elif command.operation == "rename":
            return CommandResult(True, (reply(f"renamed to {name} ({state})", command.name),))
        elif command.operation == "forget":
            detail = "profile registration removed"
        else:
            detail = f"{command.operation} requested ({state})"
        return CommandResult(True, (reply(detail, name),))

    @staticmethod
    def _radius_summary(status: dict[str, object]) -> str:
        source = "Bot override" if status.get("chunkRadiusSource") == "bot" else "shared default"
        confirmed = status.get("serverChunkRadius")
        if confirmed is not None:
            server = f"server confirmed {confirmed}"
        elif status.get("connectionState") == "online":
            server = "server confirmation pending"
        else:
            server = "applies when online"
        return f"chunk radius {status['chunkRadius']} ({source}); {server}"

    @staticmethod
    def _mining_summary(mining: dict[str, object]) -> str | None:
        position = " ".join(str(value) for value in mining.get("blockPosition") or [])
        block = mining.get("blockName")
        target = f"{block} at {position}" if block and block != "unknown" and position else position
        state = mining.get("state")
        if state == "active":
            mode = " (continuous)" if mining.get("continuous") else ""
            return f"destroying {target}{mode}".rstrip()
        if mining.get("continuous"):
            last = f" (last: {target})" if target else ""
            return f"destroy continuous waiting for a block in sight{last}"
        # A finished job stays visible until the next one, labelled as past.
        reason = str(MINING_REASONS.get(str(mining.get("reason", "")), mining.get("reason") or ""))
        if state == "completed":
            outcome = reason or "completed"
        elif state == "failed":
            outcome = f"failed: {reason}" if reason else "failed"
        elif reason.startswith(("canceled", "replaced", "stopped")):
            outcome = reason
        else:
            outcome = f"canceled: {reason}" if reason else "canceled"
        return f"last destroy: {outcome}" + (f" ({target})" if target else "")
