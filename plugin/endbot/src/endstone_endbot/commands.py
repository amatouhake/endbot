"""Pure command grammar and service for the /bot surface."""

from __future__ import annotations

import math
import shlex
from dataclasses import dataclass, field

from endstone_endbot.control import RuntimeControlError


class CommandSyntaxError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class BotCommand:
    operation: str
    name: str | None = None
    parameters: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CommandResult:
    handled: bool
    messages: tuple[str, ...] = ()

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
    "interact": HelpTopic(
        "interact",
        "right-click a block face",
        ("interact <x> <y> <z> <down|up|north|south|west|east>",),
        ("BDS decides reach, legality and inventory changes.",),
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
    ("hotbar", "interact", "drop", "rename", "forget"),
)

HELP_PAGE_TITLES = (
    "lifecycle, status and teleport",
    "movement and actions",
    "inventory and advanced lifecycle",
)


def _help_prefix(topic: HelpTopic) -> str:
    return "/bot <name>" if topic.named else "/bot"


def help_page(page: int) -> tuple[str, ...]:
    """Render one paginated help index, modeled on vanilla Bedrock /help."""
    total = len(HELP_PAGES)
    lines = [f"--- Endbot help page {page} of {total}: {HELP_PAGE_TITLES[page - 1]} ---"]
    if page == 1:
        lines.append("/bot ping | /bot list | /bot help - connectivity, profiles, this help")
    for key in HELP_PAGES[page - 1]:
        topic = HELP_TOPICS[key]
        lines.append(f"{_help_prefix(topic)} {topic.syntaxes[0]} - {topic.summary}")
    if page < total:
        lines.append(f"Next page: /bot help {page + 1} - detail: /bot help <command>")
    else:
        lines.append("Detail: /bot help <command> (for example /bot help move)")
    return tuple(lines)


def help_detail(key: str) -> tuple[str, ...]:
    """Render detailed help for one operation, listing every supported syntax."""
    topic = HELP_TOPICS[key]
    lines = [
        f"Endbot help: {topic.operation}",
        *(f"{_help_prefix(topic)} {syntax}" for syntax in topic.syntaxes),
        *topic.details,
    ]
    return tuple(lines)

BLOCK_FACES = {"down": 0, "up": 1, "north": 2, "south": 3, "west": 4, "east": 5}


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
    if operation == "move" and len(remaining) == 1 and remaining[0].lower() in {
        "forward", "backward", "left", "right", "stop"
    }:
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
    if operation == "interact" and len(remaining) == 4 and remaining[-1].lower() in BLOCK_FACES:
        target, unused = _position(remaining[:3])
        assert not unused
        return BotCommand(
            operation,
            name,
            {**target, "face": BLOCK_FACES[remaining[-1].lower()], "faceName": remaining[-1].lower()},
        )
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

    def execute(self, arguments: list[str], sender=None) -> CommandResult:
        try:
            return self._execute(parse_command(arguments), sender)
        except (CommandSyntaxError, RuntimeError, ValueError) as error:
            return CommandResult(True, (f"Endbot: {error}",))

    def _execute(self, command: BotCommand, sender) -> CommandResult:
        if command.operation == "ping":
            # Keep the server-side safety probe independent of runtime health.
            return CommandResult(True, ("Endbot: pong",))
        if command.operation == "help":
            if "topic" in command.parameters:
                return CommandResult(True, help_detail(command.parameters["topic"]))
            return CommandResult(True, help_page(int(command.parameters.get("page", 1))))
        if self.control is None:
            raise RuntimeError("runtime control is not configured")
        if command.operation == "list":
            bots = self.control.request("list")
            if not bots:
                return CommandResult(True, ("Endbot: no Bot profiles",))
            summary = ", ".join(f"{bot['name']} [{bot['connectionState']}]" for bot in bots)
            return CommandResult(True, ("Endbot: " + summary,))
        if command.operation == "status":
            status = self.control.request("status", name=command.name)
            observed = self.world.observe(status["identityId"]) if self.world else None
            fields = [status["name"], status["connectionState"], f"desired={status['desiredState']}"]
            if observed:
                fields.append(
                    f"{observed['dimension']} {observed['x']:.2f} {observed['y']:.2f} {observed['z']:.2f} "
                    f"yaw={observed['yaw']:.1f} pitch={observed['pitch']:.1f}"
                )
            if status.get("lastError"):
                fields.append(f"error={status['lastError']}")
            return CommandResult(True, ("Endbot: " + " | ".join(fields),))
        if command.operation == "spawn":
            existing = None
            try:
                existing = self.control.request("status", name=command.name)
            except RuntimeControlError as error:
                if error.code != "not_found":
                    raise
            placement = command.parameters or self.world.default_spawn(sender)
            placement = self.world.resolve_spawn_placement(placement, sender)
            self.world.assert_name_available(command.name, existing.get("identityId") if existing else None)
            result = self.control.request("spawn", name=command.name)
            if result.get("alreadyOnline"):
                return CommandResult(True, (f"Endbot: {result['name']} is already online",))
            self.world.queue_spawn_placement(result["identityId"], placement)
            verb = "created and connecting" if result.get("created") else "connecting"
            return CommandResult(True, (f"Endbot: {result['name']} {verb}",))
        if command.operation == "teleport":
            status = self.control.request("status", name=command.name)
            location = self.world.teleport(status["identityId"], command.parameters, sender)
            return CommandResult(True, (f"Endbot: teleported {status['name']} to {location}",))
        if command.operation == "interact":
            status = self.control.request("status", name=command.name)
            interaction = self.world.resolve_interaction(status["identityId"], command.parameters, sender)
            result = self.control.request("interact", name=command.name, **interaction)
        elif command.operation == "hotbar":
            result = self.control.request("hotbar", name=command.name, **command.parameters)
            return CommandResult(
                True,
                (f"Endbot: {result['name']} selected hotbar slot {result['selectedHotbarSlot']}",),
            )
        elif command.operation == "drop":
            result = self.control.request("drop", name=command.name, **command.parameters)
            amount = "stack" if command.parameters["stack"] else "one item"
            return CommandResult(True, (f"Endbot: {result['name']} requested drop {amount}",))
        elif command.operation == "look" and "targetCoordinates" in command.parameters:
            yaw, pitch = self.world.look_at(command.name, command.parameters["targetCoordinates"])
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
        return CommandResult(True, (f"Endbot: {name} {state}",))
