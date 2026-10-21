"""Group screens share command execution and result data with typed commands."""

from dataclasses import dataclass

from endstone_endbot import forms as f
from endstone_endbot.group_results import PAGE_SIZE, GroupResult
from endstone_endbot.i18n import tr


@dataclass(frozen=True)
class GroupGuard:
    identity: str
    name: str
    members: tuple[str, ...] | None = None
    result_id: str | None = None
    bot_identity: str | None = None
    bot_name: str | None = None
    candidates: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class GroupListView:
    groups: tuple


@dataclass(frozen=True)
class GroupView:
    identity: str
    name: str
    members: tuple[dict, ...]
    available: tuple[dict, ...]
    page: int = 1


@dataclass(frozen=True)
class ResultView:
    result: GroupResult
    history: int
    records: tuple[GroupResult, ...]


def run(*arguments, guard=None):
    return f.Run((tuple(arguments),), command="botgroup", guard=guard)


def group_list_screen(view, lang="en"):
    items = [f.Button(f"#{group.name} ({len(group.members)})", run("menu", f"#{group.name}",
                      guard=GroupGuard(group.identity, group.name))) for group in view.groups]
    items.extend((f.Button(tr(lang, "+ New group..."), f.Show(create_modal(lang))),
                  f.Button(tr(lang, "Back to Bot list"), f.run(f.menu_argv()))))
    return f.ActionScreen(tr(lang, "Endbot: groups"), tr(lang, "Pick a group."), tuple(items))


def create_modal(lang="en"):
    def build(values):
        reference = f._text(values[0])
        return (("create", reference),)
    return f.ModalScreen(tr(lang, "Create a group"), (f.TextInput(tr(lang, "Group reference (#name)"), "#miners"),),
                         build, tr(lang, "Create"), command="botgroup")


def group_screen(view, lang="en"):
    name = f"#{view.name}"
    guard = GroupGuard(view.identity, view.name, tuple(row["identityId"] for row in view.members),
                       candidates=tuple((row["name"], row["identityId"]) for row in view.available))
    back = f.Button(tr(lang, "Back to group"), f.run((name, "menu")))
    operations = f.ActionScreen(name, tr(lang, "Operations apply to every current member. Limits apply per Bot."), (
        f.Button(tr(lang, "Stop everything"), f.run((name, "stop"))),
        f.Button(tr(lang, "Move..."), f.Show(f.move_modal(name, lang))),
        f.Button(tr(lang, "Jump..."), f.Show(f.action_screen(name, "jump", lang))),
        f.Button(tr(lang, "Sprint / sneak..."), f.Show(f.flags_modal(name, lang))),
        f.Button(tr(lang, "Look..."), f.Show(f.look_modal(name, None, lang))),
        f.Button(tr(lang, "Attack..."), f.Show(f.action_screen(name, "attack", lang))),
        f.Button(tr(lang, "Use item..."), f.Show(f.action_screen(name, "use", lang))),
        f.Button(tr(lang, "Destroy blocks..."), f.Show(f.destroy_screen(name, lang))),
        f.Button(tr(lang, "Hotbar slot..."), f.Show(f.hotbar_modal(name, lang=lang))),
        f.Button(tr(lang, "Drop item..."), f.Show(f.drop_modal(name, lang))), back,
    ))
    pages = max(1, (len(view.members) + PAGE_SIZE - 1) // PAGE_SIZE)
    items = [f.Button(tr(lang, "Group operations..."), f.Show(operations)),
             f.Button(tr(lang, "Recent operation result..."), run("menu", name, "result"))]
    for row in view.members[(view.page - 1) * PAGE_SIZE:view.page * PAGE_SIZE]:
        label = f"{row['index']}: {row['name']} [{tr(lang, row['connectionState'])}]"
        if row["connectionState"] == "missing":
            items.append(f.Header(label))
        else:
            items.append(f.Button(label, f.run((f"{name}[{row['index']}]", "menu"))))
    if view.page > 1:
        items.append(f.Button(tr(lang, "Previous page"), run("menu", name, str(view.page - 1))))
    if view.page < pages:
        items.append(f.Button(tr(lang, "Next page"), run("menu", name, str(view.page + 1))))
    if view.available:
        choices = tuple(row["name"] for row in view.available)
        add = f.ModalScreen(tr(lang, "Add a member to {name}", name=name), (f.Dropdown(tr(lang, "Bot"), choices),),
                            lambda values, choices=choices: (("add", name, f._option(choices, values[0])),),
                            tr(lang, "Add"), command="botgroup")
        items.append(f.Button(tr(lang, "Add member..."), f.Show(add)))
    if view.members:
        choices = tuple(f"{row['index']}: {row['name']}" for row in view.members)
        remove = f.ModalScreen(tr(lang, "Remove a member from {name}", name=name),
                               (f.Dropdown(tr(lang, "Member"), choices),),
                               lambda values: (("remove", name,
                                                f"{name}[{choices.index(f._option(choices, values[0])) + 1}]"),),
                               tr(lang, "Remove"), command="botgroup")
        items.append(f.Button(tr(lang, "Remove member..."), f.Show(remove)))
    # Use the same ActionForm presentation as group browsing: this client's
    # MessageForm leaves the title/content blank. Keep the target in the button too.
    delete = f.ActionScreen(tr(lang, "Delete {name}", name=name),
                            tr(lang, "Delete {name}? Its Bots stay registered.", name=name), (
                                f.Button(tr(lang, "Delete {name}", name=name), run("delete", name)),
                                f.Button(tr(lang, "Cancel"), run("menu", name)),
                            ))
    items.extend((f.Button(tr(lang, "Delete group..."), f.Show(delete)),
                  f.Button(tr(lang, "Back to groups"), run("menu"))))
    content = tr(lang, "Members {count} - page {page}/{pages}. Open a member for teleport and individual operations.",
                 count=len(view.members), page=view.page, pages=pages)
    return f.ActionScreen(name, content, tuple(items), guard=guard)


def result_screen(view, lang="en"):
    result, history = view.result, view.history
    name = f"#{result.group_name}"
    items = []
    for offset, label in ((-1, "Newer result"), (1, "Older result")):
        index = history - 1 + offset
        if 0 <= index < len(view.records):
            target = view.records[index]
            guard = GroupGuard(result.group_id, result.group_name, result_id=target.identity)
            items.append(f.Button(tr(lang, label), run("menu", name, "result", str(index + 1), guard=guard)))
    items.append(f.Button(tr(lang, "Back to group"), run("menu", name,
                          guard=GroupGuard(result.group_id, result.group_name))))
    items.append(f.Button(tr(lang, "Back to groups"), run("menu")))
    return f.ActionScreen(tr(lang, "Recent operation result..."),
                          "\n".join(result.lines(history, len(view.records), lang)), tuple(items),
                          guard=GroupGuard(result.group_id, result.group_name, result_id=result.identity))
