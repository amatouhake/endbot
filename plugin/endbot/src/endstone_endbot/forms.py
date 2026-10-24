"""Endstone-free screen models for the /bot menu forms.

Forms never execute anything themselves: every button or modal submission
resolves to the same argument lists a typed /bot command would carry, and the
plugin feeds them through the normal command path. Authorization, revocation,
validation and reply wording therefore stay identical to typed commands.
Screens take the viewer's language; command words stay English.
"""

from __future__ import annotations

import json
import re
import shlex
from collections.abc import Callable
from dataclasses import dataclass, field

from endstone_endbot.i18n import LocalizedError, Text, tr

Argv = tuple[str, ...]

DIRECTIONS = ("forward", "backward", "left", "right", "stop")
FACES = ("down", "up", "north", "south", "west", "east")
FLAG_CHOICES = ("no change", "on", "off")
TELEPORT_COORDINATES = "coordinates below"


class FormResponseError(LocalizedError):
    pass


@dataclass(frozen=True, slots=True)
class GroupTarget:
    name: str
    count: int
    guard: object


@dataclass(frozen=True, slots=True)
class BotListView:
    """Runtime data for the Bot list; produced off the server thread."""

    bots: tuple[dict, ...]
    groups: tuple[GroupTarget, ...] = ()
    scripting: bool = False


@dataclass(frozen=True, slots=True)
class BotView:
    """Runtime data for one Bot's action screen; produced off the server thread."""

    name: str
    summary: str
    status: dict
    observed: dict | None = None
    guard: object | None = None
    back: Run | None = None
    back_label: str = "Back to Bot list"
    management: Run | None = None


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


@dataclass(frozen=True, slots=True)
class Label:
    """Read-only text in a modal form; its response value is ignored."""

    text: str


Control = Dropdown | Slider | StepSlider | TextInput | Toggle | Label


# Button actions.
@dataclass(frozen=True, slots=True)
class Run:
    """Submit each argument list through the /bot command path, in order."""

    commands: tuple[Argv, ...]
    command: str = "bot"
    guard: object | None = None


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
    guard: object | None = None


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
    command: str = "bot"
    guard: object | None = None


def run(*commands: Argv) -> Run:
    return Run(tuple(commands))


def menu_argv(name: str | None = None) -> Argv:
    return ("menu",) if name is None else (name, "menu")


TRANSITIONAL_STATES = {
    # Dark colors: the button background is light gray, so §7 would vanish.
    "connecting": ("§6", "JOIN"),
    "reconnecting": ("§6", "RETRY"),
    "failed": ("§4", "FAIL"),
}


def bot_list_screen(view: BotListView, lang: str = "en") -> ActionScreen:
    """Online Bots by name; offline ones sit behind one button to keep the list short."""
    bots = sorted(view.bots, key=lambda bot: (bot["connectionState"] != "online", str(bot["name"]).lower()))
    offline = [bot for bot in bots if bot["connectionState"] == "offline"]
    items: list[Button | Header] = []
    for bot in bots:
        state = bot["connectionState"]
        if state == "offline":
            continue
        color, badge = TRANSITIONAL_STATES.get(state, ("§8", "?"))
        text = bot["name"] if state == "online" else f"{color}{tr(lang, badge)}§r · {bot['name']}"
        items.append(Button(text, run(menu_argv(bot["name"]))))
    if offline:
        offline_items = [Button(bot["name"], run(menu_argv(bot["name"]))) for bot in offline]
        offline_items.append(Button(tr(lang, "Back to Bot list"), run(menu_argv())))
        offline_screen = ActionScreen(tr(lang, "Endbot: offline Bots"), tr(lang, "Pick a Bot."), tuple(offline_items))
        items.append(Button(tr(lang, "Offline ({count})...", count=len(offline)), Show(offline_screen)))
    items.append(Button(tr(lang, "+ New Bot here"), Show(spawn_modal(lang))))
    if view.scripting:
        items.append(Button(tr(lang, "Scripts..."), Run((("menu",),), command="botscript")))
    if view.groups:
        items.extend(Button(f"#{group.name} ({group.count})",
                            Run((menu_argv(f"#{group.name}"),), guard=group.guard)) for group in view.groups)
    content = tr(lang, "Pick a Bot or group. (/bot help lists commands)" if view.groups else
                 "Pick a Bot. (/bot help lists commands)" if bots else "No Bot profiles yet. Spawn one to start.")
    return ActionScreen("Endbot", content, tuple(items))


def _offline_content(status: dict, lang: str = "en") -> str:
    state = str(status.get("connectionState", "offline"))
    color = {"failed": "§c", "connecting": "§e", "reconnecting": "§e"}.get(state, "§8")
    content = color + tr(lang, state if color != "§8" else "offline")
    radius = status.get("chunkRadius")
    if isinstance(radius, int | float):
        source = Text("" if status.get("chunkRadiusSource") == "bot" else " (shared)")
        content += "§r · " + tr(lang, "chunk radius {radius}{source}", radius=f"{radius:g}", source=source)
    return content + "§r"


def bot_screen(
    view: BotView, caller: str | None = None, players: tuple[str, ...] = (), lang: str = "en"
) -> ActionScreen:
    """Action screen for one Bot, grouped by what the player is doing.

    ``caller`` is the viewing player's name and ``players`` the online player
    names, both read on the server thread when the form is shown.
    """
    name = view.name
    group = name.startswith("#")
    state = str(view.status.get("connectionState", ""))
    back = Button(tr(lang, "Back to group operations" if group else "Back to Bot"), run(menu_argv(name)))

    def label(text):
        return tr(lang, text)

    def category(title, *buttons, content=""):
        return Show(ActionScreen(tr(lang, "{name}: {title}", name=name, title=Text(title)), content, (*buttons, back)))

    items: list[Button | Header]
    lifecycle = [
        Button(label("Resume (where it was)"), run((name, "resume"))),
        Button(label("Spawn here"), run((name, "spawn"))),
        Button(label("Spawn at coordinates..."), Show(spawn_at_modal(name, lang))),
        Button(label("Reconnect"), run((name, "reconnect"))),
        Button(label("Despawn..."), Show(despawn_confirm(name, lang))),
        Button(label("Chunk radius..."), Show(radius_modal(name, view.status, lang))),
        Button(label("Status"), run((name, "status"))),
    ]
    if not group:
        lifecycle.append(Button(label("Profile..."), category(
            "profile", Button(label("Rename..."), Show(rename_modal(name, lang))),
            Button(label("Forget..."), Show(forget_confirm(name, lang))),
        )))
    if state == "online" or group:
        teleport = []
        if caller:
            teleport.append(Button(label("Teleport to me"), run((name, "tp", shlex.quote(caller)))))
        teleport.append(
            Button(label("Teleport..."), Show(teleport_modal(name, tuple(p for p in players if p != name), lang)))
        )
        items = [
            Button(label("Stop everything"), run((name, "stop"))),
            Button(
                label("Movement..."),
                category(
                    "movement",
                    Button(label("Move..."), Show(move_modal(name, lang))),
                    Button(label("Jump..."), Show(action_screen(name, "jump", lang))),
                    Button(label("Sprint / sneak..."), Show(flags_modal(name, lang))),
                ),
            ),
            Button(label("Look..."), Show(look_modal(name, view.observed, lang))),
            Button(label("Teleport..."), category("teleport", *teleport)),
            Button(
                label("Gameplay..."),
                category(
                    "gameplay",
                    Button(label("Attack..."), Show(action_screen(name, "attack", lang))),
                    Button(label("Use item..."), Show(action_screen(name, "use", lang))),
                    Button(label("Destroy blocks..."), Show(destroy_screen(name, lang))),
                    Button(label("Interact with block..."), Show(interact_modal(name, lang))),
                    Button(label("Interact with entity..."), Show(entity_modal(name, lang))),
                    Button(label("Dismount"), run((name, "dismount"))),
                    Button(label("Look around..."), category(
                        "look around",
                        Button(label("Looking at"), run((name, "target"))),
                        Button(label("List nearby entities"), run((name, "entities"))),
                        Button(label("Entity details..."), Show(entity_details_modal(name, lang))),
                        Button(label("Block info..."), Show(block_modal(name, lang))),
                        Button(label("Find blocks..."), Show(find_blocks_modal(name, lang))),
                    )),
                ),
            ),
            Button(
                label("Inventory..."),
                category(
                    "inventory",
                    Button(label("List inventory"), run((name, "inventory"))),
                    Button(label("Hotbar slot..."), Show(hotbar_modal(name, view.status, lang))),
                    Button(label("Hold item..."), Show(hold_modal(name, lang))),
                    Button(label("Swap slots..."), Show(swap_modal(name, lang))),
                    Button(label("Drop item..."), Show(drop_screen(name, lang))),
                ),
            ),
            Button(
                label("Bot..."),
                category("bot", *lifecycle, content=view.summary),
            ),
        ]
    else:
        items = [
            Button(label("Resume (where it was)"), run((name, "resume"))),
            Button(label("Spawn here"), run((name, "spawn"))),
        ]
        if state in {"connecting", "reconnecting"}:
            items.append(Button(label("Despawn..."), Show(despawn_confirm(name, lang))))
        items.append(Button(label("Chunk radius..."), Show(radius_modal(name, view.status, lang))))
        items.append(Button(label("Bot..."), category("bot", *lifecycle, content=view.summary)))
    if group:
        items.append(Button(label("Recent operation result..."), Run((("menu", name, "result"),), command="botgroup")))
        if view.management is not None:
            items.append(Button(label("Group management..."), view.management))
    items.append(Button(label(view.back_label), view.back or run(menu_argv())))
    if group:
        content = view.summary + "\n" + label("Operations apply to every current member. Limits apply per Bot.")
    elif state == "online":
        content = f"§2{label('online')}§r"
        if view.observed:
            observed = view.observed
            content += f" · {observed['dimension']} {observed['x']:.1f} {observed['y']:.1f} {observed['z']:.1f}"
        mining = view.status.get("mining") or {}
        if mining.get("continuous") or mining.get("state") == "active":
            content += " · " + label("mining continuous" if mining.get("continuous") else "mining active")
        vehicle = view.status.get("vehicle")
        if vehicle:
            content += " · " + tr(lang, "riding {vehicle}",
                                  vehicle=str(vehicle.get("type") or "?").removeprefix("minecraft:"))
    else:
        content = _offline_content(view.status, lang)
    return ActionScreen(f"Endbot: {name}", content, tuple(items))


def spawn_modal(lang: str = "en") -> ModalScreen:
    def build(values):
        (name,) = values
        name = _text(name)
        if not name:
            raise FormResponseError("enter a Bot name")
        return ((name, "spawn"),)

    return ModalScreen(
        tr(lang, "Spawn a new Bot"),
        (TextInput(tr(lang, "Bot name (letters, digits, underscore; up to 16)"), "Alice"),),
        build,
        tr(lang, "Spawn"),
    )


def spawn_at_modal(name: str, lang: str = "en") -> ModalScreen:
    def build(values):
        return ((name, "spawn", "at", *_coordinates(_text(values[0]))),)

    return ModalScreen(f"{name}: spawn at", (
        TextInput(tr(lang, "x y z (coordinates only; ~ is relative to you)"), "~ ~ ~"),
    ), build, tr(lang, "Spawn"))


def rename_modal(name: str, lang: str = "en") -> ModalScreen:
    def build(values):
        return ((name, "rename", _text(values[0])),)

    return ModalScreen(f"{name}: rename", (
        TextInput(tr(lang, "New Bot name"), name),
    ), build, tr(lang, "Rename"))


def forget_confirm(name: str, lang: str = "en") -> ConfirmScreen:
    return ConfirmScreen(f"{name}: forget", tr(lang,
        "Forget {name}? Requires an offline Bot. Its world player data stays intact.", name=name),
        tr(lang, "Forget"), tr(lang, "Cancel"), run((name, "forget")))


def interact_modal(name: str, lang: str = "en") -> ModalScreen:
    def build(values):
        return ((name, "interact", *_coordinates(_text(values[0])), _option(FACES, values[1])),)

    return ModalScreen(f"{name}: interact", (
        TextInput(tr(lang, "x y z (~ is relative to the Bot)"), "~ ~-1 ~"),
        Dropdown("face", FACES),
    ), build, tr(lang, "Interact"))


def entity_modal(name: str, lang: str = "en") -> ModalScreen:
    def build(values):
        selector = _text(values[0]).lower().removeprefix("@")
        if not re.fullmatch(r"nearest|[0-9]{1,19}|(?:[a-z0-9_.-]+:)?[a-z0-9_./-]+", selector):
            raise FormResponseError("Enter one entity type, nearest or id")
        return ((name, "interact", "@" + selector),)

    return ModalScreen(f"{name}: interact", (
        TextInput(tr(lang, "Entity type, nearest or id (from the list)"), "cow", "nearest"),
    ), build, tr(lang, "Interact"))


def entity_details_modal(name: str, lang: str = "en") -> ModalScreen:
    def build(values):
        selector = _text(values[0]).lower().removeprefix("@")
        if not re.fullmatch(r"nearest|[0-9]{1,19}|(?:[a-z0-9_.-]+:)?[a-z0-9_./-]+", selector):
            raise FormResponseError("Enter one entity type, nearest or id")
        return ((name, "entity", "@" + selector),)

    return ModalScreen(f"{name}: entity", (
        TextInput(tr(lang, "Entity type, nearest or id (from the list)"), "cow", "nearest"),
    ), build, tr(lang, "Run"))


def block_modal(name: str, lang: str = "en") -> ModalScreen:
    def build(values):
        return ((name, "block", *_coordinates(_text(values[0]))),)

    return ModalScreen(f"{name}: block", (
        TextInput(tr(lang, "x y z (~ is relative to the Bot)"), "~ ~-1 ~", "~ ~-1 ~"),
    ), build, tr(lang, "Run"))


def find_blocks_modal(name: str, lang: str = "en") -> ModalScreen:
    def build(values):
        block_type = _text(values[0]).lower()
        if not re.fullmatch(r"(?:[a-z0-9_.-]+:)?[a-z0-9_./-]+", block_type):
            raise FormResponseError("Enter one block type")
        return ((name, "blocks", block_type, "radius", str(int(values[1]))),)

    return ModalScreen(f"{name}: blocks", (
        TextInput(tr(lang, "Block type"), "oak_log"),
        Slider(tr(lang, "Radius"), 1, 12, 1, 8),
    ), build, tr(lang, "Run"))


def look_modal(name: str, observed: dict | None, lang: str = "en") -> ModalScreen:
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
            TextInput(tr(lang, "Or look at x y z (overrides the sliders; ~ is relative to the Bot)"), "~ ~-1 ~2"),
        ),
        build,
        tr(lang, "Look"),
    )


def flags_modal(name: str, lang: str = "en") -> ModalScreen:
    def build(values):
        commands = []
        for flag, value in zip(("sprint", "sneak"), values, strict=True):
            choice = _option(FLAG_CHOICES, value)
            if choice != "no change":
                commands.append((name, flag, choice))
        return tuple(commands)

    choices = tuple(tr(lang, choice) for choice in FLAG_CHOICES)
    return ModalScreen(
        f"{name}: sprint / sneak",
        (Dropdown(tr(lang, "Sprint"), choices), Dropdown(tr(lang, "Sneak"), choices)),
        build,
        tr(lang, "Apply"),
    )


# Action forms mirror the command grammar: the first screen offers the
# command's modes, a mode without parameters runs at once, and a mode with
# parameters opens a form holding exactly its clauses, named and measured
# (ticks) as typed. A blank optional clause is omitted; the parser validates.
# Mode buttons and clause names stay English in every language: they are the
# command words the form teaches.
TICKS = Text("20 = 1 second")
FOR_FIELD = Text("for <ticks> (optional; {ticks}; blank = until stopped)")
COUNT_FIELD = Text("count <blocks> (optional; stop after that many blocks)")
HOLD_FIELD = Text("hold <ticks> (optional; charge each press, then release)")
INTERVAL_FIELD = Text("interval <ticks> ({ticks})")
FOR_REQUIRED_FIELD = Text("for <ticks> ({ticks})")
MODE_CONTENT = Text("Pick a mode, as in the typed command. Ticks: {ticks}.")


def _required(value, clause: str) -> Argv:
    ticks = _text(value)
    if not ticks:
        raise FormResponseError("enter {clause} <ticks>", clause=clause)
    return (clause, ticks)


def _optional(value, clause: str) -> Argv:
    ticks = _text(value)
    return (clause, ticks) if ticks else ()


def _clause_form(name: str, words: Argv, fields: tuple[tuple[str, str, bool], ...], lang: str = "en") -> ModalScreen:
    """A form for ``/bot <name> <words...>`` plus ``(clause, label, required)`` fields."""

    def build(values):
        clauses = []
        for (clause, _, required), value in zip(fields, values, strict=True):
            clauses.extend(_required(value, clause) if required else _optional(value, clause))
        return ((name, *words, *clauses),)

    # Required clauses start with a typical value; optional ones start blank.
    controls = tuple(
        TextInput(tr(lang, label, ticks=TICKS), "20" if required else "", "20" if required else "")
        for _, label, required in fields
    )
    return ModalScreen(f"{name}: {' '.join(words)}", controls, build, tr(lang, "Run"))


def action_screen(name: str, action: str, lang: str = "en") -> ActionScreen:
    """``/bot <name> jump|attack|use [once|continuous|interval|stop] ...``"""
    use = action == "use"
    hold = (("hold", HOLD_FIELD, False),) if use else ()
    duration = (("for", FOR_FIELD, False),)
    once = Show(_clause_form(name, (action, "once"), hold, lang)) if use else run((name, action, "once"))
    items = [
        Button("once..." if use else "once", once),
        Button("continuous...", Show(_clause_form(name, (action, "continuous"), (*hold, *duration), lang))),
        Button(
            "interval...",
            Show(_clause_form(name, (action,), (("interval", INTERVAL_FIELD, True), *hold, *duration), lang)),
        ),
    ]
    if use:
        items.append(Button("for...", Show(_clause_form(name, (action,), (("for", FOR_REQUIRED_FIELD, True),), lang))))
    items += [Button("stop", run((name, action, "stop"))),
              Button(tr(lang, "Back to group" if name.startswith("#") else "Back to Bot"), run(menu_argv(name)))]
    return ActionScreen(f"{name}: {action}", tr(lang, MODE_CONTENT, ticks=TICKS), tuple(items))


def move_modal(name: str, lang: str = "en") -> ModalScreen:
    """``/bot <name> move forward|backward|left|right|stop [for <ticks>]``"""

    def build(values):
        direction, duration = values
        return ((name, "move", _option(DIRECTIONS, direction), *_optional(duration, "for")),)

    return ModalScreen(
        f"{name}: move",
        (Dropdown(tr(lang, "direction"), DIRECTIONS), TextInput(tr(lang, FOR_FIELD, ticks=TICKS), "", "")),
        build,
        tr(lang, "Run"),
    )


def destroy_screen(name: str, lang: str = "en") -> ActionScreen:
    """``/bot <name> destroy [once|continuous [for|count]|stop]`` or ``destroy <x> <y> <z> [face]``"""
    continuous = _clause_form(
        name, ("destroy", "continuous"), (("for", FOR_FIELD, False),
        ("count", Text("count <blocks> (optional; per Bot)") if name.startswith("#") else COUNT_FIELD, False)), lang
    )
    return ActionScreen(
        f"{name}: destroy",
        tr(lang, MODE_CONTENT, ticks=TICKS),
        (
            Button("once", run((name, "destroy", "once"))),
            Button("continuous...", Show(continuous)),
            Button("x y z...", Show(destroy_coordinates_modal(name, lang))),
            Button("stop", run((name, "destroy", "stop"))),
            Button(tr(lang, "Back to group" if name.startswith("#") else "Back to Bot"), run(menu_argv(name))),
        ),
    )


def destroy_coordinates_modal(name: str, lang: str = "en") -> ModalScreen:
    face_options = ("automatic", *FACES)

    def build(values):
        target, face_value = values
        face = _option(face_options, face_value)
        suffix = () if face == "automatic" else (face,)
        return ((name, "destroy", *_coordinates(_text(target)), *suffix),)

    return ModalScreen(
        f"{name}: destroy x y z",
        (
            TextInput(tr(lang, "x y z (~ is relative to the Bot)"), "~ ~-1 ~"),
            Dropdown("face", (tr(lang, "automatic"), *FACES)),
        ),
        build,
        tr(lang, "Run"),
    )


def teleport_modal(name: str, players: tuple[str, ...], lang: str = "en") -> ModalScreen:
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
            Dropdown(tr(lang, "Destination"), (*players, tr(lang, TELEPORT_COORDINATES))),
            TextInput(tr(lang, "x y z (coordinates only; ~ is relative to you)"), "~ ~ ~"),
        ),
        build,
        tr(lang, "Teleport"),
    )


def hotbar_modal(name: str, status: dict | None = None, lang: str = "en") -> ModalScreen:
    slots = tuple(str(slot) for slot in range(1, 10))

    def build(values):
        (slot,) = values
        return ((name, "hotbar", _option(slots, slot)),)

    selected = (status or {}).get("selectedHotbarSlot", 1)
    default = selected - 1 if isinstance(selected, int) and 1 <= selected <= 9 else 0
    return ModalScreen(
        f"{name}: hotbar", (StepSlider(tr(lang, "Hotbar slot"), slots, default),), build, tr(lang, "Select")
    )


def hold_modal(name: str, lang: str = "en") -> ModalScreen:
    def build(values):
        item = _text(values[0])
        if not re.fullmatch(r"(?:minecraft:)?[a-z0-9_./-]+", item):
            raise FormResponseError("Enter one item identifier")
        return ((name, "hold", item),)

    return ModalScreen(f"{name}: hold", (TextInput(tr(lang, "Item identifier"), "diamond_pickaxe"),),
                       build, tr(lang, "Run"))


def swap_modal(name: str, lang: str = "en") -> ModalScreen:
    slots = tuple(str(slot) for slot in range(1, 37))

    def build(values):
        return ((name, "swap", _option(slots, values[0]), _option(slots, values[1])),)

    return ModalScreen(f"{name}: swap", (Dropdown(tr(lang, "First slot (1-36)"), slots),
                                        Dropdown(tr(lang, "Second slot (1-36)"), slots, 1)),
                       build, tr(lang, "Run"))


def drop_screen(name: str, lang: str = "en") -> ActionScreen:
    slots = tuple(str(slot) for slot in range(1, 37)) + ("offhand", "head", "chest", "legs", "feet")

    def slot_command(values):
        return ((name, "drop", _option(slots, values[0])),)

    def item_command(values):
        item, count = (_text(value) for value in values)
        if not re.fullmatch(r"(?:minecraft:)?[a-z0-9_./-]+", item):
            raise FormResponseError("Enter one item identifier")
        # This form always selects an item. Bare reserved tokens must never
        # turn an item search into an unconfirmed drop-all or a slot drop.
        if item in slots or item in {"all", "stack"}:
            item = f"minecraft:{item}"
        return ((name, "drop", item, *((count,) if count else ())),)

    return ActionScreen(f"{name}: drop", tr(lang, "BDS validates each drop request."), (
        Button(tr(lang, "Selected one / stack..."), Show(drop_modal(name, lang))),
        Button(tr(lang, "A slot..."), Show(ModalScreen(f"{name}: drop slot", (
            Dropdown(tr(lang, "Inventory slot (whole stack)"), tuple(tr(lang, slot) for slot in slots)),
        ), slot_command, tr(lang, "Drop")))),
        Button(tr(lang, "An item..."), Show(ModalScreen(f"{name}: drop item", (
            TextInput(tr(lang, "Item identifier"), "stone"),
            TextInput(tr(lang, "Count (blank = every matching stack)"), "5"),
        ), item_command, tr(lang, "Drop")))),
        Button(tr(lang, "Drop all..."), Show(ConfirmScreen(
            f"{name}: drop all", tr(lang, "Request every inventory and equipment stack to be dropped?"),
            tr(lang, "Drop all"), tr(lang, "Cancel"), run((name, "drop", "all")),
        ))),
    ))


def drop_modal(name: str, lang: str = "en") -> ModalScreen:
    options = ("one item", "whole stack")

    def build(values):
        choice = _option(options, values[0])
        return ((name, "drop", "stack") if choice == "whole stack" else (name, "drop"),)

    return ModalScreen(
        f"{name}: drop",
        (Dropdown(tr(lang, "Amount from the selected hotbar slot"), tuple(tr(lang, option) for option in options)),),
        build,
        tr(lang, "Drop"),
    )


def radius_modal(name: str, status: dict, lang: str = "en") -> ModalScreen:
    current = status.get("chunkRadius")
    default = int(current) if isinstance(current, int | float) and 2 <= current <= 32 else 8

    def build(values):
        use_default, radius = values
        if _bool(use_default):
            return ((name, "radius", "default"),)
        return ((name, "radius", str(round(_float(radius)))),)

    return ModalScreen(
        tr(lang, "{name}: chunk radius", name=name),
        (
            Toggle(tr(lang, "Use the shared default"), status.get("chunkRadiusSource") != "bot"),
            Slider(tr(lang, "Chunk radius (per Bot)" if name.startswith("#") else "Chunk radius (this Bot only)"),
                   2, 32, 1, default),
        ),
        build,
        tr(lang, "Apply"),
    )


def despawn_confirm(name: str, lang: str = "en") -> ConfirmScreen:
    return ConfirmScreen(
        f"{name}: despawn",
        tr(lang, "Take {name} offline? It stays registered; resume or spawn brings it back.", name=name),
        tr(lang, "Despawn"),
        tr(lang, "Cancel"),
        run((name, "despawn")),
    )


def modal_commands(screen: ModalScreen, response: str | None) -> tuple[Argv, ...]:
    """Decode an endstone ModalForm response (a JSON array) into commands."""
    try:
        values = json.loads(response) if response else None
    except (TypeError, ValueError) as error:
        raise FormResponseError("the form response was not understood") from error
    labels = [index for index, control in enumerate(screen.controls) if isinstance(control, Label)]
    if isinstance(values, list) and labels and len(values) == len(screen.controls) - len(labels):
        # A client may leave labels out of the response; keep one value per control.
        for index in labels:
            values.insert(index, None)
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
