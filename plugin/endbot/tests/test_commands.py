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


if __name__ == "__main__":
    unittest.main()
