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

    def test_empty_command_shows_progressive_help(self) -> None:
        result = self.service.execute([])
        self.assertIn("quick start", result.message)

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
