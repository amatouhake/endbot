"""Render :mod:`endstone_endbot.forms` screens as native Endstone forms.

Server thread only: Endstone delivers form callbacks there, and every action
leaves through ``submit``, the same entry typed /bot commands use.
"""

from __future__ import annotations

from collections.abc import Callable

from endstone_endbot import forms
from endstone_endbot.commands import DIM, ERROR, RESET, reply


class FormPresenter:
    def __init__(
        self,
        submit: Callable[[object, list[str]], None],
        online_names: Callable[[], tuple[str, ...]] = tuple,
        form_module=None,
    ) -> None:
        self._submit = submit
        self._online_names = online_names
        self._form_module = form_module

    def _module(self):
        if self._form_module is None:
            # Imported on first use so the plugin module stays importable without forms.
            from endstone import form

            self._form_module = form
        return self._form_module

    def show_view(self, player, view) -> None:
        if isinstance(view, forms.BotListView):
            screen = forms.bot_list_screen(view)
        else:
            screen = forms.bot_screen(view, str(player.name), self._online_names())
        self.show(player, screen)

    def show(self, player, screen, parent=None) -> None:
        player.send_form(self.render(screen, parent))

    def render(self, screen, parent=None):
        """Build the endstone form; closing an input/confirm form returns to ``parent``."""
        form = self._module()

        def back(player) -> None:
            if parent is not None:
                self.show(player, parent)

        if isinstance(screen, forms.ActionScreen):
            action_form = form.ActionForm(title=screen.title, content=screen.content)
            for item in screen.items:
                if isinstance(item, forms.Header):
                    action_form.add_header(item.text)
                else:
                    action_form.add_button(item.text, on_click=self._on_click(item.action, screen))
            return action_form
        if isinstance(screen, forms.ConfirmScreen):

            def confirmed(player, selection: int) -> None:
                if selection == 0:  # Endstone maps button1 to 0.
                    self._act(player, screen.action)
                else:
                    back(player)

            return form.MessageForm(
                title=screen.title,
                content=screen.content,
                button1=screen.confirm,
                button2=screen.cancel,
                on_submit=confirmed,
                on_close=back,
            )
        return form.ModalForm(
            title=screen.title,
            controls=[self._control(control) for control in screen.controls],
            submit_button=screen.submit,
            on_submit=lambda player, response: self._on_modal(player, screen, response),
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
        return form.TextInput(control.label, control.placeholder, control.default_value)

    def _on_click(self, action, origin=None):
        return lambda player: self._act(player, action, origin)

    def _act(self, player, action, origin=None) -> None:
        if isinstance(action, forms.Show):
            self.show(player, action.screen, origin)
            return
        for command in action.commands:
            if command[-1:] != ("menu",):
                # Show the typed equivalent, so forms also teach the command. ASCII only:
                # the Bedrock chat font lacks symbols such as arrows.
                player.send_message(f"{DIM}/bot {' '.join(command)}{RESET}")
            self._submit(player, list(command))

    def _on_modal(self, player, screen, response) -> None:
        try:
            commands = forms.modal_commands(screen, response)
        except forms.FormResponseError as error:
            player.send_message(reply(str(error), screen.title, ERROR))
            return
        if not commands:
            player.send_message(reply("nothing to change", screen.title))
        self._act(player, forms.Run(commands))
