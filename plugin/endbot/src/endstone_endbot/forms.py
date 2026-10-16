"""Endstone-free screen models for the /bot menu forms.

Forms never execute anything themselves: every button or modal submission
resolves to the same argument lists a typed /bot command would carry, and the
plugin feeds them through the normal command path. Authorization, revocation,
validation and reply wording therefore stay identical to typed commands.
"""

from __future__ import annotations

import json
import shlex
from collections.abc import Callable
from dataclasses import dataclass, field

Argv = tuple[str, ...]

ACTION_MODES = ("once", "continuous", "interval", "stop")
DESTROY_MODES = ("once", "continuous", "stop", "block at coordinates")
FACES = ("down", "up", "north", "south", "west", "east")
FLAG_CHOICES = ("no change", "on", "off")
TELEPORT_COORDINATES = "coordinates below"


class FormResponseError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class BotListView:
    """Runtime data for the Bot list; produced off the server thread."""

    bots: tuple[dict, ...]


@dataclass(frozen=True, slots=True)
class BotView:
    """Runtime data for one Bot's action screen; produced off the server thread."""

    name: str
    summary: str
    status: dict
    observed: dict | None = None


# Controls. Field order mirrors the endstone.form constructors.
@dataclass(frozen=True, slots=True)
class Dropdown:
    label: str
    options: tuple[str, ...]
    default_index: int = 0


@dataclass(frozen=True, slots=True)
class Slider:
    label: str
    min: float
    max: float
    step: float
    default_value: float


@dataclass(frozen=True, slots=True)
class StepSlider:
    label: str
    options: tuple[str, ...]
    default_index: int = 0


@dataclass(frozen=True, slots=True)
class TextInput:
    label: str
    placeholder: str = ""
    default_value: str = ""


@dataclass(frozen=True, slots=True)
class Toggle:
    label: str
    default_value: bool = False


Control = Dropdown | Slider | StepSlider | TextInput | Toggle


# Button actions.
@dataclass(frozen=True, slots=True)
class Run:
    """Submit each argument list through the /bot command path, in order."""

    commands: tuple[Argv, ...]


@dataclass(frozen=True, slots=True)
class Show:
    screen: ActionScreen | ModalScreen | ConfirmScreen


@dataclass(frozen=True, slots=True)
class Button:
    text: str
    action: Run | Show


@dataclass(frozen=True, slots=True)
class Header:
    text: str


@dataclass(frozen=True, slots=True)
class ActionScreen:
    title: str
    content: str
    items: tuple[Button | Header, ...]


@dataclass(frozen=True, slots=True)
class ConfirmScreen:
    title: str
    content: str
    confirm: str
    cancel: str
    action: Run


@dataclass(frozen=True, slots=True)
class ModalScreen:
    title: str
    controls: tuple[Control, ...]
    # Maps the submitted values (one per control) to commands to run.
    build: Callable[[list], tuple[Argv, ...]] = field(compare=False)
    submit: str = "Run"


def run(*commands: Argv) -> Run:
    return Run(tuple(commands))


def menu_argv(name: str | None = None) -> Argv:
    return ("menu",) if name is None else (name, "menu")


TRANSITIONAL_STATES = {
    # Dark colors: the button background is light gray, so §7 would vanish.
    "connecting": "§6JOIN",
    "reconnecting": "§6RETRY",
    "failed": "§4FAIL",
}


def bot_list_screen(view: BotListView) -> ActionScreen:
    """Online Bots by name; offline ones sit behind one button to keep the list short."""
    bots = sorted(view.bots, key=lambda bot: (bot["connectionState"] != "online", str(bot["name"]).lower()))
    offline = [bot for bot in bots if bot["connectionState"] == "offline"]
    items: list[Button | Header] = []
    for bot in bots:
        state = bot["connectionState"]
        if state == "offline":
            continue
        badge = TRANSITIONAL_STATES.get(state)
        text = bot["name"] if state == "online" else f"{badge or '§8?'}§r · {bot['name']}"
        items.append(Button(text, run(menu_argv(bot["name"]))))
    if offline:
        offline_items = [Button(bot["name"], run(menu_argv(bot["name"]))) for bot in offline]
        offline_items.append(Button("Back to Bot list", run(menu_argv())))
        offline_screen = ActionScreen("Endbot: offline Bots", "Pick a Bot.", tuple(offline_items))
        items.append(Button(f"Offline ({len(offline)})...", Show(offline_screen)))
    items.append(Button("+ New Bot here", Show(spawn_modal())))
    content = "Pick a Bot. (/bot help lists commands)" if bots else "No Bot profiles yet. Spawn one to start."
    return ActionScreen("Endbot", content, tuple(items))


def _offline_content(status: dict) -> str:
    state = str(status.get("connectionState", "offline"))
    content = {"failed": "§cfailed", "connecting": "§econnecting", "reconnecting": "§ereconnecting"}.get(
        state, "§8offline"
    )
    radius = status.get("chunkRadius")
    if isinstance(radius, int | float):
        source = "" if status.get("chunkRadiusSource") == "bot" else " (shared)"
        content += f"§r · chunk radius {radius:g}{source}"
    return content + "§r"


def bot_screen(view: BotView, caller: str | None = None, players: tuple[str, ...] = ()) -> ActionScreen:
    """Action screen for one Bot, grouped by what the player is doing.

    ``caller`` is the viewing player's name and ``players`` the online player
    names, both read on the server thread when the form is shown.
    """
    name = view.name
    state = str(view.status.get("connectionState", ""))
    back = Button("Back to Bot", run(menu_argv(name)))

    def category(title, *buttons, content=""):
        return Show(ActionScreen(f"{name}: {title}", content, (*buttons, back)))

    items: list[Button | Header]
    if state == "online":
        teleport = []
        if caller:
            teleport.append(Button("Teleport to me", run((name, "tp", shlex.quote(caller)))))
        teleport.append(Button("Teleport...", Show(teleport_modal(name, tuple(p for p in players if p != name)))))
        items = [
            Button("Stop everything", run((name, "stop"))),
            Button(
                "Movement...",
                category(
                    "movement",
                    Button("Forward", run((name, "move", "forward"))),
                    Button("Backward", run((name, "move", "backward"))),
                    Button("Left", run((name, "move", "left"))),
                    Button("Right", run((name, "move", "right"))),
                    Button("Jump...", Show(action_modal(name, "jump"))),
                    Button("Sprint / sneak...", Show(flags_modal(name))),
                ),
            ),
            Button("Look...", Show(look_modal(name, view.observed))),
            Button("Teleport...", category("teleport", *teleport)),
            Button(
                "Gameplay...",
                category(
                    "gameplay",
                    Button("Attack...", Show(action_modal(name, "attack"))),
                    Button("Use item...", Show(action_modal(name, "use"))),
                    Button("Destroy blocks...", Show(destroy_modal(name))),
                ),
            ),
            Button(
                "Inventory...",
                category(
                    "inventory",
                    Button("Hotbar slot...", Show(hotbar_modal(name, view.status))),
                    Button("Drop item...", Show(drop_modal(name))),
                ),
            ),
            Button(
                "Bot...",
                category(
                    "bot",
                    Button("Reconnect", run((name, "reconnect"))),
                    Button("Despawn...", Show(despawn_confirm(name))),
                    Button("Chunk radius...", Show(radius_modal(name, view.status))),
                    content=view.summary,
                ),
            ),
        ]
    else:
        items = [
            Button("Resume (where it was)", run((name, "resume"))),
            Button("Spawn here", run((name, "spawn"))),
        ]
        if state in {"connecting", "reconnecting"}:
            items.append(Button("Despawn...", Show(despawn_confirm(name))))
        items.append(Button("Chunk radius...", Show(radius_modal(name, view.status))))
    items.append(Button("Back to Bot list", run(menu_argv())))
    if state == "online":
        content = "§2online§r"
        if view.observed:
            observed = view.observed
            content += f" · {observed['dimension']} {observed['x']:.1f} {observed['y']:.1f} {observed['z']:.1f}"
        mining = view.status.get("mining") or {}
        if mining.get("continuous") or mining.get("state") == "active":
            content += " · mining " + ("continuous" if mining.get("continuous") else "active")
    else:
        content = _offline_content(view.status)
    return ActionScreen(f"Endbot: {name}", content, tuple(items))


def spawn_modal() -> ModalScreen:
    def build(values):
        (name,) = values
        name = _text(name)
        if not name:
            raise FormResponseError("enter a Bot name")
        return ((name, "spawn"),)

    return ModalScreen(
        "Spawn a new Bot",
        (TextInput("Bot name (letters, digits, underscore; up to 16)", "Alice"),),
        build,
        "Spawn",
    )


def look_modal(name: str, observed: dict | None) -> ModalScreen:
    yaw = round(float(observed["yaw"])) if observed else 0
    pitch = round(float(observed["pitch"])) if observed else 0

    def build(values):
        yaw_value, pitch_value, target = values
        target = _text(target)
        if target:
            return ((name, "look", "at", *_coordinates(target)),)
        return ((name, "look", _number(yaw_value), _number(pitch_value)),)

    return ModalScreen(
        f"{name}: look",
        (
            Slider("Yaw", -180, 180, 5, max(-180, min(180, yaw))),
            Slider("Pitch", -90, 90, 5, max(-90, min(90, pitch))),
            TextInput("Or look at x y z (overrides the sliders; ~ is relative to the Bot)", "~ ~-1 ~2"),
        ),
        build,
        "Look",
    )


def flags_modal(name: str) -> ModalScreen:
    def build(values):
        commands = []
        for flag, value in zip(("sprint", "sneak"), values, strict=True):
            choice = _option(FLAG_CHOICES, value)
            if choice != "no change":
                commands.append((name, flag, choice))
        return tuple(commands)

    return ModalScreen(
        f"{name}: sprint / sneak",
        (Dropdown("Sprint", FLAG_CHOICES), Dropdown("Sneak", FLAG_CHOICES)),
        build,
        "Apply",
    )


def action_modal(name: str, action: str) -> ModalScreen:
    def build(values):
        mode_value, ticks = values
        mode = _option(ACTION_MODES, mode_value)
        if mode == "interval":
            return ((name, action, "interval", _text(ticks) or "20"),)
        return ((name, action, mode),)

    return ModalScreen(
        f"{name}: {action}",
        (Dropdown("Mode", ACTION_MODES), TextInput("Interval ticks (interval mode only; 20 = 1 second)", "20", "20")),
        build,
        "Start" if action != "jump" else "Jump",
    )


def destroy_modal(name: str) -> ModalScreen:
    face_options = ("automatic", *FACES)

    def build(values):
        mode_value, target, face_value = values
        mode = _option(DESTROY_MODES, mode_value)
        if mode != "block at coordinates":
            return ((name, "destroy", mode),)
        face = _option(face_options, face_value)
        suffix = () if face == "automatic" else (face,)
        return ((name, "destroy", *_coordinates(_text(target)), *suffix),)

    return ModalScreen(
        f"{name}: destroy",
        (
            Dropdown("Mode (continuous keeps breaking blocks in sight until destroy stop)", DESTROY_MODES),
            TextInput("Block x y z (coordinates mode; ~ is relative to the Bot)", "~ ~-1 ~"),
            Dropdown("Face (coordinates mode)", face_options),
        ),
        build,
        "Destroy",
    )


def teleport_modal(name: str, players: tuple[str, ...]) -> ModalScreen:
    options = (*players, TELEPORT_COORDINATES)

    def build(values):
        destination_value, target = values
        destination = _option(options, destination_value)
        if destination == TELEPORT_COORDINATES:
            return ((name, "tp", *_coordinates(_text(target))),)
        return ((name, "tp", shlex.quote(destination)),)

    return ModalScreen(
        f"{name}: teleport",
        (
            Dropdown("Destination", options),
            TextInput("x y z (coordinates only; ~ is relative to you)", "~ ~ ~"),
        ),
        build,
        "Teleport",
    )


def hotbar_modal(name: str, status: dict | None = None) -> ModalScreen:
    slots = tuple(str(slot) for slot in range(1, 10))

    def build(values):
        (slot,) = values
        return ((name, "hotbar", _option(slots, slot)),)

    selected = (status or {}).get("selectedHotbarSlot", 1)
    default = selected - 1 if isinstance(selected, int) and 1 <= selected <= 9 else 0
    return ModalScreen(f"{name}: hotbar", (StepSlider("Hotbar slot", slots, default),), build, "Select")


def drop_modal(name: str) -> ModalScreen:
    options = ("one item", "whole stack")

    def build(values):
        choice = _option(options, values[0])
        return ((name, "drop", "stack") if choice == "whole stack" else (name, "drop"),)

    return ModalScreen(f"{name}: drop", (Dropdown("Amount from the selected hotbar slot", options),), build, "Drop")


def radius_modal(name: str, status: dict) -> ModalScreen:
    current = status.get("chunkRadius")
    default = int(current) if isinstance(current, int | float) and 2 <= current <= 32 else 8

    def build(values):
        use_default, radius = values
        if _bool(use_default):
            return ((name, "radius", "default"),)
        return ((name, "radius", str(round(_float(radius)))),)

    return ModalScreen(
        f"{name}: chunk radius",
        (
            Toggle("Use the shared default", status.get("chunkRadiusSource") != "bot"),
            Slider("Chunk radius (this Bot only)", 2, 32, 1, default),
        ),
        build,
        "Apply",
    )


def despawn_confirm(name: str) -> ConfirmScreen:
    return ConfirmScreen(
        f"{name}: despawn",
        f"Take {name} offline? It stays registered; resume or spawn brings it back.",
        "Despawn",
        "Cancel",
        run((name, "despawn")),
    )


def modal_commands(screen: ModalScreen, response: str | None) -> tuple[Argv, ...]:
    """Decode an endstone ModalForm response (a JSON array) into commands."""
    try:
        values = json.loads(response) if response else None
    except (TypeError, ValueError) as error:
        raise FormResponseError("the form response was not understood") from error
    if not isinstance(values, list) or len(values) != len(screen.controls):
        raise FormResponseError("the form response was not understood")
    try:
        return tuple(tuple(command) for command in screen.build(values))
    except (TypeError, ValueError) as error:
        if isinstance(error, FormResponseError):
            raise
        raise FormResponseError("the form response was not understood") from error


def _option(options: tuple[str, ...], value) -> str:
    if isinstance(value, bool) or not isinstance(value, int | float) or value != int(value):
        raise FormResponseError("the form response was not understood")
    index = int(value)
    if not 0 <= index < len(options):
        raise FormResponseError("the form response was not understood")
    return options[index]


def _float(value) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise FormResponseError("the form response was not understood")
    return float(value)


def _number(value) -> str:
    return f"{_float(value):g}"


def _bool(value) -> bool:
    if not isinstance(value, bool):
        raise FormResponseError("the form response was not understood")
    return value


def _text(value) -> str:
    if not isinstance(value, str):
        raise FormResponseError("the form response was not understood")
    return value.strip()


def _coordinates(text: str) -> tuple[str, ...]:
    tokens = tuple(text.split())
    if len(tokens) != 3:
        raise FormResponseError("enter exactly three coordinates: x y z")
    return tokens
