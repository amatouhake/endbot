"""Thin Endstone adapter for Endbot commands."""

from pathlib import Path
from typing import ClassVar
from uuid import UUID

from endstone.command import Command, CommandSender
from endstone.event import PlayerJoinEvent, event_handler
from endstone.plugin import Plugin

from endstone_endbot.authorization import AllowedPlayers
from endstone_endbot.commands import BotCommandService
from endstone_endbot.control import RuntimeControlClient
from endstone_endbot.dispatch import AsyncCommandRunner, CommandSource, ServerThreadBridge, ServerThreadWorld
from endstone_endbot.world import EndstoneWorld

COMMAND_USAGES = [
    # Bedrock matches each usage as one native overload before Python ever runs,
    # so a documented form with no matching overload is a client-visible syntax
    # error. The two string fallbacks are the parseability guarantee: the root
    # fallback covers /bot, /bot help <page|command> and unknown roots, while the
    # named fallback covers every /bot <name> ... form (Bedrock passes the
    # remainder as one message argument and the Python parser re-splits it).
    # Both fallbacks were validated against the real Bedrock parser, while a
    # typed-only named tree was rejected live even for paper-perfect overloads
    # (notably "/bot Alice move forward" and "/bot Alice tp me"). The typed
    # enum overloads below exist only for native autocomplete and must never
    # remove a form the fallbacks accept: parseability wins over completion.
    # No private registry or soft-enum hooks are used.
    "/bot [command: string] [topic: string]",
    "/bot (ping|list|help)<command: EndbotRoot>",
    "/bot <name: string> <operation: message>",
    "/bot <name: string> (status|resume|reconnect|despawn|forget|stop)<operation: EndbotNoArgs>",
    "/bot <name: string> (spawn)<operation: EndbotSpawn>",
    (
        "/bot <name: string> (spawn)<operation: EndbotSpawnAt> (at)<mode: EndbotSpawnAtMode> "
        "<placement: message>"
    ),
    (
        "/bot <name: string> (spawn)<operation: EndbotSpawnDimension> (at)<mode: EndbotAt> "
        "<position: pos> (in)<scope: EndbotSpawnDimensionScope> "
        "(overworld|nether|end)<dimension: EndbotSpawnDimensionName>"
    ),
    "/bot <name: string> (rename)<operation: EndbotRename> <new_name: string>",
    "/bot <name: string> (tp)<operation: EndbotTeleport> <destination: message>",
    (
        "/bot <name: string> (tp)<operation: EndbotTeleportDimension> "
        "(in)<scope: EndbotTeleportDimensionScope> "
        "(overworld|nether|end)<dimension: EndbotTeleportDimensionName> "
        "<destination: message>"
    ),
    (
        "/bot <name: string> (move)<operation: EndbotMove> "
        "(forward|backward|left|right|stop)<direction: EndbotDirection>"
    ),
    "/bot <name: string> (look)<operation: EndbotLook> <yaw: float> <pitch: float>",
    "/bot <name: string> (look)<operation: EndbotLookAt> (at)<mode: EndbotLookMode> <target: pos>",
    "/bot <name: string> (jump|attack|use)<action: EndbotDefaultAction>",
    (
        "/bot <name: string> (jump|attack|use)<action: EndbotAction> "
        "(once|continuous|stop)<mode: EndbotActionMode>"
    ),
    (
        "/bot <name: string> (jump|attack|use)<action: EndbotIntervalAction> "
        "(interval)<mode: EndbotIntervalMode> <ticks: int>"
    ),
    "/bot <name: string> (sprint|sneak)<flag: EndbotFlag> (on|off)<state: EndbotOnOff>",
    "/bot <name: string> (hotbar)<operation: EndbotHotbarQuery>",
    "/bot <name: string> (hotbar)<operation: EndbotHotbarSet> <slot: int>",
    (
        "/bot <name: string> (interact)<operation: EndbotInteract> <block: block_pos> "
        "(down|up|north|south|west|east)<face: EndbotBlockFace>"
    ),
    "/bot <name: string> (drop)<operation: EndbotDropOne>",
    "/bot <name: string> (drop)<operation: EndbotDropStack> (stack)<mode: EndbotDropMode>",
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

        def resolve_sender(source: CommandSource):
            if source.player_id is not None:
                return self.server.get_player(UUID(source.player_id))
            return source.sender

        command_world = ServerThreadWorld(self._world, self._server_thread, resolve_sender)
        self._bot_commands = BotCommandService(control, command_world)
        self._command_runner = AsyncCommandRunner(self._bot_commands, self._server_thread, resolve_sender)
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
