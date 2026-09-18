"""Thin Endstone adapter for Endbot commands."""

from typing import ClassVar

from endstone.command import Command, CommandSender
from endstone.event import PlayerJoinEvent, event_handler
from endstone.plugin import Plugin

from endstone_endbot.authorization import AllowedPlayers
from endstone_endbot.commands import BotCommandService


class EndbotPlugin(Plugin):
    api_version = "0.11"
    description = "Server-owner control plane for Endbot fake players."
    authors: ClassVar[list[str]] = ["amatouhake and Endbot contributors"]
    website = "https://github.com/amatouhake/endbot"

    commands: ClassVar[dict[str, dict[str, object]]] = {
        "bot": {
            "description": "Control Endbot fake players.",
            "usages": ["/bot ping"],
            "permissions": ["endbot.command.control"],
        }
    }
    permissions: ClassVar[dict[str, dict[str, object]]] = {
        "endbot.command.control": {
            "description": "Allow use of Endbot control commands.",
            "default": False,
        }
    }

    def __init__(self) -> None:
        super().__init__()
        self._bot_commands = BotCommandService()
        self._allowed_players = AllowedPlayers()

    def on_load(self) -> None:
        self.save_default_config()
        self._allowed_players = AllowedPlayers.from_config(self.config.get("authorization", {}))

    def on_enable(self) -> None:
        self.register_events(self)

    @event_handler
    def on_player_join(self, event: PlayerJoinEvent) -> None:
        player = event.player
        if self._allowed_players.allows(str(player.unique_id), str(player.xuid)):
            player.add_attachment(self, "endbot.command.control", True)

    def on_command(self, sender: CommandSender, command: Command, args: list[str]) -> bool:
        if command.name != "bot":
            return False
        result = self._bot_commands.execute(args)
        if result.message is not None:
            sender.send_message(result.message)
        return result.handled
