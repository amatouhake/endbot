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


def reason_text(value, lang: str = "en") -> str:
    """A reason as text: a ``reason()`` mapping rendered in ``lang``, or any other value as a string."""
    if isinstance(value, dict) and isinstance(value.get("template"), str):
        params = value.get("params") if isinstance(value.get("params"), dict) else {}
        return tr(lang, value["template"], **params)
    return str(value)


def _reason_text(data: dict, lang: str) -> str:
    return reason_text(data, lang)


def _job_ended(data, lang, sink):
    color = WARNING if data["level"] == "warning" else ""
    text = tr(lang, "{command} stopped: {reason}", command=data["job"], reason=_reason_text(data["reason"], lang))
    return (reply(text, data["bot"], color),)


def _job_waiting(data, lang, sink):
    if data["blockType"]:
        text = tr(lang, "destroy is waiting: no {block} it can break within 6 blocks; move the Bot closer",
                  block=data["blockType"].removeprefix("minecraft:"))
    else:
        text = tr(lang, "destroy continuous is waiting: no block it can break within 6 blocks")
    return (reply(text, data["bot"], ""),)


def _skipped(data, lang):
    return tr(lang, "({count} log lines not shown)", count=data["skipped"])


def _run_log(data, lang, sink):
    run, limited = data["run"], data["limited"]
    if data.get("notice") == "throttled":
        if sink == "log":
            return (f"Endbot script {run}: operation rate limit reached; operations now wait for a free slot",)
        if not data["chat"]:
            return ()
        text = tr(lang, "operation rate limit reached; operations now wait for a free slot")
        if data["skipped"]:
            text += " " + _skipped(data, lang)
        return (reply(text, f"{data['name']} ({run})", DIM),)
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


def _trigger_disabled(data, lang, sink):
    """A trigger went over its start cap or lost its owner's grant (BOT-022, EVENTS.md E8)."""
    if sink == "log":
        return (f"Endbot trigger {data['name']} of {data['owner']} disabled: {data['reason']}",)
    return (reply(tr(lang, "Trigger {name} was disabled: {reason}; /botscript trigger enable {name} arms it again",
                     name=data["name"], reason=tr(lang, data["reason"]))),)


RENDERERS = {"job.ended": _job_ended, "job.waiting": _job_waiting, "run.log": _run_log, "run.ended": _run_ended,
             "trigger.disabled": _trigger_disabled}


def render(event: Event, sink: str, lang: str | None = None) -> tuple[str, ...]:
    """The lines ``sink`` ("chat" or "log") shows for ``event``; the log is always English."""
    lang = "en" if sink == "log" else lang or _language(event.to)
    return RENDERERS[event.kind]({**event.data, "level": event.level}, lang, sink)
