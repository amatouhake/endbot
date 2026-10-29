"""Events: something that happened, addressed to the principal it concerns.

An ``Event`` is data. Sinks decide how it reads: the chat sink renders it in
the principal's language, the console log sink in English, and later the web
renders the same data its own way. Each kind has one renderer here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from endbot_core.commands import DIM, WARNING, _language, reply
from endbot_core.i18n import LocalizedError, tr

# The starter's chat shows script log lines cut to this many characters.
CHAT_LOG_CHARS = 256


@dataclass(frozen=True, slots=True)
class Event:
    to: object  # The Principal it concerns; a console principal reaches the log only.
    kind: str
    data: dict
    level: str = "info"


class EventLogSink(Protocol):
    """Where Event log lines go; endbotd will wire a file sink later."""

    def write(self, level: str, line: str) -> None:
        """Write one colour-stripped English line at ``level`` ("info", "warning" or "error")."""
        ...


class LoggerSink:
    """Forward Event log lines to a logger."""

    def __init__(self, logger) -> None:
        self._logger = logger

    def write(self, level: str, line: str) -> None:
        """Write one line to ``level``, defaulting to ``info`` for an unknown level."""
        getattr(self._logger, level, self._logger.info)(line)


def reason(value) -> dict:
    """A reason as data: an English template and its parameters, localized by each sink."""
    if isinstance(value, LocalizedError):
        return {"template": value.template, "params": dict(value.params)}
    return {"template": str(value), "params": {}}


def _reason_text(data: dict, lang: str) -> str:
    return tr(lang, data["template"], **data["params"])


def _job_ended(data, lang, sink):
    color = WARNING if data["level"] == "warning" else ""
    text = tr(lang, "{command} stopped: {reason}", command=data["job"], reason=_reason_text(data["reason"], lang))
    return (reply(text, data["bot"], color),)


def _job_waiting(data, lang, sink):
    if data["blockType"]:
        text = tr(lang, "destroy is waiting: no {block} it can break within 5 blocks; move the Bot closer",
                  block=data["blockType"].removeprefix("minecraft:"))
    else:
        text = tr(lang, "destroy continuous is waiting: no block it can break within 5 blocks")
    return (reply(text, data["bot"], ""),)


def _skipped(data, lang):
    return tr(lang, "({count} log lines not shown)", count=data["skipped"])


def _run_log(data, lang, sink):
    run, limited = data["run"], data["limited"]
    if sink == "log":
        return (f"Endbot script {run}: log limit reached; further logs are dropped" if limited
                else f"Endbot script {run}: {data['text']}",)
    if not data["chat"]:
        return ()
    # The text is already free of formatting codes; a lone trailing `§` would still format the next text.
    text = tr(lang, "log limit reached; further logs are dropped") if limited else data["text"].replace("§", "")
    if len(text) > CHAT_LOG_CHARS:
        text = text[:CHAT_LOG_CHARS] + "..."
    if data["skipped"]:
        text += " " + _skipped(data, lang)
    return (reply(text, f"{data['name']} ({run})", DIM),)


def outcome_text(data, lang: str) -> str:
    """How a run ended, as a run.ended Event states it."""
    outcome = _reason_text(data["outcome"], lang)
    if not data["cleanup"]:
        outcome += "; " + tr(lang, "Script input cleanup failed; use /bot stop")
    return outcome


def _run_ended(data, lang, sink):
    run, outcome = data["run"], outcome_text(data, lang)
    if sink == "log":
        return (f"Endbot script {run} {data['name']} ended: {outcome} (controller {data['controller']})",
                f"Endbot script {run}: input cleanup {'completed' if data['cleanup'] else 'failed'}")
    lines = (reply(tr(lang, "Script {name} ({run}) ended: {reason}", name=data["name"], run=run, reason=outcome)),)
    if data["skipped"]:
        lines += (reply(_skipped(data, lang), f"{data['name']} ({run})", DIM),)
    return lines


RENDERERS = {"job.ended": _job_ended, "job.waiting": _job_waiting, "run.log": _run_log, "run.ended": _run_ended}


def render(event: Event, sink: str, lang: str | None = None) -> tuple[str, ...]:
    """The lines ``sink`` ("chat" or "log") shows for ``event``; the log is always English."""
    lang = "en" if sink == "log" else lang or _language(event.to)
    return RENDERERS[event.kind]({**event.data, "level": event.level}, lang, sink)
