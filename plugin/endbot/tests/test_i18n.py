import ast
import string
import unittest
from pathlib import Path

from plain_service import PlainCommandService
from test_command_service import FakeControl, FakeWorld

from endstone_endbot import forms
from endstone_endbot.commands import (
    DESTROY_SINGLE_BLOCK,
    DESTROY_USAGE,
    HELP_PAGE_TITLES,
    HELP_TOPICS,
    MINING_REASONS,
    help_detail,
    help_page,
    strip_colors,
)
from endstone_endbot.events import render
from endstone_endbot.i18n import JA, Text, language_setting, localize_error, resolve_language, tr
from endstone_endbot.inventory_sync import EQUIPMENT_SLOTS
from endstone_endbot.script_forms import TEMPLATES

SOURCE = Path(__file__).resolve().parents[1] / "src" / "endstone_endbot"

# Callables whose argument at this index is an English template.
TEMPLATE_ARGUMENTS = {
    "tr": 1,
    "Text": 0,
    "LocalizedError": 0,
    "CommandSyntaxError": 0,
    "FormResponseError": 0,
    "say": 0,
    "label": 0,
    "category": 0,
    "_mining_notice": 2,
}
# Plain errors raised with fixed English text that reaches players.
PLAYER_ERROR_FILES = ("commands.py", "dispatch.py", "world.py", "groups.py")
# Templates reached through variables rather than literals.
INDIRECT_TEMPLATES = (
    *TEMPLATES,
    *(label for label, _ in EQUIPMENT_SLOTS),
    "online",
    "offline",
    "connecting",
    "reconnecting",
    "failed",
    *(badge for _, badge in forms.TRANSITIONAL_STATES.values()),
    *(choice for choice in forms.FLAG_CHOICES if choice not in {"on", "off"}),
    forms.TELEPORT_COORDINATES,
    "one item",
    "whole stack",
    DESTROY_USAGE,
    DESTROY_SINGLE_BLOCK,
    *HELP_PAGE_TITLES,
    *(topic.summary for topic in HELP_TOPICS.values()),
    *(detail for topic in HELP_TOPICS.values() for detail in topic.details),
    *MINING_REASONS.values(),
)

# Bedrock's chat font lacks many symbols (arrows rendered as boxes). Japanese
# text keeps to kana, kanji, ASCII and the ideographic comma and full stop.
ALLOWED_RANGES = ((0x20, 0x7E), (0x3001, 0x3002), (0x3041, 0x309F), (0x30A0, 0x30FF), (0x4E00, 0x9FFF))


def _placeholders(text: str) -> set[str]:
    return {field for _, field, _, _ in string.Formatter().parse(text) if field is not None}


def _literals(node) -> set[str]:
    """String literals a template argument can evaluate to."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value:
        return {node.value}
    if isinstance(node, ast.IfExp):
        return _literals(node.body) | _literals(node.orelse)
    if isinstance(node, ast.BoolOp):
        return set().union(*(_literals(value) for value in node.values))
    return set()


def _source_templates() -> set[str]:
    templates = set()
    for path in SOURCE.glob("*.py"):
        if path.name == "i18n.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call):
                name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
                index = TEMPLATE_ARGUMENTS.get(name)
                if index is not None and len(node.args) > index:
                    templates.update(_literals(node.args[index]))
            if isinstance(node, ast.Raise) and path.name in PLAYER_ERROR_FILES:
                call = node.exc
                if (
                    isinstance(call, ast.Call)
                    and getattr(call.func, "id", None) in {"ValueError", "RuntimeError"}
                    and call.args
                    and isinstance(call.args[0], ast.Constant)
                ):
                    templates.add(call.args[0].value)
    return templates


class CatalogTests(unittest.TestCase):
    def test_every_player_facing_template_has_a_japanese_translation(self):
        templates = _source_templates() | set(INDIRECT_TEMPLATES)
        # Command words and bare syntax are deliberately not translated.
        untranslated = {"pong", "Endbot", "?"}
        missing = sorted(template for template in templates - untranslated if template not in JA)
        self.assertEqual(missing, [])

    def test_translations_keep_the_placeholders(self):
        for template, translation in JA.items():
            with self.subTest(template=template):
                self.assertEqual(_placeholders(translation), _placeholders(template))

    def test_translations_use_glyphs_the_chat_font_has(self):
        for template, translation in JA.items():
            unsupported = [
                character
                for character in translation
                if not any(low <= ord(character) <= high for low, high in ALLOWED_RANGES)
            ]
            with self.subTest(template=template):
                self.assertEqual(unsupported, [])


class LanguageTests(unittest.TestCase):
    def test_auto_follows_the_client_locale_and_defaults_to_english(self):
        self.assertEqual(resolve_language("auto", "ja_JP"), "ja")
        self.assertEqual(resolve_language("auto", "ja-JP"), "ja")
        self.assertEqual(resolve_language("auto", "en_US"), "en")
        self.assertEqual(resolve_language("auto", "zh_CN"), "en")
        self.assertEqual(resolve_language("auto", None), "en")
        self.assertEqual(resolve_language("auto", ""), "en")

    def test_fixed_settings_ignore_the_locale(self):
        self.assertEqual(resolve_language("en", "ja_JP"), "en")
        self.assertEqual(resolve_language("ja", "en_US"), "ja")

    def test_setting_defaults_to_auto_and_rejects_unknown_values(self):
        self.assertEqual(language_setting(None), "auto")
        self.assertEqual(language_setting(" JA "), "ja")
        with self.assertRaises(ValueError):
            language_setting("fr")

    def test_english_is_the_template_and_unknown_text_passes_through(self):
        self.assertEqual(tr("en", "{action} stopped", action="jump"), "jump stopped")
        self.assertEqual(tr("ja", "{action} stopped", action="jump"), "jump を止めました")
        self.assertEqual(tr("ja", "Bot Alice does not exist"), "Bot Alice does not exist")
        self.assertEqual(tr("fr", "destroy stopped"), "destroy stopped")
        self.assertEqual(
            tr("ja", "{label} must be a number", label=Text("coordinate")), "座標 は数値で指定してください"
        )

    def test_errors_translate_from_their_template(self):
        error = forms.FormResponseError("enter {clause} <ticks>", clause="interval")
        self.assertEqual(str(error), "enter interval <ticks>")
        self.assertEqual(localize_error(error, "ja"), "interval <ticks> を入力してください")
        self.assertEqual(
            localize_error(ValueError("Bot is not present in the world"), "ja"), "Bot がワールドにいません"
        )


class Source:
    """A captured command source, as dispatch hands it to the service."""

    def __init__(self, language="en", player_id="00000000-0000-4000-8000-000000000009"):
        self.language = language
        self.player_id = player_id


class JapaneseServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.control = FakeControl()
        self.service = PlainCommandService(self.control, FakeWorld())
        self.service.execute(["Alice", "spawn"])

    def run_in(self, language, *arguments):
        return self.service.execute(list(arguments), Source(language)).messages

    def test_english_and_unknown_languages_match_the_default(self):
        commands = (
            ("Alice", "jump", "interval", "10", "for", "40"),
            ("Alice", "move", "forward", "for", "20"),
            ("Alice", "status"),
            ("Alice", "jump", "for", "x"),
            ("help", "2"),
            ("list",),
        )
        for command in commands:
            with self.subTest(command=command):
                default = self.service.execute(list(command), object()).messages
                self.assertEqual(self.run_in("en", *command), default)
                self.assertEqual(self.run_in("fr", *command), default)

    def test_replies_are_japanese_with_english_command_words(self):
        self.assertEqual(
            self.run_in("ja", "Alice", "jump", "interval", "10", "for", "40"),
            ("Endbot: Alice: jump を 10 tick ごとに始めました (40 tick の間)",),
        )
        self.assertEqual(
            self.run_in("ja", "Alice", "move", "forward", "for", "20"),
            ("Endbot: Alice: 移動を forward にしました (20 tick の間)",),
        )
        self.assertEqual(self.run_in("ja", "Alice", "sprint", "on"), ("Endbot: Alice: sprint を on にしました",))
        self.assertEqual(self.run_in("ja", "list"), ("Endbot: Alice [接続中]",))

    def test_status_is_japanese(self):
        (status,) = self.run_in("ja", "Alice", "status")
        self.assertEqual(status, "Endbot: Alice: 接続中 | 目標=オンライン")

    def test_errors_are_japanese(self):
        self.assertEqual(
            self.run_in("ja", "Alice", "jump", "for", "x"),
            ("Endbot: Alice: for の tick 数は整数で指定してください",),
        )
        self.assertEqual(
            self.run_in("ja", "Alice", "fly"),
            ("Endbot: Alice: 不明または正しくない Bot の操作です: fly",),
        )
        # Runtime messages have no translation and stay English.
        self.assertEqual(
            self.run_in("ja", "Bob", "status"),
            ("Endbot: Bob: status に失敗しました: Bot Bob does not exist",),
        )

    def test_logged_results_keep_an_english_log_line(self):
        result = self.service.execute(["Alice", "destroy", "continuous", "for", "100"], Source("ja"))
        self.assertEqual(
            result.messages,
            ("Endbot: Alice: destroy continuous を始めました (100 tick の間)。destroy stop で止められます",),
        )
        self.assertEqual(
            tuple(strip_colors(message) for message in result.log_messages),
            ("Endbot: Alice: destroy continuous started for 100 ticks; destroy stop to cancel",),
        )
        self.assertEqual(result.log_level, "info")
        counted = self.service.execute(["Alice", "destroy", "count", "64"], Source("ja"))
        self.assertEqual(
            counted.messages,
            ("Endbot: Alice: destroy continuous を始めました (64 ブロックまで)。destroy stop で止められます",),
        )
        self.assertEqual(
            tuple(strip_colors(message) for message in counted.log_messages),
            ("Endbot: Alice: destroy continuous started until 64 blocks; destroy stop to cancel",),
        )
        self.assertEqual(counted.log_level, "info")

    def test_mining_notices_use_the_source_language_and_log_in_english(self):
        source = Source("ja")
        self.service.execute(["Alice", "destroy", "continuous"], source)
        self.control.bots["Alice"]["mining"] = {"state": "canceled", "reason": "respawning"}
        notices = super(PlainCommandService, self.service).poll_mining(lambda _: True)
        self.assertEqual(
            [(event.level, strip_colors(render(event, "chat")[0]), strip_colors(render(event, "log")[0]))
             for event in notices],
            [
                (
                    "warning",
                    "Endbot: Alice: destroy continuous が止まりました: 死亡で中止",
                    "Endbot: Alice: destroy continuous stopped: canceled by death",
                )
            ],
        )


class JapaneseHelpTests(unittest.TestCase):
    def test_help_pages_translate_descriptions_but_not_syntax(self):
        lines = [strip_colors(line) for line in help_page(1, "ja")]
        self.assertEqual(lines[0], "--- Endbot ヘルプ 1/4: 接続と状態、テレポート ---")
        self.assertIn("/bot <name> spawn - Bot を作成または接続する", lines)
        self.assertEqual(lines[-1], "次のページ: /bot help 2 - 詳細: /bot help <command>")

    def test_help_detail_translates_details(self):
        lines = [strip_colors(line) for line in help_detail("move", "ja")]
        self.assertEqual(lines[0], "Endbot ヘルプ: move")
        self.assertIn("/bot <name> move forward|backward|left|right for <ticks>", lines)
        self.assertTrue(lines[-1].startswith("移動は押しっぱなしの入力として続き"))

    def test_every_help_page_renders_in_japanese(self):
        for page in range(1, len(HELP_PAGE_TITLES) + 1):
            for line in help_page(page, "ja"):
                self.assertNotIn("{", line)


ONLINE = {"connectionState": "online", "chunkRadius": 5, "chunkRadiusSource": "default"}


class JapaneseFormTests(unittest.TestCase):
    def test_bot_list_screen(self):
        screen = forms.bot_list_screen(forms.BotListView(({"name": "Alice", "connectionState": "offline"},)), "ja")
        self.assertEqual(screen.content, "Bot を選んでください。(/bot help でコマンド一覧)")
        self.assertEqual(
            [item.text for item in screen.items],
            ["オフライン (1)...", "+ ここに新しい Bot", "グループを管理..."],
        )

    def test_bot_screen_translates_labels(self):
        screen = forms.bot_screen(forms.BotView("Alice", "summary", ONLINE), "Steve", ("Steve",), "ja")
        self.assertEqual(screen.title, "Endbot: Alice")
        self.assertEqual(strip_colors(screen.content), "オンライン")
        self.assertEqual(screen.items[0].text, "すべて止める (stop)")
        self.assertEqual(screen.items[-1].text, "Bot 一覧に戻る")
        movement = screen.items[1].action.screen
        self.assertEqual(movement.title, "Alice: 移動")
        # Still the same commands.
        self.assertEqual(screen.items[0].action, forms.run(("Alice", "stop")))

    def test_action_modes_and_clauses_stay_english(self):
        screen = forms.action_screen("Alice", "use", "ja")
        self.assertEqual(
            [item.text for item in screen.items],
            ["once...", "continuous...", "interval...", "for...", "stop", "Bot に戻る"],
        )
        self.assertEqual(screen.content, "コマンドと同じ mode を選んでください。tick: 20 で 1 秒。")
        interval = screen.items[2].action.screen
        self.assertEqual(interval.title, "Alice: use")
        self.assertEqual(interval.submit, "実行")
        self.assertEqual(
            [control.label for control in interval.controls],
            [
                "interval <ticks> (20 で 1 秒)",
                "hold <ticks> (任意。押すたびにこの長さためてから離す)",
                "for <ticks> (任意。20 で 1 秒。空欄なら止めるまで)",
            ],
        )
        self.assertEqual(
            forms.modal_commands(interval, '["10", "4", ""]'), (("Alice", "use", "interval", "10", "hold", "4"),)
        )

    def test_translated_choices_still_build_english_commands(self):
        flags = forms.flags_modal("Alice", "ja")
        self.assertEqual(flags.controls[0].options, ("変更しない", "on", "off"))
        self.assertEqual(forms.modal_commands(flags, "[1, 0]"), (("Alice", "sprint", "on"),))
        drop = forms.drop_modal("Alice", "ja")
        self.assertEqual(forms.modal_commands(drop, "[1]"), (("Alice", "drop", "stack"),))
        teleport = forms.teleport_modal("Alice", ("Steve",), "ja")
        self.assertEqual(teleport.controls[0].options, ("Steve", "下の座標"))
        self.assertEqual(forms.modal_commands(teleport, '[1, "1 2 3"]'), (("Alice", "tp", "1", "2", "3"),))
        destroy = forms.destroy_coordinates_modal("Alice", "ja")
        self.assertEqual(forms.modal_commands(destroy, '["1 2 3", 0]'), (("Alice", "destroy", "1", "2", "3"),))

    def test_form_errors_translate(self):
        modal = forms.move_modal("Alice", "ja")
        with self.assertRaises(forms.FormResponseError) as raised:
            forms.modal_commands(modal, '[9, ""]')
        self.assertEqual(localize_error(raised.exception, "ja"), "フォームの応答を読み取れませんでした")


if __name__ == "__main__":
    unittest.main()
