"""Endstone-free script menus. Save capabilities exist only on paste forms."""

from dataclasses import dataclass

from endstone_endbot import forms as f
from endstone_endbot.i18n import tr
from endstone_endbot.script_params import declarations


@dataclass(frozen=True)
class ScriptGuard:
    revision: str | None = None
    name: str | None = None
    edit: bool = False
    values: dict | None = None
    targets: dict | None = None


TEMPLATES = {
    "Mine at spots": """local a = params {
    param.bot "name",
    param.pos("start", "~ ~ ~"),
    param.number("spots", 2),
    param.number("step", 4),
    param.number("blocks", 2),
}
assert(a.spots >= 1 and a.spots % 1 == 0, "spots must be a positive integer")
assert(a.blocks >= 1 and a.blocks % 1 == 0, "blocks must be a positive integer")
local miner = bot(a.name)
waitOnline(miner)
for i = 1, a.spots do
    local r = miner:tp(a.start.x + (i - 1) * a.step, a.start.y, a.start.z, {yaw = 0, pitch = 0})
    if not r.ok then error(r.reason or "placement rejected") end
    r = miner:destroy{mode = "continuous", count = a.blocks}
    if not r.ok then error(r.reason or "destroy rejected") end
    r = miner:waitMining{timeout = 1200}
    log("spot " .. i .. ": " .. r.reason)
    if not r.ok then error(r.reason) end
end
""",
    "Group row": """local a = params { param.group "group", param.pos("origin", "~ ~ ~"), param.number("spacing", 2) }
for i, member in group(a.group):members() do
    local r = member:tp(a.origin.x + (i - 1) * a.spacing, a.origin.y, a.origin.z)
    if not r.ok then error(r.reason or "placement rejected") end
end
""",
    "Simple patrol": """local a = params { param.bot "name", param.number("ticks", 40) }
waitOnline(bot(a.name))
for i = 1, 4 do
    local r = bot(a.name):move("forward", {ticks = a.ticks})
    if not r.ok then error(r.reason or "move rejected") end
    wait(a.ticks)
    bot(a.name):look(i * 90, 0)
end
""",
}


def run(*argv, guard=None):
    return f.Run((tuple(argv),), command="botscript", guard=guard)


def list_screen(scripts, lang="en"):
    items = [f.Button(s.name, run("menu", s.name, guard=ScriptGuard(s.revision, s.name))) for s in scripts]
    templates = [
        f.Button(tr(lang, title), f.Show(edit_screen(None, lang, source))) for title, source in TEMPLATES.items()
    ]
    templates.append(f.Button(tr(lang, "Blank script"), f.Show(edit_screen(None, lang, "params {}\n"))))
    items.append(
        f.Button(
            tr(lang, "+ New script..."),
            f.Show(
                f.ActionScreen(
                    tr(lang, "Script templates"),
                    tr(lang, "Choose a template, then paste or edit source."),
                    tuple(templates),
                )
            ),
        )
    )
    items.append(f.Button(tr(lang, "Back to Bot list"), f.run(f.menu_argv())))
    return f.ActionScreen(tr(lang, "Endbot: scripts"), tr(lang, "Pick a script."), tuple(items))


def script_screen(script, lang="en"):
    guard = ScriptGuard(script.revision, script.name)
    delete = f.ConfirmScreen(
        tr(lang, "Delete script"),
        tr(lang, "Delete script {name}?", name=script.name),
        tr(lang, "Delete"),
        tr(lang, "Cancel"),
        run("delete", script.name, guard=guard),
    )
    items = (
        f.Button(tr(lang, "Run"), run("parameters", script.name, guard=guard)),
        f.Button(tr(lang, "Show source"), run("source", script.name, guard=guard)),
        f.Button(tr(lang, "Stop runs"), run("stop", script.name, guard=guard)),
        f.Button(tr(lang, "Delete"), f.Show(delete)),
        f.Button(tr(lang, "Edit source..."), run("edit", script.name, guard=guard)),
        f.Button(tr(lang, "Scripts..."), run("menu")),
    )
    return f.ActionScreen(script.name, tr(lang, "Run or edit this shared script."), items)


def edit_screen(script, lang="en", source=""):
    guard = ScriptGuard(script.revision if script else None, script.name if script else None, edit=True)

    def build(values):
        name = script.name if script else f._text(values[0])
        text = values[-1]
        if not isinstance(text, str):
            raise f.FormResponseError("the form response was not understood")
        return (("save", name, text),)

    controls = (() if script else (f.TextInput(tr(lang, "Script name")),)) + (
        f.TextInput(tr(lang, "Luau source (paste multiple lines)"), default_value=script.source if script else source),
    )
    return f.ModalScreen(
        tr(lang, "Edit script"), controls, build, submit=tr(lang, "Save"), command="botscript", guard=guard
    )


def run_screen(script, schema, groups, bots, origin, lang="en"):
    schema = declarations(schema)
    controls, choices = [], {}
    for name, spec in schema.items():
        kind, default = spec["type"], spec.get("default", "")
        label = tr(lang, "{name} ({kind})", name=name, kind=kind)
        if kind in {"group", "bot"}:
            options = tuple("#" + g.name for g in groups) if kind == "group" else tuple(b["name"] for b in bots)
            if not options:
                raise f.FormResponseError("No targets available for script parameters")
            choices[name] = options
            controls.append(f.Dropdown(label, options, options.index(default) if default in options else 0))
        elif kind == "boolean":
            controls.append(f.Toggle(label, default if type(default) is bool else False))
        else:
            if kind == "pos" and origin is not None and default in {"", "~ ~ ~"}:
                default = " ".join(str(origin[axis]) for axis in ("x", "y", "z"))
            controls.append(f.TextInput(label, default_value=str(default)))
    values, targets = {}, {}
    guard = ScriptGuard(script.revision, script.name, values=values, targets=targets)

    def build(response):
        values.clear()
        targets.clear()
        for (name, spec), value in zip(schema.items(), response):
            if name in choices:
                value = f._option(choices[name], value)
                if spec["type"] == "group":
                    selected = next(group for group in groups if "#" + group.name == value)
                    targets[name] = ("group", value, selected.identity, selected.members)
                else:
                    selected = next(bot for bot in bots if bot["name"] == value)
                    targets[name] = ("bot", value, selected["identityId"])
            values[name] = value
        return (("run", script.name),)

    return f.ModalScreen(script.name, tuple(controls), build, submit=tr(lang, "Run"), command="botscript", guard=guard)


def source_pages(script):
    lines = []
    for index, line in enumerate(script.source.splitlines()):
        chunks = [line[start : start + 200] for start in range(0, len(line), 200)] or [""]
        lines.extend(f"{index + 1}: {chunk}" for chunk in chunks)
    return ["\n".join(lines[start : start + 8]) for start in range(0, len(lines), 8)] or [""]


def source_screen(script, page, lang="en"):
    pages = source_pages(script)
    guard = ScriptGuard(script.revision, script.name)
    items = []
    if page > 1:
        items.append(f.Button(tr(lang, "Previous page"), run("source", script.name, str(page - 1), guard=guard)))
    if page < len(pages):
        items.append(f.Button(tr(lang, "Next page"), run("source", script.name, str(page + 1), guard=guard)))
    items.append(f.Button(tr(lang, "Scripts..."), run("menu", script.name, guard=guard)))
    return f.ActionScreen(f"{script.name} ({page}/{len(pages)})", pages[page - 1], tuple(items))
