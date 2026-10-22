"""Bounded, controller-private snapshots of group operation acceptance."""

from collections import Counter, OrderedDict
from dataclasses import dataclass
from uuid import uuid4

from endstone_endbot.i18n import LocalizedError, Text, tr

PAGE_SIZE = 10
CACHE_SIZE = 128
HISTORY_SIZE = 20
REASONS = {
    "not_online": Text("offline"), "offline": Text("offline"),
    "not_found": Text("missing Bot profile"), "missing_profile": Text("missing Bot profile"),
    "identity_changed": Text("Bot identity changed"), "canceled": Text("permission revoked or plugin stopped"),
    "unavailable": Text("runtime unavailable"), "unknown": Text("acceptance unknown"),
    "other": Text("other errors"),
}


@dataclass(frozen=True)
class MemberResult:
    index: int
    identity: str
    name: str
    outcome: str
    reason: str = "other"
    detail: str = ""
    data: dict | None = None


@dataclass(frozen=True)
class GroupResult:
    identity: str
    group_id: str
    group_name: str
    target: str
    operation: str
    rows: tuple[MemberResult, ...]

    def summary(self, lang="en") -> tuple[str, ...]:
        counts = Counter(row.outcome for row in self.rows)
        line = tr(lang, "{operation}: accepted {accepted}, failed {failed}, not run {skipped}",
                  operation=self.operation, accepted=counts["accepted"], failed=counts["failed"],
                  skipped=counts["skipped"])
        if counts["unknown"]:
            line += tr(lang, ", unknown {count}", count=counts["unknown"])
        aliases = {"offline": "not_online", "not_found": "missing_profile"}
        reasons = Counter(aliases.get(row.reason, row.reason) if row.reason in REASONS else "other"
                          for row in self.rows if row.outcome != "accepted")
        if not reasons:
            return (line,)
        selected = reasons.most_common(3)
        detail = ", ".join(tr(lang, "{reason} {count}", reason=REASONS[reason], count=count)
                           for reason, count in selected)
        remaining = sum(reasons.values()) - sum(count for _, count in selected)
        if remaining:
            detail += tr(lang, ", other {count}", count=remaining)
        return line, tr(lang, "Reasons: {reasons}", reasons=detail)

    def lines(self, history: int, total: int, lang="en") -> tuple[str, ...]:
        heading = tr(lang, "{target} {operation} - history {history}/{total} (1 = latest)", target=self.target,
                     operation=self.operation, history=history, total=total)
        rows = []
        for row in self.rows:
            status = tr(lang, {"accepted": "accepted", "failed": "failed", "skipped": "not run",
                               "unknown": "acceptance unknown"}[row.outcome])
            rows.append(f"#{self.group_name}[{row.index}] {row.name}: {status} - {row.detail}")
        return (heading, *self.summary(lang), *rows)


class ResultStore:
    """Controller-private history, capped per group and globally; never persisted."""

    def __init__(self):
        self.records = OrderedDict()

    @staticmethod
    def owner(sender):
        return getattr(sender, "player_id", None) or "console"

    def save(self, sender, group, target, operation, rows):
        operation = operation.replace("\n", " ").replace("\r", " ")[:128]
        result = GroupResult(str(uuid4()), group.identity, group.name, target, operation, tuple(rows))
        key = self.owner(sender), group.identity, result.identity
        self.records[key] = result
        matching = [key for key in self.records if key[:2] == (self.owner(sender), group.identity)]
        for old in matching[:-HISTORY_SIZE]:
            del self.records[old]
        while len(self.records) > CACHE_SIZE:
            self.records.popitem(last=False)
        return result

    def history(self, sender, group):
        records = tuple(result for key, result in reversed(self.records.items())
                        if key[:2] == (self.owner(sender), group.identity))
        if not records:
            raise LocalizedError("No recent group result for this controller (results reset on restart)")
        return records

    def get(self, sender, group, history=1):
        records = self.history(sender, group)
        if not 1 <= history <= len(records):
            raise LocalizedError("Result history must be between 1 and {count} (1 = latest)", count=len(records))
        return records[history - 1]

    def find(self, sender, group, identity):
        for result in self.history(sender, group):
            if result.identity == identity:
                return result
        raise LocalizedError("Result is no longer retained; reopen the result history")
