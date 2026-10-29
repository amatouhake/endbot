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
            plugin.config = {"endbotd": {"token-file": "endbotd.token", "port": 1}}
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

    def linked_plugin(self, answer=None):
        """A plugin whose link records submissions; ``answer`` is the Future each returns."""
        from concurrent.futures import Future

        plugin = self.module.EndbotPlugin()
        plugin.logger = types.SimpleNamespace(info=lambda message: None, warning=lambda message: None)
        submissions = []

        def submit(root, sender, *, argv=None, operation=None, guard=None):
            submissions.append((root, sender, argv, guard))
            if answer is not None:
                return answer
            future = Future()
            future.set_result({"accepted": True, "code": "ok"})
            return future

        plugin._link = types.SimpleNamespace(submit=submit)
        return plugin, submissions

    def player(self, locale="en_US"):
        from uuid import UUID

        player = FakeSender()
        player.name, player.xuid, player.locale = "Owner", "2535412345678901", locale
        player.unique_id = UUID("11111111-2222-4333-8444-555555555555")
        return player

    def test_commands_forward_to_endbotd_with_only_who_typed_them(self) -> None:
        plugin, submissions = self.linked_plugin()
        console = FakeConsole()
        self.assertTrue(plugin.on_command(console, FakeCommand("bot"), ["ping"]))
        player = self.player()
        self.assertTrue(plugin.on_command(player, FakeCommand("bot"), ["Alice", "stop"]))
        self.assertFalse(plugin.on_command(console, FakeCommand("other"), []))
        self.assertEqual(console.messages + player.messages, [])
        self.assertEqual(submissions, [
            ("bot", Principal(), ["ping"], None),
            ("bot", Principal("player", language="en", player_id=str(player.unique_id)), ["Alice", "stop"], None),
        ])

    def test_group_management_forwards_under_the_control_permission(self) -> None:
        plugin, submissions = self.linked_plugin()
        self.assertEqual(plugin.commands["botgroup"]["permissions"], ["endbot.command.control"])
        usages = plugin.commands["botgroup"]["usages"]
        for action in ("list", "help", "create", "delete", "add", "remove"):
            self.assertTrue(any(usage.startswith(f"/botgroup ({action})<action: EndbotGroup") for usage in usages))
        self.assertIn("/botgroup <action: string> <arguments: message>", usages)
        self.assertFalse(any("EndbotGroupMembers" in usage for usage in usages))
        self.assertIn("/botgroup (list)<action: EndbotGroupList> [group: message]", usages)
        self.assertTrue(plugin.on_command(FakeConsole(), FakeCommand("botgroup"), ["create", "#miners"]))
        self.assertEqual(submissions, [("botgroup", Principal(), ["create", "#miners"], None)])
        # Something that is neither the console nor a player never reaches endbotd.
        unauthorized = FakeSender()
        plugin.on_command(unauthorized, FakeCommand("botgroup"), ["delete", "#miners"])
        self.assertEqual(len(submissions), 1)
        self.assertIn("do not have Bot control permission", unauthorized.messages[-1])

    def test_controller_management_stays_console_only(self) -> None:
        plugin, submissions = self.linked_plugin()
        player = self.player()
        player.has_permission = lambda permission: True
        plugin.on_command(player, FakeCommand("botcontrollers"), ["add", "Other"])
        self.assertIn("only from the server console", player.messages[-1])
        self.assertEqual(submissions, [])
        plugin.on_command(FakeConsole(), FakeCommand("botcontrollers"), ["request", "id"])
        self.assertEqual(submissions, [("botcontrollers", Principal(), ["request", "id"], None)])

    def test_commands_are_refused_while_endbotd_is_down(self) -> None:
        from endbot_core.link import LinkError

        plugin, _ = self.linked_plugin()

        def down(*args, **options):
            raise LinkError("unavailable", "Endbot service is restarting")

        plugin._link = types.SimpleNamespace(submit=down)
        player = self.player(locale="ja_JP")
        plugin._submit_bot(player, ["Alice", "stop"])
        self.assertEqual(player.messages, ["Endbot: Endbot サービスを再起動しています"])
        plugin._link = None
        console = FakeConsole()
        plugin._submit_bot(console, ["ping"])
        self.assertEqual(console.messages, ["Endbot: Endbot service is restarting"])

    def test_a_command_lost_with_the_link_tells_whoever_typed_it(self) -> None:
        from concurrent.futures import Future

        from endbot_core.link import LinkError

        answer = Future()
        plugin, _ = self.linked_plugin(answer)
        posted = []
        plugin._server_thread = types.SimpleNamespace(post=posted.append)
        player = self.player()
        plugin.server = types.SimpleNamespace(get_player=lambda identifier: player)
        plugin._submit_bot(player, ["Alice", "stop"])
        answer.set_exception(LinkError("unavailable", "link closed"))
        for callback in posted:
            callback()
        self.assertEqual(player.messages, ["Endbot: Endbot service is restarting"])

    def test_reply_language_follows_endbotd_setting_and_client_locale(self) -> None:
        plugin, _ = self.linked_plugin()
        player = self.player(locale="ja_JP")
        self.assertEqual(plugin._language(player), "ja")
        plugin._apply_link_config({"language": "en"})
        self.assertEqual(plugin._language(player), "en")
        plugin._apply_link_config({"language": "ja"})
        self.assertEqual(plugin._language(self.player()), "ja")
        # The console stays English, like the server log.
        self.assertEqual(plugin._language(FakeConsole()), "en")
        warnings = []
        plugin.logger = types.SimpleNamespace(info=print, warning=warnings.append)
        plugin._apply_link_config({"language": "fr"})
        self.assertEqual(plugin._language_setting, "ja")
        self.assertTrue(warnings)

    def test_link_config_sets_the_hotbar_cadence(self) -> None:
        from unittest.mock import patch

        plugin, _ = self.linked_plugin()
        plugin._apply_link_config({"hotbar": {"hotbar-sync-interval-ticks": 2, "hotbar-sync-resend-ticks": 40}})
        self.assertEqual((plugin._hotbar_interval, plugin._hotbar_publisher.resend_ticks), (2, 40))
        plugin.server = types.SimpleNamespace(online_players=[])
        with patch.object(plugin, "_publish_hotbars") as publish:
            for _ in range(8):
                plugin._hotbar_ticker()
        self.assertEqual(publish.call_count, 3)  # the first period still runs at the default 4 ticks

    def test_botscript_carries_origin_and_a_form_guard_is_copied_when_sent(self) -> None:
        from endbot_core.script_forms import ScriptGuard
        from endbot_core.wire_views import decode_view

        plugin, submissions = self.linked_plugin()
        player = self.player(locale="ja_JP")
        player.location = types.SimpleNamespace(x=1, y=64, z=2, yaw=90, pitch=0,
                                                dimension=types.SimpleNamespace(name="Overworld"))
        guard = ScriptGuard("r1", "test", values={"name": "Alice"})
        plugin._submit_bot(player, ["save"], command="botscript", group_guard=guard)
        guard.values["name"] = "Changed"
        root, principal, _argv, wire = submissions[0]
        self.assertEqual((root, principal.kind, principal.language), ("botscript", "player", "ja"))
        self.assertEqual(principal.origin, {"x": 1.0, "y": 64.0, "z": 2.0, "yaw": 90.0, "pitch": 0.0,
                                            "dimension": "Overworld"})
        self.assertEqual(decode_view(wire).values, {"name": "Alice"})
        self.assertIsNone(plugin._principal(player).origin)
        self.assertEqual(plugin._principal(FakeConsole()), Principal())
        # The console keeps a form guard it was given (the scripting probe saves this way).
        plugin._submit_bot(FakeConsole(), ["save"], command="botscript", group_guard=guard)
        self.assertEqual(decode_view(submissions[1][3]).values, {"name": "Changed"})


if __name__ == "__main__":
    unittest.main()
