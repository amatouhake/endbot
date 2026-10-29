"""Thin Endstone adapter for Endbot commands: the world end of the endbotd link (docs/ENDBOTD.md P2).

The plugin registers the commands, reports presence, forwards command lines
and form replies to endbotd, answers its world calls on the server thread,
renders forms and delivers chat. It holds no policy and no store; while
endbotd is down every Endbot command is refused and the last control
permissions stay as they were.
"""

from pathlib import Path
from typing import ClassVar
from uuid import UUID

from endbot_core.i18n import DEFAULT_LANGUAGE_SETTING, language_setting, resolve_language, tr
from endbot_core.link import LinkError
from endbot_core.principal import Principal
from endbot_core.wire_views import encode_view
from endstone.command import Command, CommandSender, ConsoleCommandSender
from endstone.event import PlayerJoinEvent, PlayerQuitEvent, event_handler
from endstone.plugin import Plugin

from endstone_endbot import __version__
from endstone_endbot.dispatch import ServerThreadBridge
from endstone_endbot.form_adapter import FormPresenter
from endstone_endbot.inventory_sync import PACKET_ID as INVENTORY_PACKET_ID
from endstone_endbot.inventory_sync import HotbarPublisher, sync_settings, unsupported_payload
from endstone_endbot.link_adapter import PluginLink
from endstone_endbot.world import EndstoneWorld, unsupported

ROOTS = frozenset({"bot", "botgroup", "botscript", "botcontrollers"})
# Below endbotd's world-call deadline, so a stuck tick fails one call (docs/ENDBOTD.md P2).
CALL_DEADLINE_SECONDS = 5.0

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
    "/bot (ping|list|help|menu)<command: EndbotRoot>",
    "/bot <command: string> <page: int>",
    (
        "/bot (help)<command: EndbotHelpTopic> (status|spawn|resume|reconnect|despawn|forget|rename|tp|move|look|"
        "jump|attack|use|sprint|sneak|inventory|hotbar|swap|hold|interact|destroy|drop|radius|stop|menu)"
        "<topic: EndbotHelpTopicName>"
    ),
    "/bot <name: string> <operation: string>",
    "/bot <name: string> <operation: string> <arguments: message>",
    # One single-value enum per no-argument operation: the Windows client shows
    # a multi-value enum here only as its type name (<operation: EndbotNoArgs>),
    # which hid status/resume/reconnect/despawn/forget/stop from suggestions.
    "/bot <name: string> (status)<operation: EndbotStatus>",
    "/bot <name: string> (inventory)<operation: EndbotInventory>",
    "/bot <name: string> (entities)<operation: EndbotEntities>",
    "/bot <name: string> (dismount)<operation: EndbotDismount>",
    "/bot <name: string> (target)<operation: EndbotTarget>",
    "/bot <name: string> (entity)<operation: EndbotEntity>",
    "/bot <name: string> (block)<operation: EndbotBlockInfo> <position: pos>",
    "/bot <name: string> (blocks)<operation: EndbotBlocks> <type: string>",
    "/bot <name: string> (resume)<operation: EndbotResume>",
    "/bot <name: string> (reconnect)<operation: EndbotReconnect>",
    "/bot <name: string> (despawn)<operation: EndbotDespawn>",
    "/bot <name: string> (forget)<operation: EndbotForget>",
    "/bot <name: string> (stop)<operation: EndbotStop>",
    "/bot <name: string> (menu)<operation: EndbotMenu>",
    "/bot <name: string> (spawn)<operation: EndbotSpawn>",
    ("/bot <name: string> (spawn)<operation: EndbotSpawnAt> (at)<mode: EndbotSpawnAtMode> <placement: message>"),
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
    "/bot <name: string> (use)<action: EndbotTimedUse> (for)<mode: EndbotUseDuration> <ticks: int>",
    ("/bot <name: string> (jump|attack|use)<action: EndbotAction> (once|continuous|stop)<mode: EndbotActionMode>"),
    (
        "/bot <name: string> (jump|attack|use)<action: EndbotIntervalAction> "
        "(interval)<mode: EndbotIntervalMode> <ticks: int>"
    ),
    "/bot <name: string> (sprint|sneak)<flag: EndbotFlag> (on|off)<state: EndbotOnOff>",
    "/bot <name: string> (hotbar)<operation: EndbotHotbarQuery>",
    "/bot <name: string> (hotbar)<operation: EndbotHotbarSet> <slot: int>",
    "/bot <name: string> (swap)<operation: EndbotSwap> <a: int> <b: int>",
    "/bot <name: string> (hold)<operation: EndbotHold> <item: string>",
    "/bot <name: string> (destroy)<operation: EndbotDestroy>",
    "/bot <name: string> (destroy)<operation: EndbotDestroyMode> (once|continuous|stop)<mode: EndbotDestroyModeName>",
    "/bot <name: string> (destroy)<operation: EndbotDestroyPosition> <block: block_pos>",
    (
        "/bot <name: string> (destroy)<operation: EndbotDestroyFace> <block: block_pos> "
        "(down|up|north|south|west|east)<face: EndbotDestroyBlockFace>"
    ),
    "/bot <name: string> (radius)<operation: EndbotRadiusQuery>",
    "/bot <name: string> (radius)<operation: EndbotRadiusSet> <chunks: int>",
    "/bot <name: string> (radius)<operation: EndbotRadiusReset> (default)<mode: EndbotRadiusDefault>",
    (
        "/bot <name: string> (interact)<operation: EndbotInteract> <block: block_pos> "
        "(down|up|north|south|west|east)<face: EndbotBlockFace>"
    ),
    "/bot <name: string> (drop)<operation: EndbotDropOne>",
    # These are hints ending at enums; the enum-free message overload above
    # carries numeric slots and item/count tails under the pinned BDS rules.
    "/bot <name: string> (drop)<operation: EndbotDropStack> (stack)<mode: EndbotDropMode>",
    "/bot <name: string> (drop)<operation: EndbotDropAll> (all)<mode: EndbotDropAllMode>",
    "/bot <name: string> (drop)<operation: EndbotDropOffhand> (offhand)<slot: EndbotDropOffhandSlot>",
    "/bot <name: string> (drop)<operation: EndbotDropHead> (head)<slot: EndbotDropHeadSlot>",
    "/bot <name: string> (drop)<operation: EndbotDropChest> (chest)<slot: EndbotDropChestSlot>",
    "/bot <name: string> (drop)<operation: EndbotDropLegs> (legs)<slot: EndbotDropLegsSlot>",
    "/bot <name: string> (drop)<operation: EndbotDropFeet> (feet)<slot: EndbotDropFeetSlot>",
    # Group targets. Bedrock Id/string rejects a leading '#', so only a
    # first-position message carries "#group[1] <operation> ...". It natively
    # matches any input, which leaves syntax rejection to the Python parser.
    # Kept last so the typed overloads above lead native completion; that
    # ordering effect still needs real-client validation.
    "/bot <arguments: message>",
]

SCRIPT_USAGES = [
    "/botscript", "/botscript <action: string>", "/botscript <action: string> <arguments: message>",
    "/botscript (list)<action: EndbotScriptList>",
    "/botscript (help)<action: EndbotScriptHelp>",
    "/botscript (menu)<action: EndbotScriptMenu>",
    "/botscript (runs)<action: EndbotScriptRuns>",
    "/botscript (show)<action: EndbotScriptShow> <name: string> [page: int]",
    "/botscript (run)<action: EndbotScriptRun> <name: string> [arguments: message]",
    "/botscript (stop)<action: EndbotScriptStop> <run: string>",
    "/botscript (delete)<action: EndbotScriptDelete> <name: string>",
    "/botscript (copy)<action: EndbotScriptCopy> <name: string> <copy: string>",
    "/botscript (share|private)<action: EndbotScriptVisibility> <name: string>",
    "/botscript (history)<action: EndbotScriptHistory> <name: string>",
    "/botscript (restore)<action: EndbotScriptRestore> <name: string> <revision: int>",
    "/botscript (rename)<action: EndbotScriptRename> <name: string> <new: string>",
    "/botscript (import)<action: EndbotScriptImport> <name: string>",
    "/botscript (export)<action: EndbotScriptExport> <name: string>",
    "/botscript (export)<action: EndbotScriptExportTo> <name: string> (overwrite)<mode: EndbotScriptOverwrite>",
    "/botscript (reload)<action: EndbotScriptReload>",
]


class EndbotPlugin(Plugin):
    api_version = "0.11"
    description = "Server-owner control plane for Endbot fake players."
    authors: ClassVar[list[str]] = ["amatouhake and Endbot contributors"]
    website = "https://github.com/amatouhake/endbot"

    commands: ClassVar[dict[str, dict[str, object]]] = {
        "botscript": {
            "description": "Write and run controller Luau scripts.", "usages": SCRIPT_USAGES,
            "permissions": ["endbot.command.control"],
        },
        "bot": {
            "description": "Control Endbot fake players.",
            "usages": COMMAND_USAGES,
            "permissions": ["endbot.command.control"],
        },
        "botcontrollers": {
            "description": "Manage Bot controllers from the server console.",
            "usages": [
                "/botcontrollers",
                "/botcontrollers <action: string>",
                "/botcontrollers <action: string> <target: message>",
            ],
            "permissions": ["endbot.command.controllers"],
        },
        "botgroup": {
            "description": "Manage shared Endbot groups.",
            "usages": [
                "/botgroup",
                "/botgroup <action: string>",
                "/botgroup <action: string> <arguments: message>",
                # Keep the generic parseability layer above, then advertise each
                # operation separately, like /bot. A multi-value enum is shown
                # as a type name by the locked Windows client.
                "/botgroup (list)<action: EndbotGroupList> [group: message]",
                "/botgroup (help)<action: EndbotGroupHelp>",
                "/botgroup (menu)<action: EndbotGroupMenu> [arguments: message]",
                "/botgroup (result)<action: EndbotGroupResult> <group: string> [history: int]",
                "/botgroup (create)<action: EndbotGroupCreate> <group: message>",
                "/botgroup (delete)<action: EndbotGroupDelete> <group: message>",
                "/botgroup (add)<action: EndbotGroupAdd> <group: string> <bot: string>",
                "/botgroup (remove)<action: EndbotGroupRemove> <group: string> <member: message>",
            ],
            "permissions": ["endbot.command.control"],
        },
    }
    permissions: ClassVar[dict[str, dict[str, object]]] = {
        "endbot.command.control": {
            "description": "Allow use of Endbot control commands.",
            "default": False,
        },
        "endbot.command.controllers": {"description": "Console-only Bot controller management.", "default": "console"},
    }

    def __init__(self) -> None:
        super().__init__()
        self._world = None
        self._server_thread = None
        self._link = None
        self._inventory_warning = False
        self._hotbar_interval = 4
        self._hotbar_countdown = 4
        self._hotbar_tick = 0
        self._hotbar_publisher = HotbarPublisher()
        # Replaced by endbotd's [ui] language when the link opens.
        self._language_setting = DEFAULT_LANGUAGE_SETTING

    def on_load(self) -> None:
        self.save_default_config()

    def on_enable(self) -> None:
        settings = self.config.get("endbotd", {})
        token_file = Path(str(settings.get("token-file", "endbotd.token")))
        if not token_file.is_absolute():
            token_file = self.data_folder / token_file
        self._world = EndstoneWorld(self.server)
        self._server_thread = ServerThreadBridge(lambda task: self.server.scheduler.run_task(self, task))
        # Forms resolve to ordinary command arguments and re-enter through _submit_bot.
        presenter = FormPresenter(
            self._submit_bot,
            lambda: tuple(str(player.name) for player in self.server.online_players),
            language=self._language,
        )

        def resolve_sender(source: Principal):
            if source.player_id is not None:
                return self.server.get_player(UUID(source.player_id))
            return self.server.command_sender

        self._link = PluginLink(
            host=str(settings.get("host", "127.0.0.1")),
            port=int(settings.get("port", 19143)),
            token_file=token_file,
            plugin_version=__version__,
            plugin=self,
            server=self.server,
            bridge=self._server_thread,
            world=self._world,
            resolve_sender=resolve_sender,
            show_view=presenter.show_view,
            log=self._log_endbot,
            on_config=self._apply_link_config,
            call_deadline=CALL_DEADLINE_SECONDS,
        )
        self.server.scheduler.run_task(self, self._hotbar_ticker, delay=1, period=1)
        # The server console belongs to the operator who installed Endbot; it
        # gets the control permission directly. Operator players do not.
        self.server.command_sender.add_attachment(self, "endbot.command.control", True)
        self.server.command_sender.add_attachment(self, "endbot.command.controllers", True)
        self.register_events(self)
        # The first link sends a snapshot of everyone already online.
        self._link.start()

    def on_disable(self) -> None:
        if self._link is not None:
            self._link.stop()
            self._link = None
        if self._server_thread is not None:
            self._server_thread.close()
            self._server_thread = None

    def _apply_link_config(self, config: dict) -> None:
        """endbotd's settings for the in-game layer; any link thread."""
        try:
            self._language_setting = language_setting(config.get("language"))
        except ValueError as error:
            self._log_endbot("warning", f"{error}; using {DEFAULT_LANGUAGE_SETTING}")
        try:
            interval, resend = sync_settings(config.get("hotbar") or {})
        except ValueError as error:
            self._log_endbot("warning", f"Invalid hotbar settings from endbotd: {error}")
        else:
            self._hotbar_interval = interval
            self._hotbar_publisher.resend_ticks = resend

    def _hotbar_ticker(self) -> None:
        self._hotbar_countdown -= 1
        if self._hotbar_countdown > 0:
            return
        self._hotbar_countdown = self._hotbar_interval
        self._publish_hotbars()

    def _publish_hotbars(self) -> None:
        # online-mode guarantees normal humans have Xbox identities. Only
        # local-auth Bot connections receive Endbot's private observation.
        self._hotbar_tick += self._hotbar_interval
        identities = set()
        for player in self.server.online_players:
            if str(player.xuid):
                continue
            identities.add(str(player.unique_id))
            try:
                if unsupported(player):
                    player.send_packet(INVENTORY_PACKET_ID, unsupported_payload(str(player.unique_id)))
            except (AttributeError, RuntimeError, ValueError, TypeError):
                pass  # Best effort: BDS corrections still apply away from boats.
            try:
                self._hotbar_publisher.publish(player, self.server, self._hotbar_tick)
            except (RuntimeError, ValueError, TypeError) as error:
                if not self._inventory_warning:
                    self.logger.warning(f"Bot hotbar synchronization unavailable: {type(error).__name__}")
                    self._inventory_warning = True
        self._hotbar_publisher.retain(identities)

    @event_handler
    def on_player_join(self, event: PlayerJoinEvent) -> None:
        player = event.player
        self._hotbar_publisher.previous.pop(str(player.unique_id), None)
        if self._link is not None:
            self._link.player_joined(player)
        if self._world is not None:
            self._world.apply_pending_placement(player)

    @event_handler
    def on_player_quit(self, event: PlayerQuitEvent) -> None:
        if self._link is not None:
            self._link.player_quit(event.player)

    def on_command(self, sender: CommandSender, command: Command, args: list[str]) -> bool:
        if command.name not in ROOTS:
            return False
        if command.name == "botcontrollers" and not isinstance(sender, ConsoleCommandSender):
            sender.send_message("Endbot: controller management is available only from the server console")
            return True
        self._submit_bot(sender, args, command=command.name)
        return True

    def _language(self, sender) -> str:
        """Reply language for a sender; server thread only. The console stays English."""
        if not hasattr(sender, "unique_id"):
            return "en"
        try:
            locale = sender.locale
        except Exception:  # noqa: BLE001 - a missing locale must not block commands
            locale = None
        return resolve_language(self._language_setting, locale)

    def _principal(self, sender, *, command: str = "bot") -> Principal:
        """Who typed it, as the link carries it; server thread only. endbotd adds the rest."""
        if not hasattr(sender, "unique_id"):
            return Principal("console")
        origin = None
        if command == "botscript" and hasattr(sender, "location"):
            location = sender.location
            origin = {"x": float(location.x), "y": float(location.y), "z": float(location.z),
                      "yaw": float(location.yaw), "pitch": float(location.pitch),
                      "dimension": location.dimension.name}
        return Principal("player", language=self._language(sender), player_id=str(sender.unique_id), origin=origin)

    def _log_endbot(self, level: str, message: str) -> None:
        getattr(self.logger, level, self.logger.info)(message)

    def _submit_bot(self, sender, args: list[str], *, command: str = "bot", group_guard=None) -> None:
        """Shared entry for typed commands and form submissions: forward them to endbotd."""
        if not isinstance(sender, ConsoleCommandSender) and not hasattr(sender, "unique_id"):
            sender.send_message(f"Endbot: {tr(self._language(sender), 'you do not have Bot control permission')}")
            return
        language = self._language(sender)
        restarting = f"Endbot: {tr(language, 'Endbot service is restarting')}"
        try:
            # A form's guard is encoded now, so later edits to the form cannot reach the queued command.
            guard = None if group_guard is None else encode_view(group_guard)
            if self._link is None:
                raise LinkError("unavailable", "Endbot service is restarting")
            answer = self._link.submit(command, self._principal(sender, command=command), argv=list(args),
                                       guard=guard)
        except LinkError:
            sender.send_message(restarting)
            return
        except TypeError as error:
            self._log_endbot("warning", f"A form guard could not be sent to endbotd: {error}")
            sender.send_message(f"Endbot: {tr(language, 'command service is unavailable')}")
            return
        player_id = getattr(sender, "unique_id", None)

        def answered(future) -> None:
            bridge = self._server_thread
            if future.exception() is not None and bridge is not None:
                # The link dropped before endbotd took the command; tell whoever typed it.
                bridge.post(lambda: self._tell(player_id, restarting))

        answer.add_done_callback(answered)

    def _tell(self, player_id, message: str) -> None:
        target = self.server.command_sender if player_id is None else self.server.get_player(player_id)
        if target is not None:
            target.send_message(message)
