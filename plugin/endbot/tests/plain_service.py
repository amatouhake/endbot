"""Command service test double that compares replies as plain text."""

from dataclasses import replace

from endstone_endbot.commands import BotCommandService, strip_colors
from endstone_endbot.events import render


class PlainCommandService(BotCommandService):
    """Strip Bedrock color codes so assertions match what operators read."""

    def execute(self, arguments, sender=None):
        result = super().execute(arguments, sender)
        return replace(result, messages=tuple(strip_colors(message) for message in result.messages))

    def poll_mining(self, authorize):
        events = super().poll_mining(authorize)
        return [(event.to, event.level, strip_colors(render(event, "chat")[0])) for event in events]
