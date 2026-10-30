"""Endstone-free script menus. Save capabilities exist only on edit forms."""

import time
from dataclasses import dataclass

from endbot_core import forms as f
from endbot_core.i18n import tr
from endbot_core.script_params import declarations


@dataclass(frozen=True)
class ScriptGuard:
    revision: str | None = None
    name: str | None = None
    edit: bool = False
    # Set on line-editor saves: the editor page to come back to.
    page: int | None = None
    # Set on book confirmations: the book text (import) or pages (export) the player saw.
    book: str | tuple[str, ...] | None = None
    values: dict | None = None
    targets: dict | None = None
    # Set on run forms: the requires {} declaration the form's inspection read, which the run must match.
    requires: tuple[str, ...] | None = None


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

# Line numbers are colored so wrapped source lines stay apart from them: gray on the dark
# form background, dark gray on the light button background (where §7 would vanish).
GUTTER, BUTTON_GUTTER = "§7", "§8"
# Source characters the script screen shows before pointing at /botscript show.
VIEW_CHARS = 6000
# Lines per line-editor page, and the characters of a line its buttons show.
EDIT_PAGE_LINES = 40
BUTTON_CHARS = 60


def run(*argv, guard=None):
    return f.Run((tuple(argv),), command="botscript", guard=guard)


def label(script, own, lang="en"):
    """`name (owner, private)` as lists show it; your own scripts say so."""
    owner = tr(lang, "file") if script.file else tr(lang, "yours") if own else (
        script.owner_name or tr(lang, "unowned"))
    return f"{script.name} ({owner}, {tr(lang, script.visibility)})"


def shown(text):
    """Source text as the client should display it: a `§` in it would start a formatting code, so it is
    written as the Luau escape `\\u{a7}` (the same character inside a string). Otherwise a shared script
    could color, hide (`§0`) or scramble (`§k`) lines in the preview other controllers read before running it.
    """
    return text.replace("§", "\\u{a7}")


def source_view(source, first=1):
    """Source lines behind a colored, right-aligned line-number gutter."""
    lines = source.splitlines()
    width = len(str(first + len(lines) - 1))
    return "\n".join(f"{GUTTER}{index:>{width}}§r {shown(line)}" for index, line in enumerate(lines, start=first))


def _capped_view(source, lang, name=None):
    """``source_view`` up to VIEW_CHARS; the rest is counted (and pointed at `show` for a stored ``name``)."""
    lines, size = source.splitlines(), 0
    width = len(str(len(lines)))
    for index, line in enumerate(lines):
        size += len(line) + width + 6  # with the gutter's color codes, space and line break
        if size > VIEW_CHARS:
            more = (tr(lang, "... {count} more lines; see /botscript show {name}", count=len(lines) - index,
                       name=name) if name else tr(lang, "... {count} more lines", count=len(lines) - index))
            return source_view("\n".join(lines[:index])) + f"\n{GUTTER}{more}§r"
    return source_view(source)


def list_screen(scripts, lang="en", own=lambda script: False, author=True):
    """``author`` False (a controller, S10) leaves out creating scripts; the server refuses it as well."""
    items = [f.Button(label(s, own(s), lang), run("menu", s.name, guard=ScriptGuard(s.revision, s.name)))
             for s in scripts]
    if author:
        items.append(_new_script_button(lang))
    items.append(f.Button(tr(lang, "Reload script files"), run("reload")))
    items.append(f.Button(tr(lang, "Back to Bot list"), f.run(f.menu_argv())))
    return f.ActionScreen(tr(lang, "Endbot: scripts"), tr(lang, "Pick a script."), tuple(items))


def _new_script_button(lang):
    templates = [
        f.Button(tr(lang, title), f.Show(new_screen(source, "", lang))) for title, source in TEMPLATES.items()
    ]
    templates += [
        f.Button(tr(lang, "Blank script"), f.Show(new_screen("params {}\n", "", lang))),
        f.Button(tr(lang, "From held book..."), f.Show(import_screen(lang))),
    ]
    return f.Button(
        tr(lang, "+ New script..."),
        f.Show(
            f.ActionScreen(
                tr(lang, "Script templates"),
                tr(lang, "Pick a template to start a new script, or read one from a held book."),
                tuple(templates),
            )
        ),
    )


def new_screen(source, name="", lang="en"):
    """Name a new script starting from ``source`` (a template or a copy); its lines are edited after saving."""

    def build(values):
        return (("save", f._text(values[-1]), source),)

    return f.ModalScreen(
        tr(lang, "New script"),
        (f.Label(_capped_view(source, lang)),
         f.Label(tr(lang, "Name it and save; then change lines with Edit lines.")),
         f.TextInput(tr(lang, "Script name"), default_value=name)),
        build, submit=tr(lang, "Save"), command="botscript", guard=ScriptGuard(edit=True),
    )


def import_screen(lang="en"):
    """Name a new script and read it from the book and quill (or written book) in the main hand."""

    def build(values):
        return (("import", f._text(values[-1])),)

    return f.ModalScreen(tr(lang, "Import from a book"),
                         (f.Label(tr(lang, "Hold the book in your main hand. Pages are joined with line breaks.")),
                          f.TextInput(tr(lang, "Script name"))),
                         build, submit=tr(lang, "Import"), command="botscript", guard=ScriptGuard())


def rename_screen(script, guard, lang="en"):
    def build(values):
        return (("rename", script.name, f._text(values[-1])),)

    return f.ModalScreen(tr(lang, "Rename script"), (f.TextInput(tr(lang, "New name"), default_value=script.name),),
                         build, submit=tr(lang, "Rename"), command="botscript", guard=guard)


def script_screen(script, lang="en", editable=True, book_export=False, author=True):
    """The source itself, then the frequent actions; the owner's other actions are under More.

    ``author`` False (a controller, S10) leaves out copying, editing, renaming and sharing; the owner keeps running,
    making private and deleting. The server refuses the left-out actions as well.
    """
    guard = ScriptGuard(script.revision, script.name)
    # Names hold at most 16 characters, so a long name is shortened before the suffix.
    copy = [f.Button(tr(lang, "Copy and edit..."),
                     f.Show(new_screen(script.source, f"{script.name[:11]}_copy", lang)))] if author else []
    export = [f.Button(tr(lang, "Write into held book"), run("export", script.name, guard=guard))] if book_export \
        else []
    items = [
        f.Button(tr(lang, "Run"), run("parameters", script.name, guard=guard)),
        f.Button(tr(lang, "Stop runs"), run("stop", script.name, guard=guard)),
    ]
    if editable:
        delete = f.ConfirmScreen(
            tr(lang, "Delete script"),
            tr(lang, "Delete script {name}?", name=script.name),
            tr(lang, "Delete"),
            tr(lang, "Cancel"),
            run("delete", script.name, guard=guard),
        )
        visibility = "private" if script.visibility == "shared" else "shared"
        more = (
            *([f.Button(tr(lang, "Rename..."), f.Show(rename_screen(script, guard, lang)))] if author else []),
            *([f.Button(tr(lang, "Make private") if visibility == "private" else tr(lang, "Share with controllers"),
                        run("visibility", script.name, visibility, guard=guard))]
              if author or visibility == "private" else []),
            f.Button(tr(lang, "Earlier revisions..."), run("history", script.name, guard=guard)),
            *copy,
            *export,
            f.Button(tr(lang, "Delete"), f.Show(delete)),
            f.Button(tr(lang, "Back"), run("menu", script.name, guard=guard)),
        )
        if author:
            items.append(f.Button(tr(lang, "Edit lines..."), run("edit", script.name, guard=guard)))
        items.append(f.Button(tr(lang, "More..."), f.Show(f.ActionScreen(script.name, "", more))))
    else:
        items += [*copy, *export]
    items.append(f.Button(tr(lang, "Scripts..."), run("menu")))
    owner = script.owner_name or tr(lang, "unowned")
    content = (tr(lang, "Your script ({visibility}).", visibility=tr(lang, script.visibility)) if editable
               else (tr(lang, "From {file}. Run it, or copy it to edit in game; the operator edits the file.",
                        file=f"scripts/{script.name}.luau") if author
                     else tr(lang, "From {file}. Run it; the operator edits the file.",
                             file=f"scripts/{script.name}.luau")) if script.file
               else tr(lang, "Shared by {owner}. Run it, or copy it to edit your own.", owner=owner) if author
               else tr(lang, "Shared by {owner}. Run it.", owner=owner))
    return f.ActionScreen(script.name, content + "\n\n" + _capped_view(script.source, lang, script.name),
                          tuple(items))


def import_confirm_screen(script, text, lang="en"):
    """Show what the held book would make of the script before replacing all of it."""
    return f.ConfirmScreen(
        tr(lang, "Replace from book"),
        tr(lang, "Replace all of {name} ({old} lines) with the book below ({new} lines)? "
                 "The current source stays in Earlier revisions.",
           name=script.name, old=len(script.source.splitlines()), new=len(text.splitlines()))
        + "\n\n" + _capped_view(text, lang),
        tr(lang, "Replace"), tr(lang, "Cancel"),
        run("import", script.name, guard=ScriptGuard(script.revision, script.name, book=text)),
    )


def export_confirm_screen(script, pages, text, lang="en"):
    """A book and quill with writing is overwritten only after its owner saw what is lost."""
    return f.ConfirmScreen(
        tr(lang, "Overwrite the book"),
        tr(lang, "This book and quill already has writing ({pages} pages). Overwrite it with {name}? "
                 "Its current text is lost.", pages=len(pages), name=script.name)
        + "\n\n" + _capped_view(text, lang),
        tr(lang, "Overwrite"), tr(lang, "Cancel"),
        run("export", script.name, "overwrite", guard=ScriptGuard(script.revision, script.name, book=tuple(pages))),
    )


def _typed_lines(value):
    """Lines typed into one text field; a paste that kept its line breaks becomes several lines."""
    if not isinstance(value, str):
        raise f.FormResponseError("the form response was not understood")
    return [line.rstrip() for line in value.replace("\r\n", "\n").replace("\r", "\n").split("\n")]


def _indent(line):
    return line[: len(line) - len(line.lstrip())]


def _edited(script, lines, page):
    """Save changed lines (the save returns to the editor page); without a change just go back."""
    if lines == script.source.splitlines():
        return (("edit", script.name, str(page)),)
    return (("save", script.name, "\n".join(lines) + "\n"),)


def line_screen(script, index, page, lang="en"):
    """Change one line in place: its text starts filled in, so a fix is a few keystrokes."""
    lines = script.source.splitlines()
    start = max(index - 2, 0)

    def build(values):
        text, below, delete = _typed_lines(values[-3]), _typed_lines(values[-2]), values[-1]
        if not isinstance(delete, bool):
            raise f.FormResponseError("the form response was not understood")
        changed = lines[:index] + ([] if delete else text)
        if any(below):
            # New lines line up with the edited one unless typed with their own indentation.
            indent = _indent(lines[index])
            changed += [line if not line or _indent(line) else indent + line for line in below]
        return _edited(script, changed + lines[index + 1 :], page)

    return f.ModalScreen(
        tr(lang, "{name}: line {line}", name=script.name, line=index + 1),
        (f.Label(source_view("\n".join(lines[start : index + 3]), start + 1)),
         f.TextInput(tr(lang, "Line {line}", line=index + 1), default_value=lines[index]),
         f.TextInput(tr(lang, "Add a line below (optional)"), multiline=True),
         f.Toggle(tr(lang, "Delete this line"))),
        build, submit=tr(lang, "Save"), command="botscript",
        guard=ScriptGuard(script.revision, script.name, edit=True, page=page),
    )


def add_screen(script, at, page, lang="en"):
    """Add lines at the top (``at`` 0) or at the end of the script."""
    lines = script.source.splitlines()

    def build(values):
        new = _typed_lines(values[-1])
        return _edited(script, lines[:at] + new + lines[at:] if any(new) else lines, page)

    return f.ModalScreen(tr(lang, "Add a first line" if at == 0 else "Add a line at the end"),
                         (f.TextInput(tr(lang, "New line"), multiline=True),), build, submit=tr(lang, "Save"),
                         command="botscript",
                         guard=ScriptGuard(script.revision, script.name, edit=True, page=page))


def source_screen(script, page, lang="en"):
    """The whole source in one field, for clients with the optional Endbot UI resource pack.

    Without the pack the client's field drops line breaks, so a reply that lost them is refused
    rather than saved as one long line.
    """
    lines = script.source.splitlines()

    def build(values):
        typed = _typed_lines(values[-1])
        while typed and not typed[-1]:
            typed.pop()
        if typed == [line.rstrip() for line in lines]:
            return (("edit", script.name, str(page)),)
        if len(lines) > 1 and len(typed) <= 1:
            raise f.FormResponseError("The line breaks were lost, so nothing was saved. This needs the Endbot UI "
                                      "resource pack; use the line buttons or a book instead.")
        return (("save", script.name, "\n".join(typed) + "\n"),)

    return f.ModalScreen(
        tr(lang, "{name}: whole script", name=script.name),
        (f.Label(tr(lang, "Edit the whole script, with line breaks. This field needs the optional Endbot UI "
                          "resource pack; without it, saving is refused.")),
         f.TextInput(tr(lang, "Script source"), default_value=script.source.removesuffix("\n"), multiline=True)),
        build, submit=tr(lang, "Save"), command="botscript",
        guard=ScriptGuard(script.revision, script.name, edit=True, page=page),
    )


def edit_pages(script):
    return max(1, -(-len(script.source.splitlines()) // EDIT_PAGE_LINES))


def lines_screen(script, page, lang="en", whole_editor=False):
    """One button per line; tapping one opens it filled in. Multi-line changes go through a book,
    or through the whole-script field when the server enables it for the optional UI pack."""
    lines = script.source.splitlines()
    pages = edit_pages(script)
    guard = ScriptGuard(script.revision, script.name)
    first = (page - 1) * EDIT_PAGE_LINES
    width = len(str(len(lines)))
    items = []
    if page == 1:
        items.append(f.Button(tr(lang, "+ Add a first line"), f.Show(add_screen(script, 0, page, lang))))
    for index in range(first, min(first + EDIT_PAGE_LINES, len(lines))):
        text = shown(lines[index].strip())
        text = text[:BUTTON_CHARS] + "..." if len(text) > BUTTON_CHARS + 3 else text
        items.append(f.Button(f"{BUTTON_GUTTER}{index + 1:>{width}}§r {text}",
                              f.Show(line_screen(script, index, page, lang))))
    if page == pages:
        items.append(f.Button(tr(lang, "+ Add a line at the end"),
                              f.Show(add_screen(script, len(lines), page, lang))))
    if page > 1:
        items.append(f.Button(tr(lang, "Previous page"), run("edit", script.name, str(page - 1), guard=guard)))
    if page < pages:
        items.append(f.Button(tr(lang, "Next page"), run("edit", script.name, str(page + 1), guard=guard)))
    if whole_editor:
        items.append(f.Button(tr(lang, "Edit all at once (UI pack)..."),
                              f.Show(source_screen(script, page, lang))))
    items += [
        f.Button(tr(lang, "Replace all from held book"), run("import", script.name, guard=guard)),
        f.Button(tr(lang, "Back"), run("menu", script.name, guard=guard)),
    ]
    title = script.name if pages == 1 else f"{script.name} ({page}/{pages})"
    return f.ActionScreen(title, tr(lang, "Tap a line to change or delete it, or to add a line below it."),
                          tuple(items))


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
                   name=script.name, line=line) + "\n\n" + _capped_view(revision.source, lang),
                tr(lang, "Restore"), tr(lang, "Cancel"),
                run("restore", script.name, str(index), guard=guard))
            items.append(f.Button(line, f.Show(confirm)))
        else:
            items.append(f.Header(line))
    items.append(f.Button(tr(lang, "Back"), run("menu", script.name, guard=guard)))
    content = tr(lang, "Earlier revisions, newest first.") if script.history else tr(lang, "no earlier revisions")
    return f.ActionScreen(script.name, content, tuple(items))


def _typed_argument(name, value):
    """``name=value`` as `/botscript run` reads it; text with spaces (positions) is quoted."""
    text = ("true" if value else "false") if isinstance(value, bool) else str(value)
    return f'{name}="{text}"' if not text or any(c.isspace() for c in text) else f"{name}={text}"


def parameter_choices(schema, groups, bots):
    """Dropdown options for each selectable parameter; refuses a form with no target for one."""
    schema = declarations(schema)
    choices = {}
    for name, spec in schema.items():
        kind = spec["type"]
        if kind in {"group", "bot", "choice"}:
            options = (tuple("#" + g.name for g in groups) if kind == "group" else tuple(b["name"] for b in bots)
                       if kind == "bot" else tuple(spec["choices"]))
            if not options:
                raise f.FormResponseError("No targets available for script parameters")
            choices[name] = options
    return schema, choices


def run_screen(script, schema, groups, bots, origin, lang="en", requires=()):
    schema, choices = parameter_choices(schema, groups, bots)
    controls = []
    if requires:
        # The declaration belongs on the form other controllers read before running a shared script.
        controls.append(f.Label(tr(lang, "This script also uses: {names}", names=", ".join(requires))))
    for name, spec in schema.items():
        kind, default = spec["type"], spec.get("default", "")
        label = tr(lang, "{name} ({kind})", name=name, kind=kind)
        if kind in {"group", "bot", "choice"}:
            options = choices[name]
            controls.append(f.Dropdown(label, options, options.index(default) if default in options else 0))
        elif kind == "boolean":
            controls.append(f.Toggle(label, default if type(default) is bool else False))
        else:
            if kind == "pos" and origin is not None and default in {"", "~ ~ ~"}:
                default = " ".join(str(origin[axis]) for axis in ("x", "y", "z"))
            controls.append(f.TextInput(label, default_value=str(default)))
    values, targets = {}, {}
    guard = ScriptGuard(script.revision, script.name, values=values, targets=targets, requires=tuple(requires))

    def build(response):
        values.clear()
        targets.clear()
        # modal_commands keeps one value per control, so the leading label has an entry to skip.
        for (name, spec), value in zip(schema.items(), response[1:] if requires else response):
            if name in choices:
                value = f._option(choices[name], value)
                if spec["type"] == "group":
                    selected = next(group for group in groups if "#" + group.name == value)
                    targets[name] = ("group", value, selected.identity, selected.members)
                elif spec["type"] == "bot":
                    selected = next(bot for bot in bots if bot["name"] == value)
                    targets[name] = ("bot", value, selected["identityId"])
            values[name] = value
        # The run uses `values` from the guard; the `name=value` words make the echoed command one a player
        # could type to do the same.
        return (("run", script.name, *(_typed_argument(name, value) for name, value in values.items())),)

    return f.ModalScreen(script.name, tuple(controls), build, submit=tr(lang, "Run"), command="botscript", guard=guard)


def source_pages(script):
    """Chat pages for `/botscript show`: numbered lines, long lines cut into pieces."""
    lines = []
    for index, line in enumerate(script.source.splitlines()):
        chunks = [line[start : start + 200] for start in range(0, len(line), 200)] or [""]
        lines.extend(f"{index + 1}: {shown(chunk)}" for chunk in chunks)
    return ["\n".join(lines[start : start + 8]) for start in range(0, len(lines), 8)] or [""]
