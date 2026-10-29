import importlib
import re
import sys
import tempfile
import types
import unittest
from pathlib import Path

from endbot_core.principal import Principal


class FakePlugin:
    pass


class FakeCommand:
    def __init__(self, name: str) -> None:
        self.name = name


class FakeSender:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def send_message(self, message: str) -> None:
        self.messages.append(message)


class FakeConsole(FakeSender):
    pass


class PluginAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        endstone = types.ModuleType("endstone")
        command = types.ModuleType("endstone.command")
        event = types.ModuleType("endstone.event")
        level = types.ModuleType("endstone.level")
        plugin = types.ModuleType("endstone.plugin")
        command.Command = FakeCommand
        command.CommandSender = FakeSender
        command.ConsoleCommandSender = FakeConsole
        event.PlayerJoinEvent = type("PlayerJoinEvent", (), {})
        event.PlayerQuitEvent = type("PlayerQuitEvent", (), {})
        event.event_handler = lambda function: function
        level.Location = type("Location", (), {})
        plugin.Plugin = FakePlugin
        sys.modules.update(
            {
                "endstone": endstone,
                "endstone.command": command,
                "endstone.event": event,
                "endstone.level": level,
                "endstone.plugin": plugin,
            }
        )
        cls.module = importlib.reload(importlib.import_module("endstone_endbot.plugin"))

    def test_inventory_observations_only_go_to_local_bot_connections(self):
        from unittest.mock import patch

        plugin = self.module.EndbotPlugin()
        local_bot = types.SimpleNamespace(xuid="", unique_id="bot")
        human = types.SimpleNamespace(xuid="123456789")
        plugin.server = types.SimpleNamespace(online_players=[human, local_bot])
        with patch.object(plugin._hotbar_publisher, "publish") as publish:
            plugin._publish_hotbars()
        publish.assert_called_once_with(local_bot, plugin.server, 4)

    def test_inventory_observation_failure_does_not_skip_the_next_bot_or_spam_logs(self):
        from unittest.mock import Mock, patch

        plugin = self.module.EndbotPlugin()
        bots = [types.SimpleNamespace(xuid="", unique_id="a"), types.SimpleNamespace(xuid="", unique_id="b")]
        plugin.server = types.SimpleNamespace(online_players=bots)
        plugin.logger = Mock()
        with patch.object(
            plugin._hotbar_publisher, "publish", side_effect=[ValueError("bad data"), None, ValueError("bad"), None]
        ) as publish:
            plugin._publish_hotbars()
            plugin._publish_hotbars()
        self.assertEqual(publish.call_count, 4)
        plugin.logger.warning.assert_called_once()

    def test_metadata_registers_narrow_command_and_permission(self) -> None:
        plugin_class = self.module.EndbotPlugin
        self.assertEqual(plugin_class.api_version, "0.11")
        usages = plugin_class.commands["bot"]["usages"]
        self.assertIn("/bot [command: string] [topic: string]", usages)
        self.assertIn(
            "/bot (ping|list|help|menu)<command: EndbotRoot>",
            usages,
        )
        # The retired dense "advanced" mode stays parseable through the root
        # string fallback but must not be advertised as native completion.
        self.assertNotIn(
            "/bot (help)<command: EndbotHelp> (advanced)<topic: EndbotHelpTopic>",
            usages,
        )
        self.assertFalse(any("(advanced)" in usage for usage in usages))
        # Parseability layer: enum-free string overloads. Live BDS never
        # matches params after an enum, so these carry every named form while
        # the typed tree below carries completion. No message may sit at the
        # operation position: that shape absorbs all named-Bot autocomplete.
        self.assertNotIn("/bot <name: string> <operation: message>", usages)
        self.assertIn("/bot <name: string> <operation: string>", usages)
        self.assertIn("/bot <name: string> <operation: string> <arguments: message>", usages)
        self.assertIn("/bot <command: string> <page: int>", usages)
        self.assertTrue(any("EndbotHelpTopic" in usage for usage in usages))
        named_usages = [usage for usage in usages if usage.startswith("/bot <name: string>")]
        advertised_operations = set()
        for usage in named_usages:
            if "<operation: string>" in usage:
                continue  # parseability layer, not an autocomplete branch
            match = re.match(r"^/bot <name: string> \(([^)]+)\)<[^:]+: Endbot[^>]+>", usage)
            self.assertIsNotNone(match, f"named-Bot overload must advertise a typed operation: {usage}")
            advertised_operations.update(match.group(1).split("|"))
        self.assertEqual(
            advertised_operations,
            {
                "status",
                "inventory",
                "entities",
                "entity",
                "dismount",
                "target",
                "block",
                "blocks",
                "hold",
                "swap",
                "spawn",
                "resume",
                "reconnect",
                "despawn",
                "forget",
                "rename",
                "tp",
                "move",
                "look",
                "jump",
                "attack",
                "use",
                "sprint",
                "sneak",
                "hotbar",
                "radius",
                "interact",
                "destroy",
                "drop",
                "stop",
                "menu",
            },
        )
        self.assertIn("/bot <name: string> (spawn)<operation: EndbotSpawn>", usages)
        self.assertIn(
            "/bot <name: string> (jump|attack|use)<action: EndbotDefaultAction>",
            usages,
        )
        self.assertIn("/bot <name: string> (hotbar)<operation: EndbotHotbarQuery>", usages)
        self.assertTrue(any("EndbotHotbarSet" in usage and "<slot: int>" in usage for usage in usages))
        self.assertIn("/bot <name: string> (drop)<operation: EndbotDropOne>", usages)
        # Each no-argument operation is its own literal so the client lists it by name.
        for operation in ("status", "inventory", "resume", "reconnect", "despawn", "forget", "stop"):
            self.assertIn(f"/bot <name: string> ({operation})<operation: Endbot{operation.capitalize()}>", usages)
        self.assertFalse(any("EndbotNoArgs" in usage for usage in usages))
        self.assertTrue(any("EndbotDirection" in usage for usage in usages))
        self.assertTrue(any("EndbotActionMode" in usage and "once|continuous|stop" in usage for usage in usages))
        self.assertTrue(any("DimensionName" in usage and "overworld|nether|end" in usage for usage in usages))
        self.assertTrue(any("EndbotBlockFace" in usage and "block_pos" in usage for usage in usages))

        enum_names = re.findall(r": (Endbot\w+)>", "\n".join(usages))
        self.assertEqual(len(enum_names), len(set(enum_names)), "native enum names must be globally unique")
        self.assertTrue(any("EndbotDropMode" in usage and "stack" in usage for usage in usages))
        self.assertFalse(any("<direction: string>" in usage for usage in usages))
        self.assertEqual(plugin_class.commands["bot"]["permissions"], ["endbot.command.control"])
        self.assertIs(plugin_class.permissions["endbot.command.control"]["default"], False)

    def test_enable_grants_control_to_the_server_console_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            plugin = self.module.EndbotPlugin()
            plugin.config = {"runtime": {"token-file": "control.token"}}
            plugin.data_folder = Path(directory)
            plugin.logger = types.SimpleNamespace(
                info=lambda message: None, warning=lambda message: None, error=lambda message: None)
            attachments: list[tuple[str, bool]] = []
            console = types.SimpleNamespace(
                add_attachment=lambda owner, permission, value: attachments.append((permission, value))
            )
            plugin.server = types.SimpleNamespace(
                command_sender=console,
                online_players=[],
                scheduler=types.SimpleNamespace(run_task=lambda owner, task, **options: None),
                get_player=lambda identifier: None,
            )
            plugin.register_events = lambda listener: None
            plugin.on_enable()
            self.addCleanup(plugin.on_disable)

            self.assertEqual(attachments, [("endbot.command.control", True), ("endbot.command.controllers", True)])
            self.assertIs(self.module.EndbotPlugin.permissions["endbot.command.control"]["default"], False)

    def test_adapter_sends_pong(self) -> None:
        plugin = self.module.EndbotPlugin()
        sender = FakeConsole()
        submissions = []
        plugin._command_runner = types.SimpleNamespace(
            submit=lambda args, target, **_: submissions.append((args, target)) or True,
        )
        self.assertTrue(plugin.on_command(sender, FakeCommand("bot"), ["ping"]))
        self.assertEqual(sender.messages, [])
        self.assertEqual(submissions, [(["ping"], Principal())])

    def test_group_management_uses_the_authorized_worker_and_control_permission(self) -> None:
        plugin = self.module.EndbotPlugin()
        submissions = []
        plugin._command_runner = types.SimpleNamespace(
            submit=lambda args, sender, **options: submissions.append((args, options)) or True,
        )
        self.assertEqual(plugin.commands["botgroup"]["permissions"], ["endbot.command.control"])
        usages = plugin.commands["botgroup"]["usages"]
        for action in ("list", "help", "create", "delete", "add", "remove"):
            self.assertTrue(any(usage.startswith(f"/botgroup ({action})<action: EndbotGroup") for usage in usages))
        self.assertIn("/botgroup <action: string> <arguments: message>", usages)
        self.assertFalse(any("EndbotGroupMembers" in usage for usage in usages))
        self.assertIn("/botgroup (list)<action: EndbotGroupList> [group: message]", usages)
        self.assertTrue(plugin.on_command(FakeConsole(), FakeCommand("botgroup"), ["create", "#miners"]))
        self.assertEqual(submissions, [(["create", "#miners"], {"command": "botgroup"})])
        unauthorized = types.SimpleNamespace(messages=[], send_message=lambda message: None)
        plugin.on_command(unauthorized, FakeCommand("botgroup"), ["delete", "#miners"])
        self.assertEqual(len(submissions), 1)

    def test_join_binds_pending_controller_and_grants_only_control(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            plugin = self.module.EndbotPlugin()
            plugin.config = {
                "authorization": {"controller-gamertags": ["OwnerTag"], "controllers-file": "controllers.json"}
            }
            plugin.data_folder = Path(directory)
            plugin.logger = types.SimpleNamespace(info=lambda message: None)
            plugin.save_default_config = lambda: None
            plugin.on_load()

            def join(name: str, xuid: str, unique_id: str) -> list[tuple[str, bool]]:
                attachments: list[tuple[str, bool]] = []
                player = types.SimpleNamespace(
                    name=name,
                    xuid=xuid,
                    unique_id=unique_id,
                    add_attachment=lambda owner, permission, value: attachments.append((permission, value)),
                    update_commands=lambda: None,
                )
                plugin.on_player_join(types.SimpleNamespace(player=player))
                return attachments

            bot = join("OwnerTag", "", "3890c6b6-74cf-4bce-8fc3-6bf7f1cc513b")
            owner = join("OwnerTag", "2535469543141592", "63572362-0c83-5f0a-8cec-e1b788101798")
            impostor = join("OwnerTag", "2535400000000001", "526290d1-a78e-4a18-b32e-8c0d14e10850")

            self.assertEqual(bot, [])
            self.assertEqual(owner, [("endbot.command.control", True)])
            self.assertEqual(impostor, [])
            self.assertTrue((Path(directory) / "controllers.json").is_file())

    def controller_fixture(self, directory):
        from endbot_core.controller_policy import ControllerPolicy

        plugin = self.module.EndbotPlugin()
        plugin._controller_policy = ControllerPolicy(Path(directory) / "policy.json")
        plugin.server = types.SimpleNamespace(
            online_players=[],
            get_player=lambda identifier: next(
                (player for player in plugin.server.online_players if str(player.unique_id) == str(identifier)), None
            ),
        )
        plugin.logger = types.SimpleNamespace(info=lambda message: None)
        return plugin

    def controller_player(self, name="Owner", xuid="2535412345678901"):
        from uuid import UUID

        player = FakeSender()
        player.name, player.xuid = name, xuid
        player.unique_id = UUID("11111111-2222-4333-8444-555555555555")
        player.location = object()
        player.permission_values = []
        player.command_updates = 0

        def attach(owner, permission, value):
            player.permission_values.append((permission, value))
            return types.SimpleNamespace(
                set_permission=lambda key, setting: player.permission_values.append((key, setting))
            )

        player.add_attachment = attach
        player.update_commands = lambda: setattr(player, "command_updates", player.command_updates + 1)
        return player

    def test_live_grant_revoke_regrant_cancels_old_sources_and_survives_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            plugin = self.controller_fixture(directory)
            player = self.controller_player()
            plugin.server.online_players = [player]
            console = FakeConsole()
            command = FakeCommand("botcontrollers")
            plugin.on_command(console, command, ["add", player.name])
            self.assertTrue(plugin._allows_control(player))
            source = plugin._principal(player)
            self.assertTrue(plugin._authorize(source))
            plugin.on_command(console, command, ["remove", player.name])
            self.assertFalse(plugin._allows_control(player))
            self.assertFalse(plugin._authorize(source))
            self.assertEqual(player.permission_values[-1], ("endbot.command.control", False))
            plugin.on_command(console, command, ["add", player.name])
            self.assertTrue(plugin._allows_control(player))
            self.assertFalse(plugin._authorize(source))
            self.assertEqual(player.command_updates, 3)
            restarted = self.controller_fixture(directory)
            self.assertTrue(restarted._allows_control(player))
            current_source = plugin._principal(player)
            self.assertTrue(plugin._authorize(current_source))
            plugin.on_player_quit(types.SimpleNamespace(player=player))
            self.assertNotIn(str(player.unique_id), plugin._control_attachments)
            self.assertFalse(plugin._authorize(current_source))
            plugin._join_controller(player)
            self.assertTrue(plugin._allows_control(player))
            self.assertFalse(plugin._authorize(current_source))

    def test_form_submissions_share_the_typed_command_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            plugin = self.controller_fixture(directory)
            submissions = []
            plugin._command_runner = types.SimpleNamespace(
                submit=lambda args, target, **_: submissions.append((args, target)) or True
            )
            player = self.controller_player()
            plugin.server.online_players = [player]
            plugin._submit_bot(player, ["Alice", "stop"])
            self.assertEqual(player.messages, ["Endbot: you do not have Bot control permission"])
            plugin.on_command(FakeConsole(), FakeCommand("botcontrollers"), ["add", player.name])
            plugin._submit_bot(player, ["Alice", "stop"])
            self.assertEqual(submissions, [(["Alice", "stop"], plugin._principal(player))])
            self.assertEqual(submissions[0][1].kind, "player")

    def test_reply_language_follows_setting_and_client_locale(self):
        with tempfile.TemporaryDirectory() as directory:
            plugin = self.controller_fixture(directory)
            player = self.controller_player()
            player.locale = "ja_JP"
            plugin._submit_bot(player, ["Alice", "stop"])
            self.assertEqual(player.messages, ["Endbot: Bot の操作権限がありません"])
            plugin._language_setting = "en"
            plugin._submit_bot(player, ["Alice", "stop"])
            self.assertEqual(player.messages[-1], "Endbot: you do not have Bot control permission")
            plugin._language_setting = "ja"
            self.assertEqual(plugin._language(player), "ja")
            # The console stays English, like the server log.
            self.assertEqual(plugin._language(FakeConsole()), "en")

    def test_language_setting_loads_from_config_and_falls_back_to_auto(self):
        for configured, expected, warned in ((None, "auto", False), ("ja", "ja", False), ("fr", "auto", True)):
            with self.subTest(configured=configured), tempfile.TemporaryDirectory() as directory:
                plugin = self.module.EndbotPlugin()
                plugin.config = {} if configured is None else {"ui": {"language": configured}}
                plugin.data_folder = Path(directory)
                warnings = []
                plugin.logger = types.SimpleNamespace(info=lambda message: None, warning=warnings.append)
                plugin.save_default_config = lambda: None
                plugin.on_load()
                self.assertEqual(plugin._language_setting, expected)
                self.assertEqual(bool(warnings), warned)

    def test_mining_loop_survives_controller_logoff_but_not_revocation(self):
        from endbot_core.principal import as_job

        with tempfile.TemporaryDirectory() as directory:
            plugin = self.controller_fixture(directory)
            player = self.controller_player()
            plugin.server.online_players = [player]
            console, command = FakeConsole(), FakeCommand("botcontrollers")
            plugin.on_command(console, command, ["add", player.name])
            source = plugin._principal(player)
            self.assertEqual((source.xuid, source.name), (player.xuid, player.name))
            job = as_job(source)
            plugin.on_player_quit(types.SimpleNamespace(player=player))
            plugin.server.online_players = []
            # Queued commands are invalidated by logoff; a running job is not.
            self.assertFalse(plugin._authorize(source))
            self.assertTrue(plugin._authorize(job))
            plugin.on_command(console, command, ["remove", f"xuid:{player.xuid}"])
            self.assertFalse(plugin._authorize(job))
            self.assertTrue(plugin._authorize(as_job(Principal())))

    def test_revoke_and_regrant_never_revive_an_older_job(self):
        from endbot_core.principal import as_job

        with tempfile.TemporaryDirectory() as directory:
            plugin = self.controller_fixture(directory)
            player = self.controller_player()
            plugin.server.online_players = [player]
            self.assertTrue(plugin._controller_operation(["add", player.name])["ok"])
            job = as_job(plugin._principal(player))
            self.assertTrue(plugin._authorize(job))
            self.assertTrue(plugin._controller_operation(["remove", f"xuid:{player.xuid}"])["ok"])
            self.assertTrue(plugin._controller_operation(["add", player.name])["ok"])
            self.assertTrue(plugin._allows_control(player))
            self.assertFalse(plugin._authorize(job))
            self.assertTrue(plugin._authorize(as_job(plugin._principal(player))))

    def test_principal_captures_grant_origin_language_and_copies_form_values(self):
        from endstone_endbot.script_forms import ScriptGuard

        with tempfile.TemporaryDirectory() as directory:
            plugin = self.controller_fixture(directory)
            player = self.controller_player()
            player.locale = "ja_JP"
            player.location = types.SimpleNamespace(x=1, y=64, z=2, yaw=90, pitch=0,
                                                    dimension=types.SimpleNamespace(name="Overworld"))
            plugin._grant_versions[str(player.unique_id)] = 7
            guard = ScriptGuard("r1", "test", values={"name": "Alice"})
            principal = plugin._principal(player, command="botscript", group_guard=guard)
            guard.values["name"] = "Changed"
            self.assertEqual((principal.kind, principal.grant_revision, principal.language), ("player", 7, "ja"))
            self.assertEqual(principal.origin, {"x": 1.0, "y": 64.0, "z": 2.0, "yaw": 90.0, "pitch": 0.0,
                                                "dimension": "Overworld"})
            self.assertEqual(principal.group_guard.values, {"name": "Alice"})
            self.assertIsNone(plugin._principal(player).origin)
            self.assertEqual(plugin._principal(FakeConsole()), Principal())
            # The console keeps a form guard it was given (the scripting probe saves this way).
            console = plugin._principal(FakeConsole(), command="botscript", group_guard=guard)
            self.assertEqual((console.kind, console.group_guard.values), ("console", {"name": "Changed"}))

    def test_script_grant_revision_survives_logout_but_revoke_regrant_never_revives_it(self):
        from dataclasses import replace

        with tempfile.TemporaryDirectory() as directory:
            plugin = self.controller_fixture(directory)
            player = self.controller_player()
            plugin.server.online_players = [player]
            self.assertTrue(plugin._controller_operation(["add", player.name])["ok"])
            source = replace(plugin._principal(player), kind="run", run_id="run1")
            self.assertTrue(plugin._authorize(source))
            plugin.on_player_quit(types.SimpleNamespace(player=player))
            plugin.server.online_players = []
            self.assertTrue(plugin._authorize(source))
            self.assertTrue(plugin._controller_operation(["remove", f"xuid:{player.xuid}"])["ok"])
            self.assertFalse(plugin._authorize(source))
            self.assertTrue(plugin._controller_operation(["add", player.name])["ok"])
            self.assertTrue(plugin._allows_control(player))
            self.assertFalse(plugin._authorize(source))
            new_source = replace(source, grant_revision=plugin._grant_versions[str(player.unique_id)])
            self.assertTrue(plugin._authorize(new_source))

    def test_controller_cannot_manage_access_even_with_external_permission(self):
        with tempfile.TemporaryDirectory() as directory:
            plugin = self.controller_fixture(directory)
            player = self.controller_player()
            player.has_permission = lambda permission: True
            plugin.on_command(player, FakeCommand("botcontrollers"), ["add", "Other"])
            self.assertIn("only from the server console", player.messages[-1])
            self.assertFalse(plugin._controller_policy.path.exists())
            plugin.on_command(player, FakeCommand("bot"), ["ping"])
            self.assertIn("do not have Bot control permission", player.messages[-1])

    def test_local_bot_cannot_be_granted_control(self):
        with tempfile.TemporaryDirectory() as directory:
            plugin = self.controller_fixture(directory)
            bot = self.controller_player(xuid="")
            plugin.server.online_players = [bot]
            console = FakeConsole()
            plugin.on_command(console, FakeCommand("botcontrollers"), ["add", bot.name])
            self.assertIn("change failed", console.messages[-1])
            self.assertFalse(plugin._allows_control(bot))
            self.assertFalse(plugin._controller_policy.path.exists())

    def test_pending_grant_binds_on_join_and_revocation_overrides_bootstrap(self):
        from endbot_core.enrollment import ControllerEnrollment

        with tempfile.TemporaryDirectory() as directory:
            plugin = self.controller_fixture(directory)
            plugin._controllers = ControllerEnrollment(("Owner",), Path(directory) / "bindings.json")
            console = FakeConsole()
            command = FakeCommand("botcontrollers")
            plugin.on_command(console, command, ["remove", "Owner"])
            player = self.controller_player()
            plugin._join_controller(player)
            self.assertFalse(plugin._allows_control(player))
            self.assertEqual(plugin._controllers.bindings, ())
            plugin.on_command(console, command, ["add", "Owner"])
            plugin._join_controller(player)
            self.assertTrue(plugin._allows_control(player))
            plugin.on_command(console, command, ["remove", "Owner"])
            player.name = "RenamedOwner"
            plugin._join_controller(player)
            self.assertFalse(plugin._allows_control(player))
            restarted = self.controller_fixture(directory)
            self.assertFalse(restarted._allows_control(player))

    def test_failed_policy_write_does_not_change_permissions(self):
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as directory:
            plugin = self.controller_fixture(directory)
            player = self.controller_player()
            plugin.server.online_players = [player]
            console = FakeConsole()
            command = FakeCommand("botcontrollers")
            plugin.on_command(console, command, ["add", player.name])
            with patch("endbot_core.controller_policy.os.replace", side_effect=OSError("disk failure")):
                plugin.on_command(console, command, ["remove", player.name])
            self.assertTrue(plugin._allows_control(player))
            self.assertEqual(player.permission_values[-1], ("endbot.command.control", True))
            self.assertIn("disk failure", console.messages[-1])

    def test_revocation_overrides_legacy_uuid_and_xuid_permissions(self):
        from endbot_core.authorization import AllowedPlayers

        with tempfile.TemporaryDirectory() as directory:
            plugin = self.controller_fixture(directory)
            player = self.controller_player()
            plugin._allowed_players = AllowedPlayers.from_config(
                {"allowed-uuids": [str(player.unique_id)], "allowed-xuids": [player.xuid]}
            )
            self.assertTrue(plugin._allows_control(player))
            plugin.server.online_players = [player]
            plugin.on_command(FakeConsole(), FakeCommand("botcontrollers"), ["remove", player.name])
            self.assertFalse(plugin._allows_control(player))
            player.name = "Renamed"
            plugin._join_controller(player)
            self.assertFalse(plugin._allows_control(player))
            player.xuid = ""
            self.assertFalse(plugin._allows_control(player))

    def test_request_result_reaches_file_and_cannot_be_dispatched_by_player(self):
        import json
        from uuid import uuid4

        with tempfile.TemporaryDirectory() as directory:
            plugin = self.controller_fixture(directory)
            plugin._controller_requests_directory = Path(directory)
            request_id, session = str(uuid4()), str(uuid4())
            path = Path(directory) / f"{request_id}.dispatch.json"
            path.write_text(
                json.dumps(
                    {"version": 1, "id": request_id, "session": session, "operation": "add", "target": "Offline Player"}
                )
            )
            command = FakeCommand("botcontrollers")
            plugin.on_command(self.controller_player(), command, ["request", request_id])
            self.assertTrue(path.exists())
            self.assertFalse(plugin._controller_policy.path.exists())
            plugin.on_command(FakeConsole(), command, ["request", request_id])
            result = json.loads((Path(directory) / f"{request_id}.response.json").read_text())
            self.assertTrue(result["ok"])
            self.assertEqual(result["id"], request_id)
            self.assertEqual(result["session"], session)
            self.assertEqual(result["entries"][0]["state"], "pending")
            self.assertFalse(path.exists())

    def test_failed_management_returns_failure_in_correlated_reply(self):
        import json
        from uuid import uuid4

        with tempfile.TemporaryDirectory() as directory:
            plugin = self.controller_fixture(directory)
            plugin._controller_requests_directory = Path(directory)
            request_id = str(uuid4())
            (Path(directory) / f"{request_id}.dispatch.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "id": request_id,
                        "session": str(uuid4()),
                        "operation": "remove",
                        "target": "Missing",
                    }
                )
            )
            plugin.on_command(FakeConsole(), FakeCommand("botcontrollers"), ["request", request_id])
            result = json.loads((Path(directory) / f"{request_id}.response.json").read_text())
            self.assertFalse(result["ok"])
            self.assertIn("Unknown offline controller", result["messages"][0])

    def test_report_matches_effective_control_and_explains_revocation(self):
        with tempfile.TemporaryDirectory() as directory:
            plugin = self.controller_fixture(directory)
            player = self.controller_player()
            plugin.server.online_players = [player]
            plugin._controller_operation(["add", player.name])
            row = plugin._controller_report()[0]
            self.assertEqual((row["state"], row["reason"]), ("allowed", "runtime grant"))
            plugin._controller_operation(["remove", player.name])
            row = plugin._controller_report()[0]
            self.assertEqual((row["state"], row["reason"]), ("revoked", "revoked XUID"))
            self.assertFalse(plugin._allows_control(player))
            plugin._controller_operation(["add", "Pending"])
            self.assertIn("pending", [entry["state"] for entry in plugin._controller_report()])

    def test_offline_doctor_report_and_plugin_report_agree(self):
        cli_source = str(Path(__file__).resolve().parents[3] / "cli/endbot/src")
        sys.path.insert(0, cli_source)
        self.addCleanup(sys.path.remove, cli_source)
        from endbot_cli.controller_status import load_policy, saved_report
        from endbot_cli.controllers import load_controllers
        from endbot_core.enrollment import ControllerEnrollment

        with tempfile.TemporaryDirectory() as directory:
            plugin = self.controller_fixture(directory)
            binding_path = Path(directory) / "bindings.json"
            plugin._controllers = ControllerEnrollment(("Owner", "Pending"), binding_path)
            player = self.controller_player()
            plugin._join_controller(player)
            for operation in ("remove", "add", "remove"):
                plugin._controller_operation([operation, "Owner"])
                plugin_rows = [
                    {key: entry[key] for key in ("name", "state", "reason")} for entry in plugin._controller_report()
                ]
                doctor_rows = saved_report(
                    plugin._controllers.gamertags,
                    load_controllers(binding_path),
                    load_policy(plugin._controller_policy.path),
                    {},
                )
                self.assertEqual(doctor_rows, plugin_rows)


if __name__ == "__main__":
    unittest.main()
