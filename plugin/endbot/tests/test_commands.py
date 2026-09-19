import unittest

from endstone_endbot.commands import BotCommandService, parse_command


class BotCommandServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = BotCommandService()

    def test_ping_returns_pong(self) -> None:
        result = self.service.execute(["ping"])
        self.assertTrue(result.handled)
        self.assertEqual(result.message, "Endbot: pong")

    def test_unknown_or_missing_action_is_not_handled(self) -> None:
        for arguments in (["status"], ["ping", "extra"]):
            with self.subTest(arguments=arguments):
                result = self.service.execute(arguments)
                self.assertTrue(result.handled)
                self.assertIn("Endbot:", result.message)

    def test_empty_command_shows_help_page_one(self) -> None:
        result = self.service.execute([])
        self.assertIn("Endbot help page 1 of", result.message)
        bare = parse_command([])
        self.assertEqual(bare.parameters, {"page": 1})
        self.assertEqual(parse_command(["help"]).parameters, {"page": 1})

    def test_help_pages_are_paginated_like_vanilla_help(self) -> None:
        from endstone_endbot.commands import HELP_PAGES

        for page in range(1, len(HELP_PAGES) + 1):
            with self.subTest(page=page):
                command = parse_command(["help", str(page)])
                self.assertEqual(command.parameters, {"page": page})
                result = self.service.execute(["help", str(page)])
                self.assertIn(f"Endbot help page {page} of", result.message)
                self.assertGreater(len(result.messages), 3)
        page_one = self.service.execute(["help"]).messages
        self.assertIn("spawn", "\n".join(page_one))
        page_two = self.service.execute(["help", "2"]).messages
        self.assertIn("move", "\n".join(page_two))
        page_three = self.service.execute(["help", "3"]).messages
        self.assertIn("interact", "\n".join(page_three))

    def test_help_topic_shows_supported_syntaxes(self) -> None:
        result = self.service.execute(["help", "move"])
        self.assertIn("/bot <name> move forward|backward|left|right|stop", result.messages)
        teleport = self.service.execute(["help", "tp"])
        self.assertIn("/bot <name> tp <player>", teleport.messages)
        self.assertEqual(
            self.service.execute(["help", "teleport"]).messages,
            teleport.messages,
        )
        upper = self.service.execute(["help", "Move"])
        self.assertEqual(upper.messages, result.messages)

    def test_help_rejects_unknown_topics_and_pages(self) -> None:
        for arguments in (["help", "dance"], ["help", "0"], ["help", "9"], ["help", "1", "extra"]):
            with self.subTest(arguments=arguments):
                result = self.service.execute(arguments)
                self.assertTrue(result.handled)
                self.assertIn("Endbot:", result.message)

    def test_help_advanced_remains_a_silent_compatibility_alias(self) -> None:
        from endstone_endbot.commands import HELP_PAGES

        command = parse_command(["help", "advanced"])
        self.assertEqual(command.parameters, {"page": len(HELP_PAGES)})
        result = self.service.execute(["help", "advanced"])
        self.assertIn("Endbot help page", result.message)

    def test_root_and_named_default_forms_remain_parseable(self) -> None:
        self.assertEqual(parse_command(["ping"]).operation, "ping")
        self.assertEqual(parse_command(["list"]).operation, "list")
        self.assertEqual(parse_command(["help"]).operation, "help")
        self.assertEqual(parse_command(["help", "2"]).parameters, {"page": 2})
        for operation in ("spawn", "jump", "attack", "use", "hotbar", "drop"):
            with self.subTest(operation=operation):
                self.assertEqual(parse_command(["Alice", operation]).name, "Alice")

    def test_every_documented_named_form_parses(self) -> None:
        cases = [
            (["Alice", "status"], "status"),
            (["Alice", "resume"], "resume"),
            (["Alice", "reconnect"], "reconnect"),
            (["Alice", "despawn"], "despawn"),
            (["Alice", "forget"], "forget"),
            (["Alice", "stop"], "stop"),
            (["Alice", "spawn"], "spawn"),
            (["Alice", "spawn", "at", "1", "64", "2"], "spawn"),
            (["Alice", "rename", "Bob"], "rename"),
            (["Alice", "tp", "me"], "teleport"),
            (["Alice", "tp", "Steve"], "teleport"),
            (["Alice", "tp", "100", "64", "-20"], "teleport"),
            (["Alice", "tp", "100", "64", "-20", "90", "0"], "teleport"),
            (["Alice", "tp", "100", "64", "-20", "facing", "Steve"], "teleport"),
            (["Alice", "tp", "in", "nether", "100", "64", "-20"], "teleport"),
            (["Alice", "move", "forward"], "move"),
            (["Alice", "move", "stop"], "move"),
            (["Alice", "look", "90", "0"], "look"),
            (["Alice", "look", "at", "1", "64", "2"], "look"),
            (["Alice", "jump"], "action"),
            (["Alice", "attack", "continuous"], "action"),
            (["Alice", "use", "interval", "20"], "action"),
            (["Alice", "sprint", "on"], "flag"),
            (["Alice", "sneak", "off"], "flag"),
            (["Alice", "hotbar"], "hotbar"),
            (["Alice", "hotbar", "9"], "hotbar"),
            (["Alice", "interact", "1", "64", "2", "up"], "interact"),
            (["Alice", "drop"], "drop"),
            (["Alice", "drop", "stack"], "drop"),
        ]
        for arguments, operation in cases:
            with self.subTest(arguments=arguments):
                self.assertEqual(parse_command(arguments).operation, operation)

    def test_lifecycle_and_action_grammar(self) -> None:
        self.assertEqual(parse_command(["Alice", "spawn"]).operation, "spawn")
        placement = parse_command(["Alice", "spawn", "at", "~1", "64", "-2", "facing", "90", "0", "in", "nether"])
        self.assertEqual(placement.parameters["dimension"], "nether")
        self.assertEqual(placement.parameters["coordinates"][0], ("relative", 1.0))
        attack = parse_command(["Alice", "attack", "interval", "20"])
        self.assertEqual(attack.parameters, {"action": "attack", "mode": "interval", "intervalTicks": 20})
        self.assertEqual(parse_command(["Alice", "attack"]).parameters["mode"], "once")

    def test_teleport_grammar(self) -> None:
        self.assertEqual(parse_command(["Alice", "tp", "me"]).parameters, {"target": "me"})
        command = parse_command(["Alice", "tp", "in", "end", "0", "80", "0", "facing", "0", "80", "10"])
        self.assertEqual(command.parameters["dimension"], "end")
        self.assertEqual(command.parameters["facingCoordinates"], [0.0, 80.0, 10.0])

    def test_hotbar_interact_and_drop_grammar(self) -> None:
        self.assertEqual(parse_command(["Alice", "hotbar"]).parameters, {})
        self.assertEqual(parse_command(["Alice", "hotbar", "9"]).parameters, {"slot": 9})
        with self.assertRaisesRegex(ValueError, "1 to 9"):
            parse_command(["Alice", "hotbar", "0"])
        interaction = parse_command(["Alice", "interact", "~1", "64", "-2", "up"])
        self.assertEqual(interaction.parameters["coordinates"][0], ("relative", 1.0))
        self.assertEqual(interaction.parameters["face"], 1)
        self.assertEqual(parse_command(["Alice", "drop"]).parameters, {"stack": False})
        self.assertEqual(parse_command(["Alice", "drop", "stack"]).parameters, {"stack": True})

    def test_endstone_greedy_message_arguments_are_flattened(self) -> None:
        spawn = parse_command(["Alice", "spawn", "at 1 64 2 facing 90 5 in nether"])
        self.assertEqual(spawn.parameters["rotation"], [90.0, 5.0])
        self.assertEqual(spawn.parameters["dimension"], "nether")
        teleport = parse_command(["Alice", "tp", "1 64 2 facing Steve"])
        self.assertEqual(teleport.parameters["facingTarget"], "Steve")


if __name__ == "__main__":
    unittest.main()
