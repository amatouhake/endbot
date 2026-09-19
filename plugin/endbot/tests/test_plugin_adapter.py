import importlib
import sys
import types
import unittest


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
        self.assertIn("/bot (ping|list)<command: EndbotRoot>", usages)
        self.assertIn("/bot (help)<command: EndbotHelp> (advanced)[topic: EndbotHelpTopic]", usages)
        self.assertTrue(any("reconnect" in usage and "EndbotNoArgs" in usage for usage in usages))
        self.assertTrue(any("EndbotDirection" in usage for usage in usages))
        self.assertTrue(any("EndbotBlockFace" in usage and "block_pos" in usage for usage in usages))
        self.assertFalse(any("<direction: string>" in usage for usage in usages))
        self.assertEqual(plugin_class.commands["bot"]["permissions"], ["endbot.command.control"])
        self.assertIs(plugin_class.permissions["endbot.command.control"]["default"], False)

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


if __name__ == "__main__":
    unittest.main()
