"""Build in-game menu views from Operation outcomes.

The command service answers ``menu`` like any other operation: with ``data``
(``data["menu"]`` names what it describes). Screens, navigation and form
guards belong to this in-game UI layer, so other surfaces such as the web can
present the same outcome their own way.
"""

from __future__ import annotations

from dataclasses import replace

from endstone_endbot.forms import BotListView, BotView, GroupTarget, Run, menu_argv
from endstone_endbot.group_forms import GroupGuard


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
    incoming = getattr(principal, "group_guard", None)
    returning = bool(incoming and incoming.return_to_management)
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
