"""Pure command grammar and service for the M2 /bot surface."""

from __future__ import annotations

import math
import shlex
from dataclasses import dataclass, field


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


QUICK_HELP = (
    "Endbot quick start: /bot <name> spawn | tp me | jump | attack continuous | stop | despawn",
    "Use /bot help advanced for lifecycle, movement, and coordinate syntax.",
)
ADVANCED_HELP = (
    "Lifecycle: spawn [at x y z [facing yaw pitch] [in dimension]], resume, reconnect, despawn, forget, rename",
    "Control: tp, move forward|backward|left|right|stop, look yaw pitch, jump|attack|use, sprint, sneak, stop",
    "Actions: once (default), continuous, interval <ticks>, or stop. Teleport never invokes vanilla /tp.",
)


def _tokens(arguments: list[str]) -> list[str]:
    if len(arguments) == 1 and any(character.isspace() for character in arguments[0]):
        return shlex.split(arguments[0])
    return arguments


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


def parse_command(arguments: list[str]) -> BotCommand:
    tokens = _tokens(arguments)
    if not tokens:
        return BotCommand("help")
    first = tokens[0].lower()
    if first == "ping" and len(tokens) == 1:
        return BotCommand("ping")
    if first == "help" and len(tokens) <= 2:
        return BotCommand("help", parameters={"advanced": len(tokens) == 2 and tokens[1].lower() == "advanced"})
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
        return BotCommand(operation, name, {"yaw": _number(remaining[0], "yaw"), "pitch": _number(remaining[1], "pitch")})
    if operation == "look" and len(remaining) == 4 and remaining[0].lower() == "at":
        return BotCommand(operation, name, {"targetCoordinates": [_coordinate(value) for value in remaining[1:]]})
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
            # Keep the M0 server-side safety probe independent of runtime health.
            return CommandResult(True, ("Endbot: pong",))
        if command.operation == "help":
            return CommandResult(True, ADVANCED_HELP if command.parameters.get("advanced") else QUICK_HELP)
        if self.control is None:
            raise RuntimeError("runtime control is not configured")
        if command.operation == "list":
            bots = self.control.request("list")
            if not bots:
                return CommandResult(True, ("Endbot: no Bot profiles",))
            return CommandResult(True, ("Endbot: " + ", ".join(f"{bot['name']} [{bot['connectionState']}]" for bot in bots),))
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
            placement = command.parameters or self.world.default_spawn(sender)
            result = self.control.request("spawn", name=command.name)
            if result.get("alreadyOnline"):
                return CommandResult(True, (f"Endbot: {result['name']} is already online",))
            self.world.queue_spawn_placement(result["identityId"], placement, sender)
            verb = "created and connecting" if result.get("created") else "connecting"
            return CommandResult(True, (f"Endbot: {result['name']} {verb}",))
        if command.operation == "teleport":
            status = self.control.request("status", name=command.name)
            location = self.world.teleport(status["identityId"], command.parameters, sender)
            return CommandResult(True, (f"Endbot: teleported {status['name']} to {location}",))
        if command.operation == "look" and "targetCoordinates" in command.parameters:
            yaw, pitch = self.world.look_at(command.name, command.parameters["targetCoordinates"])
            result = self.control.request("look", name=command.name, yaw=yaw, pitch=pitch)
        elif command.operation == "rename":
            self.world.assert_name_available(command.parameters["newName"])
            result = self.control.request("rename", name=command.name, **command.parameters)
        else:
            result = self.control.request(command.operation, name=command.name, **command.parameters)
        return CommandResult(True, (f"Endbot: {result.get('name', command.name)} {result.get('connectionState', command.operation)}",))
