"""Render :mod:`endstone_endbot.forms` screens as native Endstone forms.

Server thread only: Endstone delivers form callbacks there, and every action
leaves through ``submit``, the same entry typed /bot commands use.
"""

from __future__ import annotations

from collections.abc import Callable

from endstone_endbot import forms


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

    def show(self, player, screen) -> None:
        player.send_form(self.render(screen))

    def render(self, screen):
        form = self._module()
        if isinstance(screen, forms.ActionScreen):
            action_form = form.ActionForm(title=screen.title, content=screen.content)
            for item in screen.items:
                if isinstance(item, forms.Header):
                    action_form.add_header(item.text)
                else:
                    action_form.add_button(item.text, on_click=self._on_click(item.action))
            return action_form
        if isinstance(screen, forms.ConfirmScreen):

            def confirmed(player, selection: int) -> None:
                if selection == 0:  # Endstone maps button1 to 0.
                    self._act(player, screen.action)

            return form.MessageForm(
                title=screen.title,
                content=screen.content,
                button1=screen.confirm,
                button2=screen.cancel,
                on_submit=confirmed,
            )
        return form.ModalForm(
            title=screen.title,
            controls=[self._control(control) for control in screen.controls],
            submit_button=screen.submit,
            on_submit=lambda player, response: self._on_modal(player, screen, response),
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

    def _on_click(self, action):
        return lambda player: self._act(player, action)

    def _act(self, player, action) -> None:
        if isinstance(action, forms.Show):
            self.show(player, action.screen)
            return
        for command in action.commands:
            self._submit(player, list(command))

    def _on_modal(self, player, screen, response) -> None:
        try:
            commands = forms.modal_commands(screen, response)
        except forms.FormResponseError as error:
            player.send_message(f"Endbot: {screen.title}: {error}")
            return
        if not commands:
            player.send_message(f"Endbot: {screen.title}: nothing to change")
        self._act(player, forms.Run(commands))
