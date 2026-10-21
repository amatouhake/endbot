"""Opt-in command-first Group smoke for the isolated raknet_spike server."""

from e2e_consumer import ConsumerError


def exercise_groups(bot, console, request, wait, log, report):
    buddy = "GroupBuddy"

    def command(line, *expected):
        offset = len(log())
        console(line)
        wait(line, lambda: all(text in log()[offset:] for text in expected), 15)

    command(f"bot {buddy} spawn", f"{buddy}: created and connecting")
    wait("GroupBuddy online", lambda: request("status", name=buddy)["connectionState"] == "online", 60)
    command("botgroup create miners", "#miners: group created")
    command(f"botgroup add miners {bot}", f"#miners: add: {bot}")
    command(f"botgroup add miners {buddy}", f"#miners: add: {buddy}")
    command("botgroup members miners", f"#miners[1]: {bot}", f"#miners[2]: {buddy}")
    command("bot #miners stop", f"{bot}: movement and actions stopped", f"{buddy}: movement and actions stopped")
    command("bot #miners jump", f"{bot}: jump once requested", f"{buddy}: jump once requested")
    command("bot #miners[1] look 45 0", f"{bot}: look set to yaw=45.0 pitch=0.0")
    command("bot #miners[2] look 90 0", f"{buddy}: look set to yaw=90.0 pitch=0.0")
    command("bot #miners move forward for 1", f"{bot}: movement set to forward", f"{buddy}: movement set to forward")
    command("bot #miners[0] stop", "Group target syntax")
    command(f"bot {buddy} despawn", f"{buddy}: despawn requested")
    command("bot #miners jump", f"{bot}: jump once requested", "#miners[2]: Bot GroupBuddy is not online")
    command("botgroup delete miners", "group deleted; Bots retained")
    names = {profile["name"] for profile in request("list")}
    if not {bot, buddy} <= names:
        raise ConsumerError("Deleting a group removed a Bot profile")
    report["groups"] = {
        "console_native_parser": True,
        "one_based_selection": True,
        "bulk_and_tailed_commands": True,
        "offline_member_partial_failure": True,
        "delete_preserves_bots": True,
        "human_client_forms_completion_and_revocation": "not validated by this console smoke",
    }
