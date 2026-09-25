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
from endstone_endbot.enrollment import ControllerEnrollment
from endstone_endbot.world import EndstoneWorld

COMMAND_USAGES = [
    # Each usage becomes one native BDS overload, matched before Python runs.
    # Live oracle batteries on the pinned BDS build proved three rules that
    # shape this list (see docs/COMMANDS.md): trailing OPTIONAL params never
    # consume input; string/Id rejects numeric tokens; and params after an
    # enum never match, so only overloads ending at (or without) an enum can
    # consume a tail. The architecture follows from those rules:
    # - enum-free string overloads are the parseability layer. <name> plus a
    #   required <operation: string> covers every 2-token named form, and the
    #   same pair plus a required <arguments: message> covers every longer
    #   form (message consumes anything only with no enum before it).
    # - typed enum overloads exist for completion: they advertise operations,
    #   directions, modes, dimensions, faces and stack natively, but never
    #   match a tailed input on real BDS; the string layer above always
    #   catches those forms for the Python parser instead. Live client smoke
    #   showed generic branches dominate filtering, so completion stays
    #   degraded on this baseline and execution is prioritized.
    # - /bot help <page> cannot use a string (numbers rejected) or an enum
    #   (numeric values are unrepresentable, message-after-enum never
    #   matches), so numeric pages use a required int. This layout is a
    #   compatibility stopgap for pinned Endstone 0.11, not the final command
    #   API; deeper redesign waits on upstream command API work. No private
    #   registry or soft-enum hooks.
    "/bot [command: string] [topic: string]",
    "/bot (ping|list|help)<command: EndbotRoot>",
    "/bot <command: string> <page: int>",
    (
        "/bot (help)<command: EndbotHelpTopic> (status|spawn|resume|reconnect|despawn|forget|rename|tp|move|look|"
        "jump|attack|use|sprint|sneak|hotbar|interact|drop|stop)<topic: EndbotHelpTopicName>"
    ),
    "/bot <name: string> <operation: string>",
    "/bot <name: string> <operation: string> <arguments: message>",
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
        self._controllers = ControllerEnrollment((), None)
        self._world = None
        self._server_thread = None
        self._command_runner = None

    def on_load(self) -> None:
        self.save_default_config()
        authorization = self.config.get("authorization", {})
        self._allowed_players = AllowedPlayers.from_config(authorization)
        self._controllers = ControllerEnrollment.from_config(authorization, self.data_folder)
        for gamertag in self._controllers.pending():
            self.logger.info(f"Controller {gamertag} is pending; it binds on its first Xbox-authenticated join.")

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
        # The server console belongs to the operator who installed Endbot; it
        # gets the control permission directly. Operator players do not.
        self.server.command_sender.add_attachment(self, "endbot.command.control", True)
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
        player_uuid, xuid = str(player.unique_id), str(player.xuid)
        authorized, binding = self._controllers.authorize(player.name, xuid, player_uuid)
        if binding is not None:
            self.logger.info(f"Bound controller {binding.gamertag} to XUID {binding.xuid}.")
        if authorized or self._allowed_players.allows(player_uuid, xuid):
            player.add_attachment(self, "endbot.command.control", True)
        if self._world is not None:
            self._world.apply_pending_placement(player)

    def on_command(self, sender: CommandSender, command: Command, args: list[str]) -> bool:
        if command.name != "bot":
            return False
        if self._command_runner is None or not self._command_runner.submit(args, sender):
            sender.send_message("Endbot: command service is unavailable")
        return True
