import unittest

from endstone_endbot.commands import BotCommandService


class BotCommandServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = BotCommandService()

    def test_ping_returns_pong(self) -> None:
        result = self.service.execute(["ping"])
        self.assertTrue(result.handled)
        self.assertEqual(result.message, "Endbot: pong")

    def test_unknown_or_missing_action_is_not_handled(self) -> None:
        for arguments in ([], ["status"], ["ping", "extra"]):
            with self.subTest(arguments=arguments):
                self.assertFalse(self.service.execute(arguments).handled)


if __name__ == "__main__":
    unittest.main()

