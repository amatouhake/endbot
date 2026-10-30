import unittest

from endbot_core.commands import BotCommand, parse_command
from endbot_core.limits import BotLimits
from plain_service import PlainCommandService


class BotCommandServiceTests(unittest.TestCase):
    def test_a_bare_target_opens_its_menu(self):
        self.assertEqual(parse_command(["Alice"]), BotCommand("menu", "Alice"))
        self.assertEqual(parse_command(["#miners"]), BotCommand("menu", "#miners"))
        self.assertEqual(parse_command(["menu"]), BotCommand("menu"))
        self.assertEqual(parse_command([]), BotCommand("menu", parameters={"default": True}))
        self.assertEqual(parse_command(["ping"]).operation, "ping")

    def test_bot_limits_default_to_one_reserved_player_slot(self):
        self.assertEqual(BotLimits.from_config(None), BotLimits(0, 1))
        self.assertEqual(BotLimits.from_config({}), BotLimits(0, 1))
        self.assertEqual(BotLimits.from_config({"max-bots": 8, "reserved-player-slots": 2}), BotLimits(8, 2))
        self.assertEqual(BotLimits.from_config({"max-bots-per-controller": 3}), BotLimits(0, 1, 3))
        self.assertFalse(BotLimits(0, 0).enabled)
        self.assertTrue(BotLimits(1, 0).enabled)
        for table in (
            {"max-bots": -1},
            {"max-bots": True},
            {"max-bots": "8"},
            {"reserved-player-slots": -1},
            {"reserved-player-slots": "2"},
            {"max-bots-per-controller": -1},
            {"max-bots-per-controller": True},
            {"max-bots-per-controller": "2"},
            {"max-bot": 1},
            {"max-bots-per-controllr": 1},
            [],
        ):
            with self.subTest(table=table), self.assertRaises(ValueError):
                BotLimits.from_config(table)

    def test_timed_use_keeps_its_meaning(self):
        self.assertEqual(
            parse_command(["Alice", "use", "for", "20"]).parameters,
            {"action": "use", "mode": "continuous", "durationTicks": 20},
        )
        for value in ("0", "72001", "1.5", "true"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_command(["Alice", "use", "for", value])
        self.assertEqual(parse_command(["Alice", "use"]).parameters, {"action": "use", "mode": "once"})

    def test_for_is_a_duration_that_implies_continuous(self):
        cases = {
            ("jump", "for", "20"): {"action": "jump", "mode": "continuous", "durationTicks": 20},
            ("attack", "continuous", "for", "20"): {"action": "attack", "mode": "continuous", "durationTicks": 20},
            ("jump", "interval", "20", "for", "100"): {
                "action": "jump", "mode": "interval", "intervalTicks": 20, "durationTicks": 100,
            },
            ("use", "FOR", "100", "interval", "20"): {
                "action": "use", "mode": "interval", "intervalTicks": 20, "durationTicks": 100,
            },
            ("use", "hold", "30"): {"action": "use", "mode": "once", "holdTicks": 30},
            ("use", "hold", "30", "for", "200"): {
                "action": "use", "mode": "continuous", "holdTicks": 30, "durationTicks": 200,
            },
            ("use", "continuous", "hold", "30"): {"action": "use", "mode": "continuous", "holdTicks": 30},
            ("use", "hold", "30", "interval", "40", "for", "200"): {
                "action": "use", "mode": "interval", "holdTicks": 30, "intervalTicks": 40, "durationTicks": 200,
            },
        }
        for arguments, parameters in cases.items():
            with self.subTest(arguments=arguments):
                self.assertEqual(parse_command(["Alice", *arguments]).parameters, parameters)
        self.assertEqual(
            parse_command(["Alice", "move", "forward", "for", "40"]).parameters,
            {"direction": "forward", "durationTicks": 40},
        )
        self.assertEqual(
            parse_command(["Alice", "destroy", "for", "200"]).parameters, {"mode": "continuous", "durationTicks": 200}
        )
        self.assertEqual(
            parse_command(["Alice", "destroy", "continuous", "for", "200"]).parameters,
            {"mode": "continuous", "durationTicks": 200},
        )
        for arguments, message in (
            (["use", "for"], "accepts"),
            (["jump", "once", "for", "20"], "once runs one time"),
            (["jump", "stop", "for", "20"], "stop takes no ticks"),
            (["jump", "continuous", "interval", "20"], "replaces once/continuous"),
            (["jump", "for", "20", "for", "30"], "accepts"),
            (["jump", "hold", "20"], "accepts"),
            (["use", "hold", "30", "interval", "30"], "longer than hold"),
            (["use", "hold", "0"], "Hold must be between"),
            (["attack", "for", "72001"], "Duration must be between"),
            (["move", "stop", "for", "20"], "move accepts"),
            (["move", "forward", "for"], "move accepts"),
            (["move", "forward", "for", "x"], "Duration must be an integer"),
            (["destroy", "once", "for", "20"], "one block has no duration"),
            (["destroy", "1", "64", "2", "for", "20"], "one block has no duration"),
        ):
            with self.subTest(arguments=arguments), self.assertRaisesRegex(ValueError, message):
                parse_command(["Alice", *arguments])

    def test_count_is_a_block_budget_that_implies_continuous(self):
        cases = {
            ("destroy", "count", "64"): {"mode": "continuous", "blockCount": 64},
            ("destroy", "continuous", "count", "64"): {"mode": "continuous", "blockCount": 64},
            ("destroy", "COUNT", "1"): {"mode": "continuous", "blockCount": 1},
            ("destroy", "for", "200", "count", "64"): {
                "mode": "continuous", "durationTicks": 200, "blockCount": 64,
            },
            ("destroy", "count", "64", "for", "200"): {
                "mode": "continuous", "durationTicks": 200, "blockCount": 64,
            },
            ("destroy", "continuous", "count", "64", "for", "200"): {
                "mode": "continuous", "durationTicks": 200, "blockCount": 64,
            },
        }
        for arguments, parameters in cases.items():
            with self.subTest(arguments=arguments):
                self.assertEqual(parse_command(["Alice", *arguments]).parameters, parameters)
        for arguments, message in (
            (["destroy", "once", "count", "5"], "one block has no duration"),
            (["destroy", "stop", "count", "5"], "one block has no duration"),
            (["destroy", "1", "64", "2", "count", "5"], "one block has no duration"),
            (["destroy", "count"], "destroy accepts"),
            (["destroy", "count", "5", "count", "6"], "destroy accepts"),
            (["destroy", "count", "5", "jump"], "destroy accepts"),
            (["destroy", "count", "0"], "Block count must be between"),
            (["destroy", "count", "72001"], "Block count must be between"),
            (["destroy", "count", "1.5"], "Block count must be an integer"),
        ):
            with self.subTest(arguments=arguments), self.assertRaisesRegex(ValueError, message):
                parse_command(["Alice", *arguments])

    def test_destroy_modes_do_not_take_coordinates(self):
        # Regression: popping the leading mode must not turn `stop 1 64 2` into
        # a coordinate command that would start breaking a block.
        for tail in (
            ["once", "1", "64", "2"],
            ["continuous", "1", "64", "2"],
            ["stop", "1", "64", "2"],
            ["once", "1", "64", "2", "up"],
            ["stop", "1", "64", "2", "up"],
            ["continuous", "~", "~-1", "~", "up"],
        ):
            with self.subTest(tail=tail), self.assertRaisesRegex(ValueError, "destroy accepts"):
                parse_command(["Alice", "destroy", *tail])

    def setUp(self) -> None:
        self.service = PlainCommandService()

    def test_ping_returns_pong(self) -> None:
        result = self.service.execute(["ping"])
        self.assertTrue(result.handled)
        self.assertEqual(result.message, "Endbot: pong")

    def test_radius_query_override_reset_and_bounds(self) -> None:
        self.assertEqual(parse_command(["Alice", "radius"]).parameters, {})
        self.assertEqual(parse_command(["Alice", "radius", "12"]).parameters, {"value": 12})
        self.assertEqual(parse_command(["Alice", "radius", "DEFAULT"]).parameters, {"value": "default"})
        for value in ("1", "33", "2.5", "auto", "true"):
            with self.subTest(value=value):
                result = self.service.execute(["Alice", "radius", value])
                self.assertIn("Endbot:", result.message)
                self.assertNotIn("runtime control is not configured", result.message)
        detail = self.service.execute(["help", "radius"])
        self.assertIn("/bot <name> radius <2-32|default>", detail.messages)

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
        self.assertEqual(bare.operation, "menu")
        self.assertEqual(bare.parameters, {"default": True})
        self.assertEqual(parse_command(["help"]).parameters, {"page": 1})

    def test_help_pages_are_paginated_like_vanilla_help(self) -> None:
        from endbot_core.commands import HELP_PAGES

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

    def test_help_and_replies_are_colored_for_players(self) -> None:
        from endbot_core.commands import (
            DIM,
            ERROR,
            HEADING,
            HELP_TOPICS,
            PREFIX,
            RESET,
            SYNTAX,
            BotCommandService,
            color_syntax,
            reply,
            strip_colors,
        )

        service = BotCommandService()
        page = service.execute(["help"]).messages
        self.assertTrue(page[0].startswith(HEADING))
        self.assertIn(color_syntax("/bot <name> spawn"), "\n".join(page))
        detail = service.execute(["help", "jump"]).messages
        self.assertEqual(detail[0], f"{HEADING}Endbot help: jump{RESET}")
        self.assertEqual(
            detail[1],
            f"{SYNTAX}/bot {DIM}<name>{SYNTAX} jump {DIM}[once|continuous|interval <ticks>|stop]{RESET}",
        )
        # Descriptions stay in the default color.
        self.assertEqual(detail[1 + len(HELP_TOPICS["jump"].syntaxes) :], HELP_TOPICS["jump"].details)
        self.assertEqual(service.execute(["ping"]).message, f"{PREFIX}Endbot:{RESET} pong")
        error = service.execute(["Alice", "move", "sideways"]).message
        self.assertTrue(error.startswith(f"{PREFIX}Endbot: Alice:{RESET} {ERROR}"))
        self.assertEqual(reply("done", "Alice"), f"{PREFIX}Endbot: Alice:{RESET} done")
        self.assertEqual(strip_colors(reply("done", "Alice", ERROR)), "Endbot: Alice: done")
        self.assertEqual(strip_colors("§6a§lb§rc§"), "abc")

    def test_help_rejects_unknown_topics_and_pages(self) -> None:
        for arguments in (["help", "dance"], ["help", "0"], ["help", "9"], ["help", "1", "extra"]):
            with self.subTest(arguments=arguments):
                result = self.service.execute(arguments)
                self.assertTrue(result.handled)
                self.assertIn("Endbot:", result.message)

    def test_help_advanced_remains_a_silent_compatibility_alias(self) -> None:
        from endbot_core.commands import HELP_PAGES

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
        self.assertEqual(parse_command(["Alice", "destroy"]).parameters, {})
        self.assertEqual(parse_command(["Alice", "destroy", "continuous"]).parameters, {"mode": "continuous"})
        self.assertEqual(parse_command(["Alice", "destroy", "stop"]).parameters, {"mode": "stop"})
        # Same mode words as jump/attack/use; once is the default and interval is rejected clearly.
        self.assertEqual(parse_command(["Alice", "destroy", "ONCE"]).parameters, {})
        with self.assertRaisesRegex(ValueError, "no interval mode"):
            parse_command(["Alice", "destroy", "interval", "20"])
        # A single unknown word is a block type; execution reports an unknown type.
        self.assertEqual(parse_command(["Alice", "destroy", "start"]).parameters, {"blockType": "minecraft:start"})
        mining = parse_command(["Alice", "destroy", "~", "~-1", "-2", "up"])
        self.assertEqual(mining.parameters["face"], 1)
        self.assertEqual(mining.parameters["coordinates"][1], ("relative", -1.0))
        for tail in (["1", "64"], ["1", "64", "2", "badface"], ["nan", "64", "2"]):
            with self.assertRaises(ValueError):
                parse_command(["Alice", "destroy", *tail])
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
