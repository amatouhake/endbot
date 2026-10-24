"""Endstone-free script menus. Save capabilities exist only on paste forms."""

import time
from dataclasses import dataclass

from endstone_endbot import forms as f
from endstone_endbot.i18n import Text, tr
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
    local r = miner:tp(a.start + vector.create((i - 1) * a.step, 0, 0), {yaw = 0, pitch = 0})
    assert(r.ok, r.reason)
    r = miner:destroy{mode = "continuous", count = a.blocks}
    assert(r.ok, r.reason)
    r = miner:waitMining{timeout = 1200}
    log("spot " .. i .. ": " .. r.reason)
    assert(r.ok, r.reason)
end
""",
    "Group row": """local a = params { param.group "group", param.pos("origin", "~ ~ ~"), param.number("spacing", 2) }
for i, member in group(a.group):members() do
    local r = member:tp(a.origin + vector.create((i - 1) * a.spacing, 0, 0))
    assert(r.ok, r.reason)
end
""",
    "Simple patrol": """local a = params { param.bot "name", param.number("ticks", 40) }
local walker = bot(a.name)
waitOnline(walker)
for i = 1, 4 do
    assert(walker:move("forward", {ticks = a.ticks}).ok, "move rejected")
    wait(a.ticks)
    walker:look(i * 90, 0)
end
""",
}


def run(*argv, guard=None):
    return f.Run((tuple(argv),), command="botscript", guard=guard)


def label(script, own, lang="en"):
    """`name (owner, private)` as lists show it; your own scripts say so."""
    owner = tr(lang, "yours") if own else (script.owner_name or tr(lang, "unowned"))
    return f"{script.name} ({owner}, {tr(lang, script.visibility)})"


def list_screen(scripts, lang="en", own=lambda script: False):
    items = [f.Button(label(s, own(s), lang), run("menu", s.name, guard=ScriptGuard(s.revision, s.name)))
             for s in scripts]
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


def import_screen(lang="en"):
    """Name a new script and read it from the book and quill (or written book) in the main hand."""

    def build(values):
        return (("import", f._text(values[-1])),)

    return f.ModalScreen(tr(lang, "Import from a book"),
                         (f.Label(tr(lang, "Hold the book in your main hand. Pages are joined with line breaks.")),
                          f.TextInput(tr(lang, "Script name"))),
                         build, submit=tr(lang, "Import"), command="botscript")


def rename_screen(script, guard, lang="en"):
    def build(values):
        return (("rename", script.name, f._text(values[-1])),)

    return f.ModalScreen(tr(lang, "Rename script"), (f.TextInput(tr(lang, "New name"), default_value=script.name),),
                         build, submit=tr(lang, "Rename"), command="botscript", guard=guard)


def script_screen(script, lang="en", editable=True, book_export=False):
    guard = ScriptGuard(script.revision, script.name)
    items = [
        f.Button(tr(lang, "Run"), run("parameters", script.name, guard=guard)),
        f.Button(tr(lang, "Show source"), run("source", script.name, guard=guard)),
        f.Button(tr(lang, "Stop runs"), run("stop", script.name, guard=guard)),
    ]
    if book_export:
        items.append(f.Button(tr(lang, "Write into held book"), run("export", script.name, guard=guard)))
    if editable:
        delete = f.ConfirmScreen(
            tr(lang, "Delete script"),
            tr(lang, "Delete script {name}?", name=script.name),
            tr(lang, "Delete"),
            tr(lang, "Cancel"),
            run("delete", script.name, guard=guard),
        )
        visibility = "private" if script.visibility == "shared" else "share"
        items += [
            f.Button(tr(lang, "Edit source..."), run("edit", script.name, guard=guard)),
            f.Button(tr(lang, "Replace from held book"), run("import", script.name, guard=guard)),
            f.Button(tr(lang, "Rename..."), f.Show(rename_screen(script, guard, lang))),
            f.Button(tr(lang, "Make private") if visibility == "private" else tr(lang, "Share with controllers"),
                     run(visibility, script.name, guard=guard)),
            f.Button(tr(lang, "Earlier revisions..."), run("history", script.name, guard=guard)),
            f.Button(tr(lang, "Delete"), f.Show(delete)),
        ]
    items += [
        f.Button(tr(lang, "Copy and edit..."), f.Show(edit_screen(None, lang, script.source, f"{script.name}_copy"))),
        f.Button(tr(lang, "Scripts..."), run("menu")),
    ]
    content = (tr(lang, "Your script ({visibility}).", visibility=tr(lang, script.visibility)) if editable
               else tr(lang, "Shared by {owner}. Run it, or copy it to edit your own.",
                       owner=script.owner_name or tr(lang, "unowned")))
    return f.ActionScreen(script.name, content, tuple(items))


def numbered(source):
    """The whole source with line numbers, for the read-only preview above the paste field."""
    return "\n".join(f"{index}: {line}" for index, line in enumerate(source.splitlines(), start=1))


# Edit modes in dropdown order. Line modes take "from" and an optional "to" line number.
EDIT_MODES = ("Replace all", "Append at end", "Replace lines", "Insert after line", "Delete lines")


def _line_number(value, low, high, label):
    text = f._text(value)
    if not text:
        return None
    if not text.isascii() or not text.isdecimal() or not low <= int(text) <= high:
        raise f.FormResponseError("{field} must be a line number from {low} to {high}", field=Text(label), low=low,
                                  high=high)
    return int(text)


def apply_edit(source, mode, pasted, first, last):
    """The source after one edit; ``first``/``last`` are 1-based line numbers (strings from the form).

    Pasted text replaces whole lines, so a trailing newline in it adds nothing.
    """
    lines = source.splitlines()
    pasted = pasted.replace("\r\n", "\n").replace("\r", "\n")
    new = pasted.removesuffix("\n").split("\n") if pasted.strip() else []
    if mode == "Replace all":
        if not new:
            raise f.FormResponseError("Paste the new source; nothing was changed")
        result = new
    elif mode == "Append at end":
        if not new:
            raise f.FormResponseError("Paste the lines to add; nothing was changed")
        result = lines + new
    elif mode == "Insert after line":
        at = _line_number(first, 0, len(lines), "From line")
        if at is None or not new:
            raise f.FormResponseError("Enter a line number (0 = top) and paste the lines to insert")
        result = lines[:at] + new + lines[at:]
    else:
        if not lines:
            raise f.FormResponseError("The script has no lines yet")
        start = _line_number(first, 1, len(lines), "From line")
        if start is None:
            raise f.FormResponseError("Enter the first line to change")
        end = _line_number(last, start, len(lines), "To line") or start
        if mode == "Replace lines" and not new:
            raise f.FormResponseError("Paste the replacement lines, or choose Delete lines")
        result = lines[: start - 1] + (new if mode == "Replace lines" else []) + lines[end:]
    return "\n".join(result) + "\n"


def edit_screen(script, lang="en", source="", name=""):
    """Edit by pasting into an empty field: replace all, append, or replace/insert/delete numbered lines.

    A new script (template or copy) starts from ``source``; leaving the field empty keeps it as is.
    """
    guard = ScriptGuard(script.revision if script else None, script.name if script else None, edit=True)
    text = script.source if script else source

    def build(values):
        name = script.name if script else f._text(values[0])
        mode = f._option(EDIT_MODES, values[-4])
        pasted = values[-1]
        if not isinstance(pasted, str):
            raise f.FormResponseError("the form response was not understood")
        if script is None and mode == "Replace all" and not pasted.strip():
            return (("save", name, text),)
        return (("save", name, apply_edit(text, mode, pasted, values[-3], values[-2])),)

    # The client's text input is about two lines tall and cannot select all, so it starts empty;
    # the label shows the whole source with line numbers for the line modes.
    preview = (f.Label(tr(lang, "Current source:") + "\n" + numbered(text)),) if text.strip() else ()
    hint = tr(lang, "Leave empty to keep the source above.") if script is None and text.strip() else ""
    controls = (() if script else (f.TextInput(tr(lang, "Script name"), default_value=name),)) + preview + (
        f.Dropdown(tr(lang, "Change"), tuple(tr(lang, mode) for mode in EDIT_MODES)),
        f.TextInput(tr(lang, "From line (insert: 0 = top)")),
        f.TextInput(tr(lang, "To line (optional)")),
        f.TextInput((tr(lang, "Luau source (paste multiple lines)") + " " + hint).strip()),
    )
    return f.ModalScreen(
        tr(lang, "Edit script"), controls, build, submit=tr(lang, "Save"), command="botscript", guard=guard
    )


def revision_line(index, revision, lang="en"):
    saved = time.strftime("%Y-%m-%d %H:%M", time.localtime(revision.saved_at)) if revision.saved_at else "?"
    return tr(lang, "{index}: {saved}, {lines} lines", index=index, saved=saved,
              lines=len(revision.source.splitlines()))


def history_screen(script, editable, lang="en"):
    guard = ScriptGuard(script.revision, script.name)
    items = []
    for index, revision in enumerate(script.history, start=1):
        line = revision_line(index, revision, lang)
        if editable:
            confirm = f.ConfirmScreen(
                tr(lang, "Restore revision"),
                tr(lang, "Restore {name} to revision {line}? The current source stays in the history.",
                   name=script.name, line=line) + "\n\n" + numbered(revision.source),
                tr(lang, "Restore"), tr(lang, "Cancel"),
                run("restore", script.name, str(index), guard=guard))
            items.append(f.Button(line, f.Show(confirm)))
        else:
            items.append(f.Header(line))
    items.append(f.Button(tr(lang, "Scripts..."), run("menu", script.name, guard=guard)))
    content = tr(lang, "Earlier revisions, newest first.") if script.history else tr(lang, "no earlier revisions")
    return f.ActionScreen(script.name, content, tuple(items))


def run_screen(script, schema, groups, bots, origin, lang="en"):
    schema = declarations(schema)
    controls, choices = [], {}
    for name, spec in schema.items():
        kind, default = spec["type"], spec.get("default", "")
        label = tr(lang, "{name} ({kind})", name=name, kind=kind)
        if kind in {"group", "bot", "choice"}:
            options = (tuple("#" + g.name for g in groups) if kind == "group" else tuple(b["name"] for b in bots)
                       if kind == "bot" else tuple(spec["choices"]))
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
                elif spec["type"] == "bot":
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
