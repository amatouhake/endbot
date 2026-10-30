"""Build in-game menu views from Operation outcomes.

The command service answers ``menu`` like any other operation: with ``data``
(``data["menu"]`` names what it describes). Screens, navigation and form
guards belong to this in-game UI layer, so other surfaces such as the web can
present the same outcome their own way.
"""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

from endbot_core import script_forms
from endbot_core.forms import BotListView, BotView, GroupTarget, Run, ScriptMenuView, menu_argv
from endbot_core.group_forms import GroupGuard
from endbot_core.script_store import Revision, Script


def _script(data: dict) -> Script:
    """The script a screen builder takes, rebuilt from its plain outcome fields."""
    return Script(
        name=data["name"],
        source=data["source"],
        revision=data["revision"],
        owner=data.get("owner"),
        owner_name=data.get("owner_name"),
        visibility=data.get("visibility", "shared"),
        history=tuple(Revision(row["revision"], row["source"], row["saved_at"]) for row in data.get("history", ())),
        saved_at=data.get("saved_at", 0),
    )


def _list(data: dict, lang: str):
    scripts = tuple(
        SimpleNamespace(name=row["name"], revision=row["revision"], owner_name=row["owner_name"],
                        visibility=row["visibility"], file=row["file"], own=row["own"])
        for row in data.get("scripts", ())
    )
    return script_forms.list_screen(scripts, lang, own=lambda script: script.own, author=data.get("author", True))


def _screen(data: dict, lang: str):
    return script_forms.script_screen(_script(data["script"]), lang, data["editable"], data["book_export"],
                                      author=data.get("author", True))


def _lines(data: dict, lang: str):
    return script_forms.lines_screen(_script(data["script"]), data["page"], lang, data["whole_editor"])


def _import(data: dict, lang: str):
    return script_forms.import_confirm_screen(_script(data["script"]), data["text"], lang)


def _export(data: dict, lang: str):
    return script_forms.export_confirm_screen(_script(data["script"]), data["pages"], data["text"], lang)


def _history(data: dict, lang: str):
    return script_forms.history_screen(_script(data["script"]), data["editable"], lang)


def _parameters(data: dict, lang: str):
    groups = tuple(SimpleNamespace(name=row["name"], identity=row["identity"], members=tuple(row["members"]))
                   for row in data.get("groups", ()))
    return script_forms.run_screen(_script(data["script"]), data["schema"], groups, data["bots"],
                                   data.get("origin"), lang, requires=tuple(data.get("requires", ())))


# One table drives both the crossing decision (menu_view) and the screen builder (script_view), so a
# new /botscript menu cannot be added to one and forgotten in the other.
SCRIPT_MENUS = {
    "scripts": _list,
    "script": _screen,
    "scriptLines": _lines,
    "scriptImport": _import,
    "scriptExport": _export,
    "scriptHistory": _history,
    "scriptParameters": _parameters,
}


def script_view(data: dict):
    """The /botscript screen a menu outcome describes, or None for another outcome.

    Called where forms are rendered (the plugin), not in endbotd: a script screen carries form
    builders, which are functions and cannot cross the endbotd link.
    """
    menu = data.get("menu")
    build = SCRIPT_MENUS.get(menu) if isinstance(menu, str) else None
    return None if build is None else build(data, data.get("lang", "en"))


def group_view_back(view: BotView, group: dict, guard: GroupGuard) -> BotView:
    """Keep menu origin separate from the UUID/membership checks on actions."""
    back_guard = GroupGuard(group["identity"], group["name"])
    manage = Run((("menu", f"#{group['name']}"),), command="botgroup", guard=back_guard)
    management = guard.return_to_management
    if management:
        back = manage
    elif guard.bot_identity is not None:
        back = Run((menu_argv(f"#{group['name']}"),), guard=back_guard)
    else:
        back = Run((menu_argv(),))
    label = "Back to group management" if management else (
        "Back to group operations" if guard.bot_identity is not None else "Back to Bot list")
    return replace(view, guard=guard, back=back, back_label=label,
                   management=manage if view.name.startswith("#") and not management else None)


def menu_view(result, principal, scripting: bool = False):
    """The view a menu outcome opens for ``principal``, or None for any other outcome."""
    data = result.data if isinstance(result.data, dict) else {}
    menu = data.get("menu")
    if menu in SCRIPT_MENUS:
        # Script screens are built where forms are rendered; see script_view.
        return ScriptMenuView(data)
    incoming = getattr(principal, "group_guard", None)
    returning = bool(getattr(incoming, "return_to_management", False))
    if menu == "bots":
        groups = tuple(GroupTarget(group["name"], group["count"], GroupGuard(group["identity"], group["name"]))
                       for group in data.get("groups", ()))
        return BotListView(tuple(data["bots"]), groups, scripting)
    if menu == "bot":
        view = BotView(data["name"], data["summary"], data["status"], data.get("observed"))
        group, member = data.get("group"), data.get("member")
        if group is None:
            return view
        if member is None:
            # Reopened from a Group member's menu: keep the guard the form carried.
            return group_view_back(view, group, incoming)
        guard = GroupGuard(group["identity"], group["name"], tuple(group["members"]),
                           bot_identity=member["identityId"], bot_name=member["name"],
                           return_to_management=returning)
        return group_view_back(view, group, guard)
    if menu == "groupOperations":
        group = data["group"]
        guard = GroupGuard(group["identity"], group["name"], tuple(group["members"]), operation_menu=True,
                           return_to_management=returning)
        return group_view_back(BotView(f"#{group['name']}", data["summary"], {}), group, guard)
    return None
