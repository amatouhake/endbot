"""Opt-in command-first Group smoke for the isolated raknet_spike server."""

from e2e_consumer import ConsumerError


def exercise_groups(bot, console, request, wait, log, report):
    buddy = "GroupBuddy"

    def command(line, *expected):
        offset = len(log())
        console(line)
        wait(line, lambda: all(text in log()[offset:] for text in expected), 15)
        return log()[offset:]

    command(f"bot {buddy} spawn", f"{buddy}: created and connecting")
    wait("GroupBuddy online", lambda: request("status", name=buddy)["connectionState"] == "online", 60)
    command("botgroup help", "/botgroup list [#group]")
    command("botgroup create #miners", "#miners: group created")
    command(f"botgroup add #miners {bot}", f"#miners: add: {bot}")
    command(f"botgroup add #miners {buddy}", f"#miners: add: {buddy}")
    command("botgroup list", "#miners (2)")
    command("botgroup list #miners", f"#miners[1]: {bot}", f"#miners[2]: {buddy}")
    command("botgroup list miners", "Group syntax: #group")
    command("botgroup members #miners", "Invalid group command")
    command("botgroup remove #miners #miners[2]", "#miners: remove: #miners[2]")
    command(f"botgroup add #miners {buddy}", f"#miners: add: {buddy}")
    command("botgroup menu", "menu is available only to players")
    command("botgroup result #miners", "No recent group result")
    command("bot #miners stop", "#miners: console: stop: accepted 2, failed 0, not run 0")
    command("bot #miners jump", "#miners: console: jump: accepted 2, failed 0, not run 0")
    command("botgroup result #miners 1", "#miners[1]", "#miners[2]", "jump once requested")
    command("bot #miners[1] look 45 0", f"#miners[1]: console {bot}: look 45 0: accepted 1")
    command("bot #miners[2] look 90 0", f"#miners[2]: console {buddy}: look 90 0: accepted 1")
    command("bot #miners move forward for 1", "move forward for 1: accepted 2, failed 0")
    command("bot #miners[0] stop", "Group target syntax")
    command(f"bot {buddy} despawn", f"{buddy}: despawn requested")
    output = command("bot #miners jump", "jump: accepted 1, failed 1, not run 0", "Reasons: offline 1")
    if sum("Endbot: #miners:" in line for line in output.splitlines()) != 2:
        raise ConsumerError("Group partial failure was not limited to two summary log lines")
    command("botgroup result #miners", f"#miners[1] {bot}: accepted", f"#miners[2] {buddy}: failed")
    command("botgroup delete #miners", "group deleted; Bots retained")
    names = {profile["name"] for profile in request("list")}
    if not {bot, buddy} <= names:
        raise ConsumerError("Deleting a group removed a Bot profile")
    report["groups"] = {
        "console_native_parser": True,
        "prefixed_management_and_scoped_list": True,
        "one_based_selection": True,
        "bulk_and_tailed_commands": True,
        "offline_member_partial_failure": True,
        "delete_preserves_bots": True,
        "bounded_summary_and_result_command": True,
        "human_client_forms_completion_and_revocation": "not validated by this console smoke",
    }
