"""Thin Endstone adapter for Endbot commands."""

from pathlib import Path
from typing import ClassVar

from endstone.command import Command, CommandSender
from endstone.event import PlayerJoinEvent, event_handler
from endstone.plugin import Plugin

from endstone_endbot.authorization import AllowedPlayers
from endstone_endbot.commands import BotCommandService
from endstone_endbot.control import RuntimeControlClient
from endstone_endbot.dispatch import AsyncCommandRunner, ServerThreadBridge, ServerThreadWorld
from endstone_endbot.world import EndstoneWorld

COMMAND_USAGES = [
    "/bot",
    "/bot ping",
    "/bot help [topic: string]",
    "/bot list",
    "/bot <name: string> status",
    "/bot <name: string> spawn [placement: message]",
    "/bot <name: string> resume",
    "/bot <name: string> reconnect",
    "/bot <name: string> despawn",
    "/bot <name: string> forget",
    "/bot <name: string> rename <new_name: string>",
    "/bot <name: string> tp <destination: message>",
    "/bot <name: string> move <direction: string>",
    "/bot <name: string> look <rotation: message>",
    "/bot <name: string> jump [mode: message]",
    "/bot <name: string> attack [mode: message]",
    "/bot <name: string> use [mode: message]",
    "/bot <name: string> sprint <state: string>",
    "/bot <name: string> sneak <state: string>",
    "/bot <name: string> stop",
]


class EndbotPlugin(Plugin):
    api_version = "0.11"
    description = "Server-owner control plane for Endbot fake players."
    authors: ClassVar[list[str]] = ["amatouhake and Endbot contributors"]
    website = "https://github.com/amatouhake/endbot"

    commands: ClassVar[dict[str, dict[str, object]]] = {
        "bot": {
            "description": "Control Endbot fake players.",
            "usages": COMMAND_USAGES,
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
        self._world = None
        self._server_thread = None
        self._command_runner = None

    def on_load(self) -> None:
        self.save_default_config()
        self._allowed_players = AllowedPlayers.from_config(self.config.get("authorization", {}))

    def on_enable(self) -> None:
        runtime = self.config.get("runtime", {})
        token_file = Path(str(runtime.get("token-file", "control.token")))
        if not token_file.is_absolute():
            token_file = self.data_folder / token_file
        control = RuntimeControlClient(
            host=str(runtime.get("host", "127.0.0.1")),
            port=int(runtime.get("port", 19142)),
            token_file=token_file,
            timeout=float(runtime.get("timeout-seconds", 10.0)),
        )
        self._world = EndstoneWorld(self.server)
        self._server_thread = ServerThreadBridge(lambda task: self.server.scheduler.run_task(self, task))
        command_world = ServerThreadWorld(self._world, self._server_thread)
        self._bot_commands = BotCommandService(control, command_world)
        self._command_runner = AsyncCommandRunner(self._bot_commands, self._server_thread)
        self.register_events(self)

    def on_disable(self) -> None:
        if self._command_runner is not None:
            self._command_runner.close()
            self._command_runner = None
        if self._server_thread is not None:
            self._server_thread.close()
            self._server_thread = None

    @event_handler
    def on_player_join(self, event: PlayerJoinEvent) -> None:
        player = event.player
        if self._allowed_players.allows(str(player.unique_id), str(player.xuid)):
            player.add_attachment(self, "endbot.command.control", True)
        if self._world is not None:
            self._world.apply_pending_placement(player)

    def on_command(self, sender: CommandSender, command: Command, args: list[str]) -> bool:
        if command.name != "bot":
            return False
        if self._command_runner is None or not self._command_runner.submit(args, sender):
            sender.send_message("Endbot: command service is unavailable")
        return True
