"""Command service test double that compares replies as plain text."""

from dataclasses import replace

from endstone_endbot.commands import BotCommandService, strip_colors


class PlainCommandService(BotCommandService):
    """Strip Bedrock color codes so assertions match what operators read."""

    def execute(self, arguments, sender=None):
        result = super().execute(arguments, sender)
        return replace(result, messages=tuple(strip_colors(message) for message in result.messages))

    def poll_mining(self, authorize):
        notices = super().poll_mining(authorize)
        return [(source, level, strip_colors(message)) for source, level, message, _ in notices]
