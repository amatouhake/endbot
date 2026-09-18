"""Command-domain behavior independent from Endstone's adapter objects."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CommandResult:
    handled: bool
    message: str | None = None


class BotCommandService:
    """Dispatch the deliberately tiny M0 command surface."""

    def execute(self, arguments: list[str]) -> CommandResult:
        if arguments == ["ping"]:
            return CommandResult(handled=True, message="Endbot: pong")
        return CommandResult(handled=False)

