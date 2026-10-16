"""Thin Endstone adapter for Endbot commands."""

import shlex
from pathlib import Path
from typing import ClassVar
from uuid import UUID

from endstone.command import Command, CommandSender, ConsoleCommandSender
from endstone.event import PlayerJoinEvent, PlayerQuitEvent, event_handler
from endstone.plugin import Plugin

from endstone_endbot.authorization import AllowedPlayers
from endstone_endbot.commands import BotCommandService
from endstone_endbot.control import RuntimeControlClient
from endstone_endbot.controller_policy import ControllerPolicy
from endstone_endbot.controller_requests import execute_request
from endstone_endbot.dispatch import AsyncCommandRunner, CommandSource, ServerThreadBridge, ServerThreadWorld
from endstone_endbot.enrollment import ControllerEnrollment, _is_xuid
from endstone_endbot.form_adapter import FormPresenter
from endstone_endbot.i18n import DEFAULT_LANGUAGE_SETTING, language_setting, resolve_language, tr
from endstone_endbot.inventory_sync import HotbarPublisher, sync_settings
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
    "/bot (ping|list|help|menu)<command: EndbotRoot>",
    "/bot <command: string> <page: int>",
    (
        "/bot (help)<command: EndbotHelpTopic> (status|spawn|resume|reconnect|despawn|forget|rename|tp|move|look|"
        "jump|attack|use|sprint|sneak|hotbar|interact|destroy|drop|radius|stop|menu)<topic: EndbotHelpTopicName>"
    ),
    "/bot <name: string> <operation: string>",
    "/bot <name: string> <operation: string> <arguments: message>",
    # One single-value enum per no-argument operation: the Windows client shows
    # a multi-value enum here only as its type name (<operation: EndbotNoArgs>),
    # which hid status/resume/reconnect/despawn/forget/stop from suggestions.
    "/bot <name: string> (status)<operation: EndbotStatus>",
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
        self._bot_commands = BotCommandService()
        self._allowed_players = AllowedPlayers()
        self._controllers = ControllerEnrollment((), None)
        self._world = None
        self._server_thread = None
        self._command_runner = None
        self._controller_policy = None
        self._controller_requests_directory = None
        self._control_attachments = {}
        self._control_granted = {}
        self._authorization_versions = {}
        self._inventory_warning = False
        self._hotbar_interval = 4
        self._hotbar_tick = 0
        self._hotbar_publisher = HotbarPublisher()
        self._language_setting = DEFAULT_LANGUAGE_SETTING

    def on_load(self) -> None:
        self.save_default_config()
        try:
            self._language_setting = language_setting((self.config.get("ui") or {}).get("language"))
        except ValueError as error:
            self._language_setting = DEFAULT_LANGUAGE_SETTING
            self.logger.warning(f"{error}; using {DEFAULT_LANGUAGE_SETTING}")
        authorization = self.config.get("authorization", {})
        self._allowed_players = AllowedPlayers.from_config(authorization)
        self._controllers = ControllerEnrollment.from_config(authorization, self.data_folder)
        self._controller_policy = ControllerPolicy.from_config(authorization, self.data_folder)
        requests = authorization.get("controller-requests-directory")
        if requests is not None:
            self._controller_requests_directory = Path(requests)
            if not self._controller_requests_directory.is_absolute():
                self._controller_requests_directory = self.data_folder / self._controller_requests_directory
        if (
            self._controllers.bindings_path is not None
            and self._controllers.bindings_path.resolve() == self._controller_policy.path.resolve()
        ):
            raise ValueError("controllers-file and controller-policy-file must be different files")
        for gamertag in self._controllers.pending():
            self.logger.info(f"Controller {gamertag} is pending; it binds on its first Xbox-authenticated join.")

    def on_enable(self) -> None:
        runtime = self.config.get("runtime", {})
        self._hotbar_interval, resend_ticks = sync_settings(runtime)
        self._hotbar_publisher = HotbarPublisher(resend_ticks)
        self._hotbar_tick = 0
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

        def authorized_sender(source: CommandSource):
            return resolve_sender(source) if self._authorize_source(source) else None

        command_world = ServerThreadWorld(self._world, self._server_thread, authorized_sender)
        # Forms resolve to ordinary /bot arguments and re-enter through _submit_bot.
        presenter = FormPresenter(
            self._submit_bot,
            lambda: tuple(str(player.name) for player in self.server.online_players),
            language=self._language,
        )
        self._bot_commands = BotCommandService(control, command_world)
        self._command_runner = AsyncCommandRunner(
            self._bot_commands,
            self._server_thread,
            resolve_sender,
            authorize=self._authorize_source,
            authorize_loop=self._authorize_loop,
            log=self._log_endbot,
            show_view=presenter.show_view,
            language=self._language,
            revision=lambda sender: (
                self._authorization_versions.get(str(sender.unique_id), 0) if hasattr(sender, "unique_id") else None
            ),
        )
        self.server.scheduler.run_task(self, self._command_runner.poll, delay=4, period=4)
        self.server.scheduler.run_task(
            self, self._publish_hotbars, delay=self._hotbar_interval, period=self._hotbar_interval
        )
        # The server console belongs to the operator who installed Endbot; it
        # gets the control permission directly. Operator players do not.
        self.server.command_sender.add_attachment(self, "endbot.command.control", True)
        self.server.command_sender.add_attachment(self, "endbot.command.controllers", True)
        self.register_events(self)
        for player in self.server.online_players:
            self._join_controller(player)

    def on_disable(self) -> None:
        if self._command_runner is not None:
            self._command_runner.close()
            self._command_runner = None
        if self._server_thread is not None:
            self._server_thread.close()
            self._server_thread = None
        self._control_attachments.clear()
        self._control_granted.clear()

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
        self._join_controller(player)
        if self._world is not None:
            self._world.apply_pending_placement(player)

    def _join_controller(self, player) -> None:
        player_uuid, xuid = str(player.unique_id), str(player.xuid)
        self._authorization_versions[player_uuid] = self._authorization_versions.get(player_uuid, 0) + 1
        policy = self._controller_policy
        if policy is not None:
            policy.enroll(player.name, xuid, player_uuid)
        if _is_xuid(xuid) and (
            policy is None or (not policy.denied(xuid, player_uuid) and not policy.name_denied(player.name))
        ):
            _, binding = self._controllers.authorize(player.name, xuid, player_uuid)
            if binding is not None:
                self.logger.info(f"Bound controller {binding.gamertag} through Xbox authentication.")
        self._apply_control_permission(player)

    def _allows_control(self, player) -> bool:
        return self._control_decision(player.name, str(player.xuid), str(player.unique_id))[0]

    def _control_decision(self, name, xuid, player_uuid) -> tuple[bool, str]:
        if not _is_xuid(xuid):
            return False, "Xbox authentication required"
        policy = self._controller_policy
        if policy is not None:
            if xuid in policy.state.denied_xuids:
                return False, "revoked XUID"
            if player_uuid in policy.state.denied_uuids:
                return False, "revoked UUID"
            if policy.grants(xuid):
                return True, "runtime grant"
            if policy.name_denied(name):
                return False, "revoked GamerTag"
        if any(binding.xuid == xuid for binding in self._controllers.bindings):
            return True, "bootstrap binding"
        if xuid in self._allowed_players.xuids:
            return True, "legacy XUID allowlist"
        if player_uuid in self._allowed_players.uuids:
            return True, "legacy UUID allowlist"
        return False, "not enrolled"

    def _controller_report(self) -> list[dict]:
        policy = self._controller_policy
        rows, seen_xuids, seen_names, seen_uuids = [], set(), set(), set()
        online = list(self.server.online_players)
        bindings = (*self._controllers.bindings, *policy.state.grants)
        # Prefer the latest runtime-bound name, then the authenticated online name.
        for binding in reversed(bindings):
            if binding.xuid in seen_xuids:
                continue
            player = next((entry for entry in online if str(entry.xuid) == binding.xuid), None)
            name = player.name if player is not None else binding.gamertag
            identity = str(player.unique_id) if player is not None else binding.uuid
            allowed, reason = self._control_decision(name, binding.xuid, identity)
            rows.append(
                {
                    "name": name,
                    "state": "allowed" if allowed else "revoked",
                    "reason": reason,
                    "xuid": binding.xuid,
                    "uuid": identity,
                    "online": player is not None,
                }
            )
            seen_xuids.add(binding.xuid)
            seen_uuids.add(identity)
            seen_names.add(binding.gamertag.casefold())
            seen_names.add(name.casefold())
        for player in online:
            xuid, identity = str(player.xuid), str(player.unique_id)
            if not _is_xuid(xuid) or xuid in seen_xuids:
                continue
            allowed, reason = self._control_decision(player.name, xuid, identity)
            rows.append(
                {
                    "name": player.name,
                    "state": "allowed" if allowed else "denied",
                    "reason": reason,
                    "xuid": xuid,
                    "uuid": identity,
                    "online": True,
                }
            )
            seen_xuids.add(xuid)
            seen_uuids.add(identity)
            seen_names.add(player.name.casefold())
        for name in (*self._controllers.pending(), *policy.state.pending, *policy.state.denied_names):
            if name.casefold() in seen_names:
                continue
            revoked = policy.name_denied(name)
            rows.append(
                {
                    "name": name,
                    "state": "revoked" if revoked else "pending",
                    "reason": "revoked GamerTag" if revoked else "awaiting first Xbox-authenticated join",
                }
            )
            seen_names.add(name.casefold())
        for kind, configured, denied, seen in (
            ("xuid", self._allowed_players.xuids, policy.state.denied_xuids, seen_xuids),
            ("uuid", self._allowed_players.uuids, policy.state.denied_uuids, seen_uuids),
        ):
            for identity in sorted(set(configured) | set(denied)):
                if identity in seen:
                    continue
                revoked = identity in denied
                rows.append(
                    {
                        "name": f"{kind}:{identity}",
                        "state": "revoked" if revoked else "conditional",
                        "reason": f"revoked {kind.upper()}"
                        if revoked
                        else f"legacy {kind.upper()} allowlist; requires Xbox authentication and identity checks",
                    }
                )
        return sorted(rows, key=lambda row: row["name"].casefold())

    def _authorize_source(self, source: CommandSource) -> bool:
        if source.player_id is None:
            return isinstance(source.sender, ConsoleCommandSender)
        player = self.server.get_player(UUID(source.player_id))
        return (
            player is not None
            and self._allows_control(player)
            and source.authorization_revision == self._authorization_versions.get(source.player_id, 0)
        )

    def _authorize_loop(self, source: CommandSource) -> bool:
        """Keep a continuous loop while its controller remains permitted.

        Unlike queued commands, a loop survives logoff and other operations;
        only a revocation (or a stop) ends it. Offline controllers are checked
        by the identity captured when they started the loop.
        """
        if source.player_id is None:
            return isinstance(source.sender, ConsoleCommandSender)
        player = self.server.get_player(UUID(source.player_id))
        if player is not None:
            return self._allows_control(player)
        return self._control_decision(source.name or "", source.xuid or "", source.player_id)[0]

    def _log_endbot(self, level: str, message: str) -> None:
        getattr(self.logger, level, self.logger.info)(message)

    def _apply_control_permission(self, player) -> None:
        identity = str(player.unique_id)
        allowed = self._allows_control(player)
        previous = self._control_granted.get(identity, False)
        if previous != allowed:
            self._authorization_versions[identity] = self._authorization_versions.get(identity, 0) + 1
        self._control_granted[identity] = allowed
        attachment = self._control_attachments.get(identity)
        if attachment is not None:
            attachment.set_permission("endbot.command.control", allowed)
        elif allowed:
            attachment = player.add_attachment(self, "endbot.command.control", True)
            if attachment is not None:
                self._control_attachments[identity] = attachment
        player.update_commands()

    @event_handler
    def on_player_quit(self, event: PlayerQuitEvent) -> None:
        identity = str(event.player.unique_id)
        self._control_attachments.pop(identity, None)
        self._control_granted.pop(identity, None)
        self._authorization_versions[identity] = self._authorization_versions.get(identity, 0) + 1

    def _manage_controllers(self, sender: CommandSender, args: list[str]) -> None:
        if not isinstance(sender, ConsoleCommandSender):
            sender.send_message("Endbot: controller management is available only from the server console")
            return
        if args and args[0] == "request":
            try:
                if len(args) != 2 or self._controller_requests_directory is None:
                    raise ValueError("controller result channel is unavailable")
                result = execute_request(self._controller_requests_directory, args[1], self._controller_operation)
            except (OSError, ValueError, TypeError, KeyError) as error:
                sender.send_message(f"Endbot: controller request failed: {error}")
                return
        else:
            result = self._controller_operation(args)
        for message in result["messages"]:
            sender.send_message(message)

    def _controller_operation(self, args: list[str], target: str | None = None) -> dict:
        """Run a console operation; ``target`` is an already-validated whole name."""
        try:
            if self._controller_policy is None:
                raise ValueError("controller policy is unavailable")
            if not args or args == ["list"]:
                rows = self._controller_report()
                messages = ["Endbot controllers (live effective authorization):"]
                messages.extend(
                    f"{row['name']}: {row['state']} - {row['reason']}" + (" (online)" if row.get("online") else "")
                    for row in rows
                )
                if not rows:
                    messages.append("none; the server console remains authorized")
                return {"ok": True, "messages": messages, "entries": rows}
            operation = args[0].lower()
            if target is None and len(args) >= 2:
                # Console text: quoted names may contain spaces.
                target = " ".join(shlex.split(" ".join(args[1:])))
            if operation not in {"add", "remove"} or not target:
                raise ValueError("Use botcontrollers list|add <GamerTag>|remove <GamerTag|xuid:id|uuid:id>")
            player = next(
                (entry for entry in self.server.online_players if entry.name.casefold() == target.casefold()), None
            )
            if operation == "add":
                binding = next(
                    (
                        entry
                        for entry in (*self._controllers.bindings, *self._controller_policy.state.grants)
                        if entry.gamertag.casefold() == target.casefold()
                    ),
                    None,
                )
                if player is not None:
                    self._controller_policy.add(target, xuid=str(player.xuid), player_uuid=str(player.unique_id))
                elif binding is not None:
                    self._controller_policy.add(target, xuid=binding.xuid, player_uuid=binding.uuid)
                else:
                    self._controller_policy.add(target)
            else:
                self._controller_policy.remove(
                    target,
                    known=self._controllers.bindings,
                    configured=self._controllers.gamertags,
                    xuid=str(player.xuid) if player is not None else None,
                    player_uuid=str(player.unique_id) if player is not None else None,
                )
            for entry in self.server.online_players:
                self._apply_control_permission(entry)
            state = "updated" if operation == "add" else "revoked"
            rows = self._controller_report()
            details = [
                f"{row['name']}: {row['state']} - {row['reason']}"
                for row in rows
                if row["name"].casefold() == target.casefold()
            ]
            return {"ok": True, "messages": [f"Endbot: controller {target} {state}", *details], "entries": rows}
        except (OSError, ValueError, TypeError) as error:
            return {"ok": False, "messages": [f"Endbot: controller change failed: {error}"]}

    def on_command(self, sender: CommandSender, command: Command, args: list[str]) -> bool:
        if command.name == "botcontrollers":
            self._manage_controllers(sender, args)
            return True
        if command.name != "bot":
            return False
        self._submit_bot(sender, args)
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

    def _submit_bot(self, sender, args: list[str]) -> None:
        """Shared entry for typed /bot commands and form submissions."""
        if not isinstance(sender, ConsoleCommandSender) and (
            not hasattr(sender, "unique_id") or not self._allows_control(sender)
        ):
            sender.send_message(f"Endbot: {tr(self._language(sender), 'you do not have Bot control permission')}")
            return
        if self._command_runner is None or not self._command_runner.submit(args, sender):
            sender.send_message(f"Endbot: {tr(self._language(sender), 'command service is unavailable')}")
