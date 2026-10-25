"""Render :mod:`endstone_endbot.forms` screens as native Endstone forms.

Server thread only: Endstone delivers form callbacks there, and every action
leaves through ``submit``, the same entry typed /bot commands use.
"""

from __future__ import annotations

from collections.abc import Callable

from endstone_endbot import forms, group_forms
from endstone_endbot.commands import DIM, ERROR, RESET, reply
from endstone_endbot.i18n import localize_error, tr

# /botscript form steps that are not commands a player could type.
SCRIPT_FORM_STEPS = {("parameters",), ("edit",), ("save",)}


class FormPresenter:
    def __init__(
        self,
        submit: Callable[[object, list[str]], None],
        online_names: Callable[[], tuple[str, ...]] = tuple,
        form_module=None,
        language: Callable[[object], str] = lambda player: "en",
    ) -> None:
        self._submit = submit
        self._online_names = online_names
        self._form_module = form_module
        # The viewing player's language for screen text; commands stay English.
        self._language = language

    def _module(self):
        if self._form_module is None:
            # Imported on first use so the plugin module stays importable without forms.
            from endstone import form

            self._form_module = form
        return self._form_module

    def show_view(self, player, view) -> None:
        lang = self._language(player)
        if isinstance(view, (forms.ActionScreen, forms.ModalScreen, forms.ConfirmScreen)):
            screen = view
        elif isinstance(view, forms.BotListView):
            screen = forms.bot_list_screen(view, lang)
        elif isinstance(view, group_forms.GroupListView):
            screen = group_forms.group_list_screen(view, lang)
        elif isinstance(view, group_forms.GroupView):
            screen = group_forms.group_screen(view, lang)
        elif isinstance(view, group_forms.ResultView):
            screen = group_forms.result_screen(view, lang)
        else:
            screen = forms.bot_screen(view, str(player.name), self._online_names(), lang)
        self.show(player, screen, guard=getattr(view, "guard", None))

    def show(self, player, screen, parent=None, guard=None) -> None:
        player.send_form(self.render(screen, parent, guard))

    def render(self, screen, parent=None, guard=None):
        """Build the endstone form; closing an input/confirm form returns to ``parent``."""
        form = self._module()
        guard = getattr(screen, "guard", None) or guard

        def back(player) -> None:
            if parent is not None:
                self.show(player, parent, guard=guard)

        if isinstance(screen, forms.ActionScreen):
            action_form = form.ActionForm(title=screen.title, content=screen.content)
            for item in screen.items:
                if isinstance(item, forms.Header):
                    action_form.add_header(item.text)
                else:
                    action_form.add_button(item.text, on_click=self._on_click(item.action, screen, guard))
            return action_form
        if isinstance(screen, forms.ConfirmScreen):
            # The locked client's MessageForm can hide title/content. Use the
            # same visible action-form confirmation as group deletion.
            confirmation = form.ActionForm(title=screen.title, content=screen.content, on_close=back)
            confirmation.add_button(screen.confirm, on_click=self._on_click(screen.action, guard=guard))
            confirmation.add_button(screen.cancel, on_click=back)
            return confirmation
        return form.ModalForm(
            title=screen.title,
            controls=[self._control(control) for control in screen.controls],
            submit_button=screen.submit,
            on_submit=lambda player, response: self._on_modal(player, screen, response, guard),
            on_close=back,
        )

    def _control(self, control):
        form = self._module()
        if isinstance(control, forms.Dropdown):
            return form.Dropdown(control.label, list(control.options), control.default_index)
        if isinstance(control, forms.Slider):
            return form.Slider(control.label, control.min, control.max, control.step, control.default_value)
        if isinstance(control, forms.StepSlider):
            return form.StepSlider(control.label, list(control.options), control.default_index)
        if isinstance(control, forms.Toggle):
            return form.Toggle(control.label, control.default_value)
        if isinstance(control, forms.Label):
            return form.Label(control.text)
        return form.TextInput(control.label, control.placeholder, control.default_value)

    def _on_click(self, action, origin=None, guard=None):
        return lambda player: self._act(player, action, origin, guard)

    def _act(self, player, action, origin=None, guard=None) -> None:
        if isinstance(action, forms.Show):
            self.show(player, action.screen, origin, guard)
            return
        for command in action.commands:
            # Menu navigation is not echoed: /bot ends with "menu", /botgroup starts with it. /botscript also
            # opens forms (`parameters`, `edit`) and saves them (`save`) with internal words nobody can type.
            if (command[:1] if action.command in {"botgroup", "botscript"} else command[-1:]) != ("menu",) and not (
                action.command == "botscript" and command[:1] in SCRIPT_FORM_STEPS
            ):
                # Show the typed equivalent, so forms also teach the command. ASCII only:
                # the Bedrock chat font lacks symbols such as arrows.
                player.send_message(f"{DIM}/{action.command} {' '.join(command)}{RESET}")
            self._submit(player, list(command), command=action.command, group_guard=action.guard or guard)

    def _on_modal(self, player, screen, response, guard=None) -> None:
        try:
            commands = forms.modal_commands(screen, response)
        except forms.FormResponseError as error:
            player.send_message(reply(localize_error(error, self._language(player)), screen.title, ERROR))
            return
        if not commands:
            player.send_message(reply(tr(self._language(player), "nothing to change"), screen.title))
        self._act(player, forms.Run(commands, command=screen.command), guard=guard)
