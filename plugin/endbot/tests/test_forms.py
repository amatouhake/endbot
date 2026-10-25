import json
import queue
import types
import unittest

from plain_service import PlainCommandService
from test_command_service import FakeControl, FakeWorld

from endstone_endbot import forms
from endstone_endbot.commands import BotCommandService, CommandResult, parse_command, strip_colors
from endstone_endbot.dispatch import AsyncCommandRunner, CommandSource, ServerThreadBridge
from endstone_endbot.form_adapter import FormPresenter
from endstone_endbot.i18n import tr

ONLINE = {
    "identityId": "00000000-0000-4000-8000-000000000001",
    "name": "Alice",
    "desiredState": "online",
    "connectionState": "online",
    "chunkRadius": 12,
    "chunkRadiusSource": "bot",
}
OBSERVED = {"dimension": "Overworld", "x": 1.0, "y": 64.0, "z": 2.0, "yaw": 91.0, "pitch": -12.0}


def buttons(screen, depth=1):
    """Buttons of a screen and (by default) of its category screens, by text."""
    result = {}
    for item in screen.items:
        if not isinstance(item, forms.Button):
            continue
        if depth and isinstance(item.action, forms.Show) and isinstance(item.action.screen, forms.ActionScreen):
            result.update(buttons(item.action.screen, depth - 1))
        else:
            result[item.text] = item.action
    return result


def at(screen, *path):
    """Follow button texts through Show actions to a nested screen."""
    for text in path:
        screen = next(item.action.screen for item in screen.items if getattr(item, "text", None) == text)
    return screen


def submit(screen, *values):
    return forms.modal_commands(screen, json.dumps(list(values)))


class ScreenTests(unittest.TestCase):
    def online_screen(self, caller="Steve", players=("Steve", "Alice", "Some Name")):
        view = forms.BotView("Alice", "Alice: online", ONLINE, OBSERVED)
        return forms.bot_screen(view, caller, players)

    def test_bot_list_shows_online_bots_and_hides_offline_ones_behind_a_button(self):
        screen = forms.bot_list_screen(
            forms.BotListView(
                (
                    {"name": "Bob", "connectionState": "offline"},
                    {"name": "Zed", "connectionState": "online"},
                    {"name": "Alice", "connectionState": "online"},
                    {"name": "Cy", "connectionState": "failed"},
                    {"name": "Dee", "connectionState": "offline"},
                )
            )
        )
        self.assertEqual(
            [strip_colors(i.text) for i in screen.items],
            ["Alice", "Zed", "FAIL · Cy", "Offline (2)...", "+ New Bot here", "Manage groups..."],
        )
        actions = buttons(screen)
        self.assertEqual(actions["Alice"], forms.Run((("Alice", "menu"),)))
        self.assertEqual(actions["Bob"], forms.Run((("Bob", "menu"),)))
        self.assertEqual(actions["Manage groups..."], forms.Run((("menu",),), command="botgroup"))
        offline = next(i.action.screen for i in screen.items if i.text.startswith("Offline"))
        self.assertEqual([i.text for i in offline.items], ["Bob", "Dee", "Back to Bot list"])
        self.assertEqual(submit(actions["+ New Bot here"].screen, " Carol "), (("Carol", "spawn"),))
        empty = forms.bot_list_screen(forms.BotListView(()))
        self.assertIn("No Bot profiles", empty.content)
        self.assertEqual([i.text for i in empty.items], ["+ New Bot here", "Manage groups..."])

    def test_group_targets_come_before_creation_and_other_commands(self):
        guard = object()
        screen = forms.bot_list_screen(
            forms.BotListView(
                ({"name": "Alice", "connectionState": "online"},),
                groups=(forms.GroupTarget("miners", 3, guard),),
                scripting=True,
            )
        )
        self.assertEqual(
            [getattr(item, "text", None) for item in screen.items],
            ["Alice", "#miners (3)", "+ New Bot here", "Manage groups...", "Scripts..."],
        )
        self.assertEqual(screen.items[1].action, forms.Run((("#miners", "menu"),), guard=guard))
        self.assertEqual(screen.items[-1].action, forms.Run((("menu",),), command="botscript"))

    def test_a_form_without_controls_accepts_an_empty_submission(self):
        screen = forms.ModalScreen("Run", (), lambda values: (("run", "plain"),))
        for response in (None, "", "[]"):
            with self.subTest(response=response):
                self.assertEqual(forms.modal_commands(screen, response), (("run", "plain"),))

    def test_main_screen_groups_actions_like_the_game_controls(self):
        screen = self.online_screen()
        self.assertEqual(
            [item.text for item in screen.items],
            [
                "Stop everything",
                "Movement...",
                "Look...",
                "Teleport...",
                "Gameplay...",
                "Inventory...",
                "Bot...",
                "Back to Bot list",
            ],
        )
        for item in screen.items:
            if isinstance(item.action, forms.Show) and isinstance(item.action.screen, forms.ActionScreen):
                self.assertLessEqual(len(item.action.screen.items), 9)
                self.assertEqual(item.action.screen.items[-1].text, "Back to Bot")

    def test_long_bot_names_keep_state_first_on_one_line(self):
        name = "RakNetTest1234567"
        screen = forms.bot_list_screen(forms.BotListView(({"name": name, "connectionState": "reconnecting"},)))
        self.assertEqual(strip_colors(screen.items[0].text), f"RETRY · {name}")
        self.assertNotIn("\n", screen.items[0].text)

    def test_offline_bot_offers_only_connection_and_radius(self):
        view = forms.BotView("Bob", "Bob: offline", {**ONLINE, "name": "Bob", "connectionState": "offline"})
        screen = forms.bot_screen(view, "Steve")
        self.assertEqual(strip_colors(screen.content), "offline · chunk radius 12")
        actions = buttons(screen)
        self.assertEqual(actions["Resume (where it was)"], forms.Run((("Bob", "resume"),)))
        self.assertEqual(actions["Spawn here"], forms.Run((("Bob", "spawn"),)))
        self.assertNotIn("Forward", actions)
        self.assertIn("Despawn...", actions)
        self.assertEqual(actions["Back to Bot list"], forms.Run((("menu",),)))
        self.assertFalse(any(isinstance(item, forms.Header) for item in screen.items))

    def test_online_bot_screen_shows_status_and_command_backed_buttons(self):
        screen = self.online_screen()
        self.assertEqual(strip_colors(screen.content), "online · Overworld 1.0 64.0 2.0")
        actions = buttons(screen)
        self.assertEqual(actions["Stop everything"], forms.Run((("Alice", "stop"),)))
        self.assertEqual(
            [item.text for item in at(screen, "Movement...").items],
            ["Move...", "Jump...", "Sprint / sneak...", "Back to Bot"],
        )
        self.assertEqual(actions["Reconnect"], forms.Run((("Alice", "reconnect"),)))
        confirm = actions["Despawn..."].screen
        self.assertEqual(confirm.action, forms.Run((("Alice", "despawn"),)))
        self.assertIn("Interact with block...", actions)
        profile = at(screen, "Bot...", "Profile...")
        self.assertIn("Rename...", buttons(profile))
        self.assertIn("Forget...", buttons(profile))

    def test_full_status_stays_under_bot_and_hotbar_defaults_to_current_slot(self):
        view = forms.BotView("Alice", "full status and radius details", {**ONLINE, "selectedHotbarSlot": 9}, OBSERVED)
        screen = forms.bot_screen(view, "Steve")
        connection = next(item.action.screen for item in screen.items if item.text == "Bot...")
        self.assertEqual(connection.content, view.summary)
        self.assertEqual(buttons(screen)["Hotbar slot..."].screen.controls[0].default_index, 8)

    def test_inventory_listing_runs_the_same_command_for_bot_and_group(self):
        for name in ("Alice", "#miners", "#miners[1]"):
            for lang, label in (("en", "List inventory"), ("ja", "持ち物を表示")):
                view = forms.BotView(name, "summary", ONLINE, OBSERVED)
                screen = forms.bot_screen(view, "Steve", lang=lang)
                # Category wording is resolved through the same catalog as the label.
                category = tr(lang, "Inventory...")
                inventory = at(screen, category)
                self.assertEqual(buttons(inventory)[label], forms.Run(((name, "inventory"),)))

    def test_teleport_targets_quote_player_names_for_the_command_tokenizer(self):
        actions = buttons(self.online_screen(caller="Some Name"))
        (to_me,) = actions["Teleport to me"].commands
        self.assertEqual(parse_command(list(to_me)).parameters, {"target": "Some Name"})
        modal = actions["Teleport..."].screen
        destinations = modal.controls[0].options
        self.assertEqual(destinations, ("Steve", "Some Name", forms.TELEPORT_COORDINATES))
        (command,) = submit(modal, 1, "")
        self.assertEqual(parse_command(list(command)).parameters, {"target": "Some Name"})
        (command,) = submit(modal, 2, " 10 ~ -3 ")
        self.assertEqual(command, ("Alice", "tp", "10", "~", "-3"))

    def test_every_modal_produces_commands_the_parser_accepts(self):
        actions = buttons(self.online_screen())
        cases = [
            ("Look...", (90.0, -15.0, ""), [("Alice", "look", "90", "-15")]),
            ("Look...", (0, 0, "1 64 ~2"), [("Alice", "look", "at", "1", "64", "~2")]),
            ("Sprint / sneak...", (1, 2), [("Alice", "sprint", "on"), ("Alice", "sneak", "off")]),
            ("Sprint / sneak...", (0, 0), []),
            ("Hotbar slot...", (8,), [("Alice", "hotbar", "9")]),
            ("Selected one / stack...", (0,), [("Alice", "drop")]),
            ("Selected one / stack...", (1,), [("Alice", "drop", "stack")]),
            ("Chunk radius...", (False, 16.0), [("Alice", "radius", "16")]),
            ("Chunk radius...", (True, 16.0), [("Alice", "radius", "default")]),
            ("Spawn at coordinates...", ("~1 64 ~2",), [("Alice", "spawn", "at", "~1", "64", "~2")]),
            ("Interact with block...", ("1 64 2", 1), [("Alice", "interact", "1", "64", "2", "up")]),
        ]
        actions.update(buttons(forms.drop_screen("Alice")))
        for button, values, expected in cases:
            with self.subTest(button=button, values=values):
                commands = submit(actions[button].screen, *values)
                self.assertEqual([tuple(command) for command in commands], expected)
                for command in commands:
                    parse_command(list(command))

    def test_profile_forms_mirror_rename_and_forget_without_broadcast(self):
        actions = buttons(at(self.online_screen(), "Bot...", "Profile..."))
        self.assertEqual(submit(actions["Rename..."].screen, "Alicia"), (("Alice", "rename", "Alicia"),))
        self.assertEqual(actions["Forget..."].screen.action.commands, (("Alice", "forget"),))

    def test_action_forms_mirror_command_modes_and_clauses(self):
        screen = self.online_screen()
        jump = at(screen, "Movement...", "Jump...")
        self.assertEqual(
            [item.text for item in jump.items], ["once", "continuous...", "interval...", "stop", "Back to Bot"]
        )
        use = at(screen, "Gameplay...", "Use item...")
        self.assertEqual(
            [item.text for item in use.items],
            ["once...", "continuous...", "interval...", "for...", "stop", "Back to Bot"],
        )
        destroy = at(screen, "Gameplay...", "Destroy blocks...")
        self.assertEqual(
            [item.text for item in destroy.items], ["once", "continuous...", "x y z...", "stop", "Back to Bot"]
        )
        immediate = [
            (jump, "once", ("Alice", "jump", "once")),
            (jump, "stop", ("Alice", "jump", "stop")),
            (at(screen, "Gameplay...", "Attack..."), "once", ("Alice", "attack", "once")),
            (use, "stop", ("Alice", "use", "stop")),
            (destroy, "once", ("Alice", "destroy", "once")),
            (destroy, "stop", ("Alice", "destroy", "stop")),
        ]
        for parent, text, command in immediate:
            with self.subTest(text=text, command=command):
                self.assertEqual(buttons(parent, 0)[text], forms.Run((command,)))
        forms_cases = [
            (("Movement...", "Move..."), (0, ""), ("Alice", "move", "forward")),
            (("Movement...", "Move..."), (1, " 40 "), ("Alice", "move", "backward", "for", "40")),
            (("Movement...", "Move..."), (4, ""), ("Alice", "move", "stop")),
            (("Movement...", "Jump...", "continuous..."), ("",), ("Alice", "jump", "continuous")),
            (("Movement...", "Jump...", "continuous..."), ("100",), ("Alice", "jump", "continuous", "for", "100")),
            (
                ("Movement...", "Jump...", "interval..."),
                ("20", "100"),
                ("Alice", "jump", "interval", "20", "for", "100"),
            ),
            (("Gameplay...", "Attack...", "interval..."), (" 40 ", ""), ("Alice", "attack", "interval", "40")),
            (("Gameplay...", "Use item...", "once..."), ("",), ("Alice", "use", "once")),
            (("Gameplay...", "Use item...", "once..."), ("30",), ("Alice", "use", "once", "hold", "30")),
            (("Gameplay...", "Use item...", "continuous..."), ("", ""), ("Alice", "use", "continuous")),
            (
                ("Gameplay...", "Use item...", "continuous..."),
                ("25", "200"),
                ("Alice", "use", "continuous", "hold", "25", "for", "200"),
            ),
            (
                ("Gameplay...", "Use item...", "interval..."),
                ("30", "20", "80"),
                ("Alice", "use", "interval", "30", "hold", "20", "for", "80"),
            ),
            (("Gameplay...", "Use item...", "for..."), ("40",), ("Alice", "use", "for", "40")),
            (("Gameplay...", "Destroy blocks...", "continuous..."), ("", ""), ("Alice", "destroy", "continuous")),
            (
                ("Gameplay...", "Destroy blocks...", "continuous..."),
                ("200", ""),
                ("Alice", "destroy", "continuous", "for", "200"),
            ),
            (
                ("Gameplay...", "Destroy blocks...", "continuous..."),
                ("", "64"),
                ("Alice", "destroy", "continuous", "count", "64"),
            ),
            (
                ("Gameplay...", "Destroy blocks...", "continuous..."),
                ("200", "64"),
                ("Alice", "destroy", "continuous", "for", "200", "count", "64"),
            ),
            (
                ("Gameplay...", "Destroy blocks...", "x y z..."),
                ("~ ~-1 ~", 0),
                ("Alice", "destroy", "~", "~-1", "~"),
            ),
            (
                ("Gameplay...", "Destroy blocks...", "x y z..."),
                ("1 64 2", 2),
                ("Alice", "destroy", "1", "64", "2", "up"),
            ),
        ]
        for path, values, expected in forms_cases:
            with self.subTest(path=path, values=values):
                (command,) = submit(at(screen, *path), *values)
                self.assertEqual(tuple(command), expected)
                parse_command(list(command))
        for path, values in (
            (("Movement...", "Jump...", "interval..."), (" ", "")),
            (("Gameplay...", "Use item...", "for..."), ("",)),
        ):
            with self.subTest(path=path), self.assertRaisesRegex(forms.FormResponseError, "enter"):
                submit(at(screen, *path), *values)

    def test_modal_defaults_reflect_current_bot_state(self):
        actions = buttons(self.online_screen())
        yaw, pitch, _ = actions["Look..."].screen.controls
        self.assertEqual((yaw.default_value, pitch.default_value), (91, -12))
        use_default, radius = actions["Chunk radius..."].screen.controls
        self.assertEqual((use_default.default_value, radius.default_value), (False, 12))

    def test_malformed_responses_are_rejected_without_commands(self):
        actions = buttons(self.online_screen())
        move = actions["Hotbar slot..."].screen
        destroy = at(self.online_screen(), "Gameplay...", "Destroy blocks...", "x y z...")
        for screen, response in (
            (move, None),
            (move, "not json"),
            (move, json.dumps({"0": 1})),
            (move, json.dumps([0, 1])),
            (move, json.dumps([9])),
            (move, json.dumps([-1])),
            (move, json.dumps([True])),
            (move, json.dumps([0.5])),
            (move, json.dumps(["0"])),
            (destroy, json.dumps(["1 2", 0])),
            (actions["Chunk radius..."].screen, json.dumps(["yes", 4])),
            (forms.spawn_modal(), json.dumps(["  "])),
        ):
            with self.subTest(response=response), self.assertRaises(forms.FormResponseError):
                forms.modal_commands(screen, response)


class ServiceMenuTests(unittest.TestCase):
    def setUp(self):
        self.control = FakeControl()
        self.control.bots["Alice"] = dict(ONLINE)
        self.world = FakeWorld()
        self.world.observe = lambda identity: OBSERVED
        self.service = PlainCommandService(self.control, self.world)
        self.player = CommandSource(player_id="11111111-2222-4333-8444-555555555555")

    def test_menu_is_player_only_and_needs_no_runtime_for_the_refusal(self):
        for service in (self.service, PlainCommandService()):
            for arguments in (["menu"], ["Alice", "menu"]):
                result = service.execute(arguments, CommandSource(sender=object()))
                self.assertEqual(result.messages, ("Endbot: menu is available only to players; use /bot help",))
                self.assertIsNone(result.view)

    def test_menu_views_carry_runtime_data_and_the_status_reply(self):
        listing = self.service.execute(["menu"], self.player)
        self.assertEqual(listing.messages, ())
        self.assertEqual(listing.view, forms.BotListView((ONLINE,)))
        # Players see the colored status reply in the form, exactly as in chat.
        colored = BotCommandService(self.control, self.world)
        bot = colored.execute(["Alice", "MENU"], self.player)
        status = colored.execute(["Alice", "status"], self.player)
        self.assertEqual(bot.view.summary, status.message)
        self.assertNotEqual(strip_colors(bot.view.summary), bot.view.summary)
        self.assertEqual((bot.view.name, bot.view.observed), ("Alice", OBSERVED))

    def test_bare_bot_opens_list_for_players_and_help_for_console(self):
        self.assertEqual(self.service.execute([], self.player).view, forms.BotListView((ONLINE,)))
        self.assertIn("Endbot help page 1", self.service.execute([], CommandSource()).message)

    def test_unknown_bot_menu_fails_like_any_command(self):
        result = self.service.execute(["Ghost", "menu"], self.player)
        self.assertIsNone(result.view)
        self.assertEqual(result.message, "Endbot: Ghost: menu failed: Bot Ghost does not exist")

    def test_menu_help_is_listed(self):
        self.assertIn("/bot menu - open the Bot control forms (players only)", self.service.execute(["help"]).messages)
        detail = self.service.execute(["help", "menu"]).messages
        self.assertIn("/bot <name> menu", detail)


class ScheduledCallbacks:
    def __init__(self):
        self.callbacks = queue.Queue()

    def schedule(self, callback):
        self.callbacks.put(callback)

    def run_next(self, timeout=1.0):
        self.callbacks.get(timeout=timeout)()


class DispatchViewTests(unittest.TestCase):
    def run_view(self, allowed):
        scheduled = ScheduledCallbacks()
        bridge = ServerThreadBridge(scheduled.schedule)
        self.addCleanup(bridge.close)
        view = forms.BotListView(())
        shown, checks = [], iter(allowed)
        player = types.SimpleNamespace(unique_id="11111111-2222-4333-8444-555555555555", location=object())

        class Service:
            def execute(self, arguments, source):
                return CommandResult(True, view=view)

        runner = AsyncCommandRunner(
            Service(),
            bridge,
            lambda source: player,
            authorize=lambda source: next(checks),
            show_view=lambda target, value: shown.append((target, value)),
        )
        self.addCleanup(runner.close)
        runner.submit(["menu"], player)
        scheduled.run_next()  # worker authorization
        scheduled.run_next()  # delivery
        return shown, player, view

    def test_view_is_shown_on_the_server_thread_after_authorization(self):
        shown, player, view = self.run_view([True, True])
        self.assertEqual(shown, [(player, view)])

    def test_view_is_dropped_when_permission_is_revoked_before_delivery(self):
        shown, _, _ = self.run_view([True, False])
        self.assertEqual(shown, [])


class FakeFormModule:
    """Records the endstone.form calls the presenter makes."""

    def __init__(self):
        module = self

        class Control:
            def __init__(self, *args, **kwargs):
                self.args, self.kwargs = args, kwargs

        class ActionForm(Control):
            def __init__(self, **kwargs):
                super().__init__(**kwargs)
                self.items = []

            def add_header(self, text):
                self.items.append(("header", text, None))

            def add_button(self, text, on_click=None):
                self.items.append(("button", text, on_click))

        self.ActionForm = ActionForm
        for name in ("MessageForm", "ModalForm", "Dropdown", "Slider", "StepSlider", "Toggle", "TextInput", "Label"):
            setattr(module, name, type(name, (Control,), {}))


class FakePlayer:
    name = "Steve"

    def __init__(self):
        self.forms, self.messages = [], []

    def send_form(self, form):
        self.forms.append(form)

    def send_message(self, message):
        self.messages.append(message)


class PresenterTests(unittest.TestCase):
    def test_script_paste_view_routes_save_with_capability_and_does_not_echo_source(self):
        from endstone_endbot.script_forms import line_screen
        from endstone_endbot.script_store import Script

        submitted = []
        presenter = FormPresenter(lambda player, argv, **kwargs: submitted.append((argv, kwargs)),
                                  form_module=FakeFormModule())
        player = FakePlayer()
        screen = line_screen(Script("example", "params {}\n", "r1"), 0, 1)
        presenter.show_view(player, screen)
        # The context label, the line, the line below and the delete toggle.
        player.forms[-1].kwargs["on_submit"](player, json.dumps([None, "params {}", "log('test')", False]))
        self.assertEqual(submitted[0][0], ["save", "example", "params {}\nlog('test')\n"])
        self.assertEqual(submitted[0][1]["command"], "botscript")
        self.assertTrue(submitted[0][1]["group_guard"].edit)
        self.assertEqual(player.messages, [])

    def test_script_menus_echo_only_commands_a_player_could_type(self):
        from endstone_endbot.script_forms import run_screen, script_screen
        from endstone_endbot.script_store import Script

        presenter = FormPresenter(lambda player, argv, **kwargs: None, form_module=FakeFormModule())
        player = FakePlayer()
        script = Script("hop", "params {}\n", "r1")
        presenter.show_view(player, script_screen(script, "en", True))
        for kind, text, click in player.forms[-1].items:
            if kind == "button" and text in {"Run", "Stop runs", "Edit lines..."}:
                click(player)
        # Run and Edit lines only open forms; Stop runs is a real command.
        self.assertEqual([strip_colors(message) for message in player.messages], ["/botscript stop hop"])
        player.messages.clear()
        schema = [{"name": "name", "type": "string"}, {"name": "origin", "type": "pos"}]
        presenter.show_view(player, run_screen(script, schema, [], [], None))
        player.forms[-1].kwargs["on_submit"](player, json.dumps(["Alice", "1 64 2"]))
        self.assertEqual([strip_colors(message) for message in player.messages],
                         ['/botscript run hop name=Alice origin="1 64 2"'])

    def setUp(self):
        self.submitted = []
        self.presenter = FormPresenter(
            lambda player, argv, **_: self.submitted.append(argv), lambda: ("Steve", "Alice"), FakeFormModule()
        )
        self.player = FakePlayer()

    def bot_form(self):
        self.presenter.show_view(self.player, forms.BotView("Alice", "Alice: online", ONLINE, OBSERVED))
        form = self.player.forms[-1]
        clicks = {}

        # Only category buttons open ActionForms; do not invoke commands or
        # modal/confirmation buttons while collecting the nested callbacks.
        for kind, text, click in form.items:
            if kind != "button":
                continue
            if text in {"Movement...", "Teleport...", "Gameplay...", "Inventory...", "Bot..."}:
                click(self.player)
                for child_kind, child_text, child_click in self.player.forms[-1].items:
                    if child_kind == "button":
                        clicks[child_text] = child_click
            else:
                clicks[text] = click
        return form, clicks

    def test_buttons_submit_commands_or_open_screens(self):
        form, clicks = self.bot_form()
        self.assertEqual(strip_colors(form.kwargs["content"]), "online · Overworld 1.0 64.0 2.0")
        self.assertEqual(len(form.items), 8)
        clicks["Teleport to me"](self.player)
        clicks["Stop everything"](self.player)
        self.assertEqual(self.submitted, [["Alice", "tp", "Steve"], ["Alice", "stop"]])
        clicks["Hotbar slot..."](self.player)
        modal = self.player.forms[-1]
        self.assertEqual(type(modal).__name__, "ModalForm")
        self.assertEqual(modal.kwargs["submit_button"], "Select")
        self.submitted.clear()
        modal.kwargs["on_submit"](self.player, json.dumps([1]))
        self.assertEqual(self.submitted, [["Alice", "hotbar", "2"]])
        # Commands from forms echo their typed form; navigation does not.
        self.assertEqual(
            [strip_colors(message) for message in self.player.messages],
            ["/bot Alice tp Steve", "/bot Alice stop", "/bot Alice hotbar 2"],
        )

    def test_modal_errors_reply_instead_of_submitting(self):
        _, clicks = self.bot_form()
        clicks["Destroy blocks..."](self.player)
        {text: c for _, text, c in self.player.forms[-1].items}["x y z..."](self.player)
        self.player.forms[-1].kwargs["on_submit"](self.player, json.dumps(["1 2", 0]))
        clicks["Sprint / sneak..."](self.player)
        self.player.forms[-1].kwargs["on_submit"](self.player, json.dumps([0, 0]))
        self.assertEqual(self.submitted, [])
        self.assertEqual(
            [strip_colors(message) for message in self.player.messages],
            [
                "Endbot: Alice: destroy x y z: enter exactly three coordinates: x y z",
                "Endbot: Alice: sprint / sneak: nothing to change",
            ],
        )

    def test_mode_screen_form_closes_back_to_the_mode_screen(self):
        _, clicks = self.bot_form()
        clicks["Use item..."](self.player)
        modes = {text: c for _, text, c in self.player.forms[-1].items}
        modes["interval..."](self.player)
        self.player.forms[-1].kwargs["on_close"](self.player)
        self.assertEqual(self.player.forms[-1].kwargs["title"], "Alice: use")
        modes["interval..."](self.player)
        self.player.forms[-1].kwargs["on_submit"](self.player, json.dumps(["30", "20", "80"]))
        self.assertEqual(self.submitted, [["Alice", "use", "interval", "30", "hold", "20", "for", "80"]])
        self.assertEqual(
            strip_colors(self.player.messages[-1]), "/bot Alice use interval 30 hold 20 for 80"
        )

    def test_closing_an_input_or_confirm_form_returns_to_its_category(self):
        self.presenter.show_view(self.player, forms.BotView("Alice", "Alice: online", ONLINE, OBSERVED))
        main = self.player.forms[-1]
        click = {text: c for kind, text, c in main.items if kind == "button"}
        click["Movement..."](self.player)
        movement = self.player.forms[-1]
        {text: c for _, text, c in movement.items}["Move..."](self.player)
        modal = self.player.forms[-1]
        self.assertEqual(type(modal).__name__, "ModalForm")
        modal.kwargs["on_close"](self.player)
        self.assertEqual(self.player.forms[-1].kwargs["title"], "Alice: movement")
        click["Inventory..."](self.player)
        click_inventory = self.player.forms[-1]
        self.assertEqual(click_inventory.kwargs["title"], "Alice: inventory")
        click["Bot..."](self.player)
        {text: c for _, text, c in self.player.forms[-1].items}["Despawn..."](self.player)
        confirm = self.player.forms[-1]
        {text: c for _, text, c in confirm.items}["Cancel"](self.player)
        self.assertEqual(self.player.forms[-1].kwargs["title"], "Alice: bot")
        self.assertEqual(self.submitted, [])

    def test_despawn_needs_the_confirm_button(self):
        _, clicks = self.bot_form()
        clicks["Despawn..."](self.player)
        confirm = self.player.forms[-1]
        self.assertEqual(type(confirm).__name__, "ActionForm")
        self.assertIn("Alice", confirm.kwargs["content"])
        {text: c for _, text, c in confirm.items}["Cancel"](self.player)
        self.assertEqual(self.submitted, [])
        {text: c for _, text, c in confirm.items}["Despawn"](self.player)
        self.assertEqual(self.submitted, [["Alice", "despawn"]])

    def test_list_view_renders_bot_buttons(self):
        self.presenter.show_view(self.player, forms.BotListView(({"name": "Alice", "connectionState": "online"},)))
        form = self.player.forms[-1]
        {text: click for _, text, click in form.items}["Alice"](self.player)
        self.assertEqual(self.submitted, [["Alice", "menu"]])

    def test_japanese_viewer_gets_japanese_screens_and_errors_but_english_commands(self):
        presenter = FormPresenter(
            lambda player, argv, **_: self.submitted.append(argv),
            lambda: ("Steve", "Alice"),
            FakeFormModule(),
            language=lambda player: "ja",
        )
        presenter.show_view(self.player, forms.BotView("Alice", "Alice: online", ONLINE, OBSERVED))
        main = self.player.forms[-1]
        click = {text: c for kind, text, c in main.items if kind == "button"}
        self.assertIn("すべて止める (stop)", click)
        click["ゲームプレイ..."](self.player)
        self.assertEqual(self.player.forms[-1].kwargs["title"], "Alice: ゲームプレイ")
        {text: c for _, text, c in self.player.forms[-1].items}["ブロックを壊す (destroy)..."](self.player)
        modes = {text: c for _, text, c in self.player.forms[-1].items}
        modes["x y z..."](self.player)
        self.player.forms[-1].kwargs["on_submit"](self.player, json.dumps(["1 2", 0]))
        self.assertEqual(
            strip_colors(self.player.messages[-1]),
            "Endbot: Alice: destroy x y z: 座標は x y z の 3 つを入力してください",
        )
        modes["stop"](self.player)
        self.assertEqual(self.submitted, [["Alice", "destroy", "stop"]])
        self.assertEqual(strip_colors(self.player.messages[-1]), "/bot Alice destroy stop")


if __name__ == "__main__":
    unittest.main()
