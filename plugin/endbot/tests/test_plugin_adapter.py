import importlib
import re
import sys
import tempfile
import types
import unittest
from pathlib import Path


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
        event.PlayerJoinEvent = type("PlayerJoinEvent", (), {})
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
        cls.module = importlib.import_module("endstone_endbot.plugin")

    def test_metadata_registers_narrow_command_and_permission(self) -> None:
        plugin_class = self.module.EndbotPlugin
        self.assertEqual(plugin_class.api_version, "0.11")
        usages = plugin_class.commands["bot"]["usages"]
        self.assertIn("/bot [command: string] [topic: string]", usages)
        self.assertIn(
            "/bot (ping|list|help)<command: EndbotRoot>",
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
                "interact",
                "drop",
                "stop",
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
        self.assertTrue(any("reconnect" in usage and "EndbotNoArgs" in usage for usage in usages))
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
            attachments: list[tuple[str, bool]] = []
            console = types.SimpleNamespace(
                add_attachment=lambda owner, permission, value: attachments.append((permission, value))
            )
            plugin.server = types.SimpleNamespace(
                command_sender=console,
                scheduler=types.SimpleNamespace(run_task=lambda owner, task: None),
                get_player=lambda identifier: None,
            )
            plugin.register_events = lambda listener: None
            plugin.on_enable()
            self.addCleanup(plugin.on_disable)

            self.assertEqual(attachments, [("endbot.command.control", True)])
            self.assertIs(self.module.EndbotPlugin.permissions["endbot.command.control"]["default"], False)

    def test_adapter_sends_pong(self) -> None:
        plugin = self.module.EndbotPlugin()
        sender = FakeSender()
        submissions = []
        plugin._command_runner = types.SimpleNamespace(
            submit=lambda args, target: submissions.append((args, target)) or True,
        )
        self.assertTrue(plugin.on_command(sender, FakeCommand("bot"), ["ping"]))
        self.assertEqual(sender.messages, [])
        self.assertEqual(submissions, [(["ping"], sender)])

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


if __name__ == "__main__":
    unittest.main()
