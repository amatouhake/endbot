"""Plugin-owned per-run Luau processes; all Bot work uses the command worker."""

import re
import shlex
import threading
import time
from collections import deque
from dataclasses import dataclass, field, replace
from pathlib import Path
from uuid import uuid4

from endstone_endbot import script_forms
from endstone_endbot.commands import (
    DIM,
    ERROR,
    INPUT_JOBS,
    RESET,
    CommandResult,
    _language,
    error_code,
    reply,
    strip_colors,
)
from endstone_endbot.control import bot_profiles
from endstone_endbot.events import Event, outcome_text, reason
from endstone_endbot.group_forms import GroupGuard
from endstone_endbot.i18n import LocalizedError, Text, localize_error, tr
from endstone_endbot.operations import OPERATIONS, Operation
from endstone_endbot.script_books import pages_to_source, source_to_pages, written
from endstone_endbot.script_params import arguments, declarations
from endstone_endbot.script_process import RunnerProcess
from endstone_endbot.script_store import FILE_SUFFIX, Script, load_files

# The operator's script folder in the plugin data folder.
FILES_DIRECTORY = "scripts"
# Inspection errors that come from the host, not the inspected source: `reload` keeps such a file.
UNCHECKED = frozenset({
    "Script concurrency limit reached",
    "Script authority was revoked",
    "Script service is closed",
    "scripting runner not built/installed",
    "Script runner OS confinement failed; see the server log",
    "Script runner process memory allocation failed",
})
# Per-run log budget: logs past it are dropped (one notice), so a script cannot
# fill the server log at the IPC rate for hours.
LOG_LINES = 2000
LOG_BYTES = 512 * 1024
# The starter's chat shows at most this many log lines per window per run (events.CHAT_LOG_CHARS cuts each).
CHAT_LOG_LINES = 10
CHAT_LOG_SECONDS = 5
SCRIPT_TARGET = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,15}|#[A-Za-z_][A-Za-z0-9_]{0,15}(?:\[[1-9][0-9]*\])?")


def _clean(text, limit):
    """Script-controlled text for one log/chat line: no colors, control characters (CR/LF/ESC) or overflow."""
    return re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", strip_colors(str(text)))[:limit]


@dataclass
class ScriptRun:
    identity: str
    name: str
    source: object
    canceled: threading.Event = field(default_factory=threading.Event)
    deadline: float | None = None
    state: str = "running"
    guards: dict = field(default_factory=dict)
    calls: deque = field(default_factory=deque)
    thread: object = None
    parameter_targets: dict = field(default_factory=dict)
    bot_pins: dict = field(default_factory=dict)
    log_lines: int = 0
    log_bytes: int = 0
    log_dropped: bool = False
    # Log lines in the starter's chat: the current window's start and count, and lines held back.
    chat_window: float = 0.0
    chat_lines: int = 0
    chat_skipped: int = 0


class ScriptService:
    def __init__(
        self, config, store, service, inputs, data_folder, authorize, *, process_factory=RunnerProcess, bootstrap=None
    ):
        self.config, self.store, self.service, self.inputs = config, store, service, inputs
        self.executable = config.executable(data_folder)
        # The operator's `<name>.luau` files, loaded at start-up and on `reload`.
        self.files_directory = Path(data_folder) / FILES_DIRECTORY
        self.authorize, self.process_factory = authorize, process_factory
        self.bootstrap = bootstrap
        self._runs = {}
        self._inspecting = {}  # controller -> declaration checks holding a run slot
        self._lock = threading.RLock()
        self._open = True
        self.ordered = None
        self.notify = None
        # Publishes an Event to its sinks (chat, console log); set by the command runner.
        self.publish = None

    def _enabled(self):
        if not self.config.enabled:
            raise LocalizedError("Scripting is disabled; enable it in config.toml and restart")
        if not self._open:
            raise LocalizedError("Script service is closed")
        if self.store is None:
            raise LocalizedError("Scripts are unavailable; see the server log")

    def _check(self, source):
        if not self._open or not self.authorize(source):
            raise LocalizedError("Script authority was revoked")

    def _alive(self, run):
        self._check(run.source)
        if run.canceled.is_set() or (run.deadline is not None and time.monotonic() >= run.deadline):
            raise LocalizedError("Script stopped or time limit reached")

    def _process(self, source, wall_seconds=None):
        self._enabled()
        bootstrap = self.bootstrap
        if bootstrap is None:
            try:
                bootstrap = self.executable.with_name("api.luau").read_text(encoding="utf-8")
            except OSError as error:
                raise LocalizedError("scripting runner not built/installed") from error
        limits = self.config.limits()
        if wall_seconds is not None:
            limits["wallMs"] = wall_seconds * 1000
        return self.process_factory(self.executable, source, bootstrap, OPERATIONS, limits)

    def _schema(self, process, source, canceled, deadline):
        event = process.receive(deadline, canceled, lambda: self._check(source))
        if event.get("type") != "params" or event.get("id") != 1:
            if event.get("type") == "done":
                # The whole script ran without declaring its parameters.
                raise LocalizedError("Start the script with params {} (or params { param... }) before other code")
            message = _clean(event.get("message", event.get("type")), 1024)
            match = re.fullmatch(r"(script:\d+): (.*)", message)
            if match and match[2] in {
                "Script parameters must be an array", "Invalid script parameter declarations",
                "Invalid script parameter name", "Duplicate script parameter name",
                "Unknown script parameter type", "Invalid script parameter default",
            }:
                raise LocalizedError(
                    "Script parameter declaration at {location}: {error}", location=match[1], error=Text(match[2])
                )
            raise LocalizedError(
                "Script declaration failed: {error}", error=message
            )
        schema = event.get("schema")
        declarations(schema)
        return schema

    def _reserve(self, source):
        """Check the concurrency limits; the caller holds the lock and then takes the slot."""
        controller = self._controller(source)
        running = len(self._runs) + sum(self._inspecting.values())
        mine = len(self.active(source)) + self._inspecting.get(controller, 0)
        if running >= self.config.server_runs or mine >= self.config.controller_runs:
            raise LocalizedError("Script concurrency limit reached")
        return controller

    def inspect(self, script, source):
        # Inspection executes only up to params and never accepts body operations.
        # It holds a run slot meanwhile, so concurrent checks cannot exceed the limits.
        with self._lock:
            controller = self._reserve(source)
            self._inspecting[controller] = self._inspecting.get(controller, 0) + 1
        try:
            seconds = min(5, self.config.wall_seconds or 5)
            process = self._process(script.source, seconds)
            try:
                return self._schema(process, source, threading.Event(), time.monotonic() + seconds)
            finally:
                process.close()
        finally:
            with self._lock:
                self._inspecting[controller] -= 1
                if not self._inspecting[controller]:
                    del self._inspecting[controller]

    def active(self, source=None, name=None):
        with self._lock:
            return tuple(
                run
                for run in self._runs.values()
                if (source is None or self._controller(run.source) == self._controller(source))
                and (name is None or run.name.casefold() == name.casefold())
            )

    @staticmethod
    def _controller(source):
        return source.player_id or "console"

    def visible(self, source):
        """Runs a source may list and stop: its own, or every run for the console (the operator)."""
        return self.active() if source.player_id is None else self.active(source)

    @staticmethod
    def _who(source):
        return source.name or ("console" if source.player_id is None else source.player_id)

    def _owner(self, run):
        return self._who(run.source)

    @staticmethod
    def owner_key(source):
        """The stable owner identity of a controller: XUID when known, else the player UUID."""
        if source.player_id is None:
            return "console"
        return f"xuid:{source.xuid}" if source.xuid else f"player:{source.player_id}"

    def can_edit(self, script, source):
        """The owner or the console; an unowned (pre-ownership) script is claimed by its next editor.

        Nobody edits a file script in game: its file is the source.
        """
        if script.file:
            return False
        return source.player_id is None or script.owner is None or script.owner == self.owner_key(source)

    def can_run(self, script, source):
        return script.visibility == "shared" or self.can_edit(script, source)

    def _visible(self, source):
        return tuple(script for script in self.store.list() if self.can_run(script, source))

    def _script(self, name, source, *, edit=False):
        script = self.store.get(name)
        if not self.can_run(script, source):
            # A private script of another controller is indistinguishable from a missing one.
            raise LocalizedError("Script {name} does not exist", name=name)
        if edit and script.file:
            raise LocalizedError("Script {name} comes from a file; edit the file, then /botscript reload",
                                 name=script.name)
        if edit and not self.can_edit(script, source):
            raise LocalizedError("Only the owner {owner} may change script {name}; use Copy and edit",
                                 owner=script.owner_name or "?", name=script.name)
        return script

    def reload_files(self, source=None):
        """Read the operator's script folder again; with a ``source``, also check each file's declaration.

        Returns ``(loaded names, problems)`` where a problem is ``(file or script name, error)``.
        Running scripts keep the source they started with.
        """
        files, problems = load_files(self.files_directory, self.config)
        if source is not None:
            # A stored script wins a name clash; there is no point checking a file that will not load.
            stored = {script.name.casefold() for script in self.store.list() if not script.file}
            for key, script in list(files.items()):
                if key in stored:
                    continue
                try:
                    self.inspect(script, source)
                except LocalizedError as error:
                    if error.template in UNCHECKED:
                        # Busy run slots, revoked authority or a missing runner say nothing about the file.
                        problems.append((script.name + FILE_SUFFIX, LocalizedError(
                            "Loaded without a declaration check: {error}", error=Text(error.template))))
                        continue
                    problems.append((script.name + FILE_SUFFIX, error))
                    del files[key]
        shadowed = self.store.set_files(files)
        for name in shadowed:
            # The console (and the start-up log) is the operator, who may need the owner to sort it out.
            stored = self.store.get(name)
            problems.append((name + FILE_SUFFIX, LocalizedError(
                "Not loaded: a stored script named {name} already exists (owner {owner})", name=stored.name,
                owner=stored.owner_name or "?") if source is None or source.player_id is None else LocalizedError(
                "Not loaded: a stored script named {name} already exists", name=stored.name)))
        return tuple(sorted(script.name for script in files.values() if script.name not in shadowed)), problems

    def _new_name(self, name):
        try:
            self.store.get(name)
        except LocalizedError:
            return
        raise LocalizedError("Script {name} already exists", name=name)

    def start(self, script, values, source, targets=None):
        self._enabled()
        self._check(source)
        if len(script.source.encode("utf-8")) > self.config.max_script_bytes:
            raise LocalizedError("Script source is empty or too large")
        with self._lock:
            self._reserve(source)
            identity = uuid4().hex[:12]
            source = replace(source, kind="run", run_id=identity, group_guard=None)
            run = ScriptRun(
                identity,
                script.name,
                source,
                deadline=time.monotonic() + self.config.wall_seconds if self.config.wall_seconds else None,
                parameter_targets=dict(targets or {}),
            )
            self._runs[identity] = run
            run.thread = threading.Thread(
                target=self._run, args=(run, script, values), name="endbot-script-" + identity, daemon=True
            )
            run.thread.start()
        return identity

    def _prepare(self, run, schema, values):
        self._alive(run)
        with self.inputs.scope(run.identity, lambda: self._alive(run)):
            bots = bot_profiles(self.inputs)
        for target in run.parameter_targets.values():
            kind, name, identity, *members = target
            if kind == "group":
                current = self.service._store().get(name[1:])
                matches = current.identity == identity and current.members == members[0]
            else:
                matches = any(bot["name"] == name and bot["identityId"] == identity for bot in bots)
            if not matches:
                raise LocalizedError("Script parameter target changed; reopen the menu")
        result = arguments(schema, values, groups=self.service.groups, bots=bots, origin=run.source.origin)
        # Pin declared groups now; later membership/name reuse cannot retarget the run.
        for name, spec in declarations(schema).items():
            if spec["type"] == "group":
                self._guard(run, result[name])
            elif spec["type"] == "bot":
                bot = next(bot for bot in bots if bot["name"] == result[name])
                run.bot_pins[bot["name"].casefold()] = bot["identityId"]
        return result

    def _guard(self, run, target):
        if not isinstance(target, str) or not target.startswith("#"):
            return None
        group, _, _ = self.service._store().select(target)
        key = group.name.casefold()
        guard = run.guards.setdefault(key, GroupGuard(group.identity, group.name, group.members))
        self.service._form_group(replace(run.source, group_guard=guard), group)
        return guard

    def _reply_chat(self, result) -> bool:
        """Whether this operation is echoed to the invoker's chat (``[scripting] replies``)."""
        mode = self.config.replies
        return mode == "all" or (mode == "failures" and not result.accepted)

    def _operation(self, run, event, *, audit=True):
        self._alive(run)
        now = time.monotonic()
        while run.calls and run.calls[0] <= now - 1:
            run.calls.popleft()
        if len(run.calls) >= self.config.operations_per_second:
            raise LocalizedError("Script operation rate limit reached")
        run.calls.append(now)
        target = event.get("target")
        guard = self._guard(run, target)
        source = replace(run.source, group_guard=guard)
        with self.inputs.scope(run.identity, lambda: self._alive(run), run.bot_pins):
            if event["type"] == "members":
                if guard is None or target.casefold() != "#" + guard.name.casefold():
                    raise LocalizedError("Invalid script group target")
                return {"count": len(guard.members)}
            if event["type"] == "waitOnline":
                if not isinstance(target, str) or not SCRIPT_TARGET.fullmatch(target):
                    raise LocalizedError("Invalid script target")
                if guard is not None:
                    _, members, _ = self.service._store().select(target)
                    profiles = self.service._profiles(source)
                    online = bool(members) and all(
                        profiles.get(identity, {}).get("connectionState") == "online" for _, identity in members
                    )
                else:
                    online = self.inputs.request("status", name=target).get("connectionState") == "online"
                return {"ok": online}
            try:
                operation = Operation(event.get("operation"), target, event.get("params"))
                result = self.service.execute_operation(operation, source)
            except (RuntimeError, ValueError) as error:
                return {"result": {"ok": False, "code": error_code(error), "reason": localize_error(error, "en")}}
            if audit and self.notify:
                # Preserve existing command audit, even if the controller logged out.
                self.notify(source, result, chat=self._reply_chat(result))
            job = self._job(result.data)
            return {
                "result": {
                    "ok": result.accepted,
                    "code": result.code,
                    **({"job": job} if job else {}),
                    "data": result.data,
                    "messages": [strip_colors(m) for m in result.messages],
                    **({"reason": strip_colors(result.message or "")} if not result.accepted else {}),
                }
            }

    @staticmethod
    def _job(data):
        """The job a result started, as job:wait() sends it back: its kind and each member's job id."""
        if not isinstance(data, dict):
            return None
        rows = data.get("jobs") or ([{"identityId": data.get("identityId"), **data["job"]}]
                                    if isinstance(data.get("job"), dict) else [])
        kinds = {row["kind"] for row in rows}
        if len(kinds) != 1:
            return None
        kind = kinds.pop()
        return {"kind": kind, **({} if kind == "mining" else
                                 {"ids": {row["identityId"]: row["id"] for row in rows}})}

    def _wait_job(self, run, event):
        """Wait until the job an operation returned ends on every member, or the timeout."""
        job = event.get("job")
        kind = job.get("kind") if isinstance(job, dict) else None
        if kind == "mining":
            return self._wait_mining(run, event)
        ids = job.get("ids") if kind in (*INPUT_JOBS, "tp") else None
        # The ids come back through Luau and the runner's JSON, where 7 may arrive as 7.0.
        if not isinstance(ids, dict) or not ids or not all(
                isinstance(key, str) and type(value) in (int, float) and math_is_integer(value)
                for key, value in ids.items()):
            raise LocalizedError("Invalid script wait")
        ids = {key: int(value) for key, value in ids.items()}

        def settle(status, expired):
            expected = ids.get(status.get("identityId"))
            if expected is None:
                return False, False, "no job"
            if kind == "tp":
                if (status.get("teleports") or 0) >= expected:
                    return False, True, "arrived"
                return True, False, "timeout" if expired else "active"
            if (status.get("running") or {}).get(kind) == expected:
                return True, False, "timeout" if expired else "active"
            ended = (status.get("ended") or {}).get(kind) or {}
            # A later job of the same channel may have ended since; its reason is not this job's.
            return False, True, ended.get("reason") if ended.get("job") == expected else "ended"

        return self._wait_status(run, event, settle)

    def _wait_mining(self, run, event):
        def settle(status, expired):
            mining = status.get("mining") or {}
            if mining.get("active"):
                return True, False, "timeout" if expired else "active"
            return False, mining.get("state") != "failed", mining.get("reason") or "idle"

        return self._wait_status(run, event, settle, "mining")

    def _wait_status(self, run, event, settle, detail=None):
        """Poll the target's status until ``settle`` reports nothing pending, or the timeout.

        ``settle(status, expired)`` returns (pending, ok, reason) for one online member. The result is the common
        wait shape: ok, code ("ok", "timeout" or the failure's), reason, and one row per member.
        """
        ticks = event.get("timeout", 1200)
        if type(ticks) not in (int, float) or not math_is_integer(ticks) or not 0 <= ticks <= 2147483647:
            raise LocalizedError("Invalid script wait")
        end = time.monotonic() + ticks / 20
        # Every poll re-enters the ordered, UUID-pinned status path. Never hold
        # the command worker while sleeping; mining retargeting needs it too.
        query = {"type": "operation", "target": event.get("target"), "operation": "status", "params": {}}
        payload = None
        while True:
            # At timeout use the last snapshot, avoiding a final rapid poll that
            # could itself exceed a low configured operation rate.
            if payload is None or time.monotonic() < end:
                payload = self.ordered(lambda: self._operation(run, query, audit=False))["result"]
            data = payload.get("data") or {}
            rows = data.get("results")
            if rows is None:
                if data.get("identityId") and not query["target"].startswith("#"):
                    run.bot_pins.setdefault(query["target"].casefold(), data["identityId"])
                rows = [{"name": data.get("name", query["target"]), "ok": payload["ok"],
                         "reason": payload.get("reason"), "data": data}]
            results, pending = [], False
            expired = time.monotonic() >= end
            for row in rows:
                status = row.get("data") or {}
                if not row["ok"]:
                    reason, ok, code = row.get("reason") or "status failed", False, row.get("code") or "unavailable"
                elif status.get("connectionState") != "online":
                    reason, ok, code = "offline", False, "conflict"
                else:
                    waiting, ok, reason = settle(status, expired)
                    pending |= waiting
                    code = "ok" if ok else "timeout" if reason == "timeout" else "conflict"
                results.append({"name": row["name"], "identityId": status.get("identityId", row.get("identityId")),
                                "ok": ok, "code": code, "reason": reason,
                                **({detail: status.get(detail) or {}} if detail else {})})
            if not pending or expired:
                ok = bool(results) and all(row["ok"] for row in results)
                reasons = {row["reason"] for row in results}
                reason = (next(iter(reasons)) if len(reasons) == 1 else
                          ("completed" if ok else next((row["reason"] for row in results if not row["ok"]), "idle")))
                code = "ok" if ok else next(row["code"] for row in results if not row["ok"]) if results else "not_found"
                self._alive(run)
                return {"ok": ok, "code": code, "reason": reason, "results": results}
            self._wait(run, min(max(0.25, 1 / self.config.operations_per_second), max(0, end - time.monotonic())))

    def _wait(self, run, seconds):
        end = time.monotonic() + seconds
        if run.deadline is not None:
            end = min(end, run.deadline)
        while time.monotonic() < end:
            self._alive(run)
            run.canceled.wait(min(0.1, end - time.monotonic()))
        self._alive(run)

    def _run(self, run, script, values):
        process, outcome = None, reason("completed")
        try:
            process = self._process(script.source)
            schema = self._schema(process, run.source, run.canceled, run.deadline)
            args = self.ordered(lambda: self._prepare(run, schema, values))
            self._alive(run)
            process.send({"type": "reply", "id": 1, "accepted": True, "args": args})
            next_id, messages = 2, deque()
            while True:
                event = process.receive(run.deadline, run.canceled, lambda: self._alive(run))
                now = time.monotonic()
                while messages and messages[0] <= now - 1:
                    messages.popleft()
                messages.append(now)
                # Logs and zero waits also consume the sliding one-second IPC budget.
                if len(messages) > self.config.operations_per_second * 4:
                    raise LocalizedError("Script operation rate limit reached")
                kind = event.get("type")
                if kind == "done":
                    break
                if kind == "error":
                    raise LocalizedError("Script runner failed: {error}", error=_clean(event.get("message", ""), 1024))
                if kind == "log":
                    text = _clean(event.get("text", ""), 4096)
                    run.log_lines += 1
                    run.log_bytes += len(text.encode("utf-8"))
                    over = run.log_lines > LOG_LINES or run.log_bytes > LOG_BYTES
                    if self.publish and not (over and run.log_dropped):
                        run.log_dropped = over
                        shown = self._chat_log(run)
                        self.publish(Event(run.source, "run.log", {
                            "run": run.identity, "name": run.name, "text": text, "limited": over, "chat": shown,
                            "skipped": self._skipped_logs(run) if shown else 0}))
                    continue
                if type(event.get("id")) is not int or event["id"] != next_id:
                    raise LocalizedError("Invalid script runner request")
                next_id += 1
                payload = {}
                if kind == "wait":
                    ticks = event.get("ticks")
                    if type(ticks) not in (int, float) or ticks < 0 or not math_is_integer(ticks):
                        raise LocalizedError("Invalid script wait")
                    self._wait(run, ticks / 20)
                elif kind == "waitMining":
                    payload = {"result": self._wait_mining(run, event)}
                elif kind == "waitJob":
                    payload = {"result": self._wait_job(run, event)}
                elif kind in {"operation", "members", "waitOnline"}:
                    # waitOnline(b) waits without end; b:waitOnline{timeout = ...} gives up after its ticks.
                    timeout = event.get("timeout") if kind == "waitOnline" else None
                    if timeout is not None and (type(timeout) not in (int, float) or not math_is_integer(timeout)
                                                or not 0 <= timeout <= 2147483647):
                        raise LocalizedError("Invalid script wait")
                    end = None if timeout is None else time.monotonic() + timeout / 20
                    payload = self.ordered(lambda event=event: self._operation(run, event))
                    while kind == "waitOnline" and not payload["ok"]:
                        if end is not None and time.monotonic() >= end:
                            payload = {"ok": False, "reason": "timeout"}
                            break
                        pause = max(0.25, 1 / self.config.operations_per_second)
                        self._wait(run, pause if end is None else min(pause, max(0, end - time.monotonic())))
                        payload = self.ordered(lambda event=event: self._operation(run, event))
                    if kind == "waitOnline":
                        payload = {"result": {**payload, "code": "ok" if payload["ok"] else "timeout",
                                              "reason": payload.get("reason") or ("online" if payload["ok"]
                                                                                   else "timeout")}}
                else:
                    raise LocalizedError("Invalid script runner request")
                self._alive(run)
                process.send({"type": "reply", "id": event["id"], "accepted": True, **payload})
        except Exception as error:  # noqa: BLE001 - isolate failures to this run
            outcome = reason(error)
        finally:
            cleanup_ok = False
            if process is not None:
                try:
                    process.close()
                except Exception as error:  # noqa: BLE001 - final cleanup must release the run slot
                    outcome = reason(error)
            try:
                cleanup_ok = not self.ordered(lambda: self.inputs.cleanup(run.identity))
            except Exception:  # noqa: BLE001 - record cleanup failure and always retire the run
                cleanup_ok = False
            ended = Event(run.source, "run.ended", {
                "run": run.identity, "name": run.name, "outcome": outcome, "cleanup": cleanup_ok,
                "controller": self._owner(run), "skipped": self._skipped_logs(run)})
            with self._lock:
                run.state = outcome_text(ended.data, _language(run.source))
                self._runs.pop(run.identity, None)
            if self.publish:
                self.publish(ended)

    def _chat_log(self, run) -> bool:
        """Whether the chat of the player who started the run shows this log line.

        The console log always has every line; chat gets at most CHAT_LOG_LINES per CHAT_LOG_SECONDS per run,
        counting the rest for the next line it shows. Formatting codes are removed so a script's text cannot
        pass for a message of Endbot or another plugin; the run.log renderer cuts each line.
        """
        if not self.config.chat_logs or run.source.player_id is None:
            return False
        now = time.monotonic()
        if now - run.chat_window >= CHAT_LOG_SECONDS:
            run.chat_window, run.chat_lines = now, 0
        if run.chat_lines >= CHAT_LOG_LINES:
            run.chat_skipped += 1
            return False
        run.chat_lines += 1
        return True

    @staticmethod
    def _skipped_logs(run) -> int:
        """The count of log lines chat did not show since the last one it did, then reset."""
        skipped, run.chat_skipped = run.chat_skipped, 0
        return skipped

    def stop(self, source, selector):
        """Stop runs by id, script name or `all`; the console may stop any controller's runs."""
        selected = [
            run for run in self.visible(source)
            if not run.canceled.is_set()
            and selector.casefold() in {"all", run.identity, run.name.casefold()}
        ]
        for run in selected:
            run.canceled.set()
        return len(selected)

    def cancel(self):
        self._open = False
        for run in self.active():
            run.canceled.set()

    def close(self):
        self.cancel()
        for run in self.active():
            # Called on the command worker after all earlier operations.
            self.inputs.cleanup(run.identity)

    def execute(self, argv, source):
        lang = _language(source)
        try:
            self._enabled()
            self._check(source)
            # Unlike /bot, form source/argument text is never recursively tokenized.
            operation, tail = (argv[0].lower(), argv[1:]) if argv else ("menu", [])
            guard = getattr(source, "group_guard", None)
            if not isinstance(guard, script_forms.ScriptGuard) or (not guard.edit and guard.values is None):
                tokens = shlex.split(" ".join(argv))
                operation, tail = (tokens[0].lower(), tokens[1:]) if tokens else ("menu", [])
            if operation == "help" and not tail:
                return CommandResult(
                    True,
                    (
                        reply(
                            tr(
                                lang,
                                "Script help: list | show <name> [page] | run <name> [args...] "
                                "| runs | stop <id|name|all> | delete <name> | copy <name> <new> "
                                "| share|private <name> | history <name> | restore <name> <n> "
                                "| rename <name> <new> | import <name> | export <name> [overwrite] | reload | menu",
                            )
                        ),
                        reply(
                            tr(
                                lang,
                                "Arguments follow declaration order, then name=value for any parameter; "
                                "quote positions. Create/edit in the menu, or import from a held book and quill.",
                            )
                        ),
                    ),
                )
            if operation == "list" and not tail:
                text = ", ".join(script_forms.label(script, script.owner == self.owner_key(source), lang)
                                 for script in self._visible(source))
                return CommandResult(True, (reply(text or tr(lang, "No stored scripts")),))
            if operation == "runs" and not tail:
                # The console sees every controller's runs, with their owners, to stop them.
                text = ", ".join(
                    f"{r.identity} {r.name}" + (f" ({self._owner(r)})" if source.player_id is None else "")
                    for r in self.visible(source)
                ) or tr(lang, "No active script runs")
                return CommandResult(True, (reply(text),))
            if operation == "reload" and not tail:
                loaded, problems = self.reload_files(source)
                lines = [tr(lang, "Loaded {count} script files from {folder}", count=len(loaded),
                            folder=f"plugins/endbot/{FILES_DIRECTORY}/")]
                lines += [f"{name}: {localize_error(error, lang)}" for name, error in problems]
                english = [f"{name}: {error}" for name, error in problems]
                return CommandResult(
                    True, tuple(reply(line, color=ERROR) if index else reply(line) for index, line in enumerate(lines)),
                    log_level="info",
                    log_messages=(f"Script files reloaded by {self._who(source)}: {len(loaded)} loaded"
                                  + "".join(f"; {line}" for line in english),))
            if operation == "stop" and len(tail) == 1:
                count = self.stop(source, tail[0])
                return CommandResult(
                    True, (reply(tr(lang, "Stopping {count} script runs", count=count)),), log_level="info",
                    log_messages=(f"Stopping {count} script runs ({_clean(tail[0], 64)}) by {self._who(source)}",),
                )
            if (
                operation == "save"
                and len(tail) == 2
                and isinstance(guard, script_forms.ScriptGuard)
                and guard.edit
                and (guard.name is None or guard.name == tail[0])
            ):
                if (
                    not isinstance(tail[1], str)
                    or not tail[1].strip()
                    or len(tail[1].encode("utf-8")) > self.config.max_script_bytes
                ):
                    raise LocalizedError("Script source is empty or too large")
                if guard.revision is None:
                    self._new_name(tail[0])
                else:
                    self._script(tail[0], source, edit=True)
                self.inspect(Script(tail[0], tail[1], "draft"), source)
                self._check(source)
                self.store.save(tail[0], tail[1], guard.revision, self.owner_key(source), self._who(source))
                # Menus continue where the save came from: the line editor page, or the new script.
                saved = self.store.get(tail[0])
                view = None
                if source.player_id is not None:
                    view = (script_forms.lines_screen(saved, min(guard.page, script_forms.edit_pages(saved)), lang,
                                                       self.config.ui_pack_editor)
                            if guard.page is not None
                            else script_forms.script_screen(saved, lang, True, self.config.book_export))
                return CommandResult(True, (reply(tr(lang, "Script {name} saved", name=tail[0])),), log_level="info",
                                     log_messages=(f"Script {tail[0]} saved by {self._who(source)}",), view=view)
            if operation == "menu" and not tail:
                self._player(source)
                return CommandResult(True, view=script_forms.list_screen(
                    self._visible(source), lang, own=lambda script: script.owner == self.owner_key(source)))
            if not tail:
                raise LocalizedError("Use /botscript help")
            if operation == "import" and len(tail) == 1:
                # A new name creates the script; an existing one gets a new revision (owner only).
                # Menus never replace a script in one tap: the owner first sees the book's source.
                self._player(source)
                menu = isinstance(guard, script_forms.ScriptGuard)
                text = pages_to_source(self._world().held_book_pages(source.player_id))
                if len(text.encode("utf-8")) > self.config.max_script_bytes:
                    raise LocalizedError("Script source is empty or too large")
                try:
                    existing = self._script(tail[0], source, edit=True)
                except LocalizedError as error:
                    if error.template != "Script {name} does not exist":
                        raise
                    self._new_name(tail[0])
                    existing = None
                if existing is not None and menu:
                    if guard.name is None:
                        # New script from a book: an existing name is never overwritten.
                        raise LocalizedError("Script {name} already exists", name=existing.name)
                    if guard.revision != existing.revision:
                        raise LocalizedError("Script changed; reopen the menu")
                if existing is not None and text == existing.source:
                    # Nothing to replace: no confirmation and no history entry.
                    return CommandResult(True, (reply(tr(lang, "Script {name} already has the book's source",
                                                         name=existing.name)),))
                if existing is not None and menu:
                    if guard.book is None:
                        return CommandResult(True, view=script_forms.import_confirm_screen(existing, text, lang))
                    if guard.book != text:
                        raise LocalizedError("The book changed; check it again")
                revision = existing.revision if existing else None
                name = existing.name if existing else tail[0]
                self.inspect(Script(name, text, "draft"), source)
                self._check(source)
                self.store.save(name, text, revision, self.owner_key(source), self._who(source))
                lines = len(text.splitlines())
                return CommandResult(
                    True, (reply(tr(lang, "Script {name} imported from the book ({lines} lines)", name=name,
                                    lines=lines)),),
                    log_level="info", log_messages=(f"Script {name} imported from a book by {self._who(source)}",),
                    view=script_forms.script_screen(self.store.get(name), lang, True, self.config.book_export)
                    if isinstance(guard, script_forms.ScriptGuard) else None)
            if operation == "copy" and len(tail) == 2:
                # Copies start private and belong to the copier; the original is untouched.
                original = self._script(tail[0], source)
                self._new_name(tail[1])
                self._check(source)
                self.store.save(tail[1], original.source, None, self.owner_key(source), self._who(source))
                return CommandResult(
                    True, (reply(tr(lang, "Script {name} copied to {copy}", name=original.name, copy=tail[1])),),
                    log_level="info",
                    log_messages=(f"Script {original.name} copied to {tail[1]} by {self._who(source)}",))
            script = self._script(tail[0], source,
                                  edit=operation in {"delete", "edit", "share", "private", "restore", "rename"})
            if isinstance(guard, script_forms.ScriptGuard) and guard.revision != script.revision:
                raise LocalizedError("Script changed; reopen the menu")
            if operation in {"show", "edit"} and len(tail) <= 2:
                total = len(script_forms.source_pages(script)) if operation == "show" else script_forms.edit_pages(
                    script)
                page = int(tail[1]) if len(tail) == 2 and tail[1].isascii() and tail[1].isdecimal() else (
                    1 if len(tail) == 1 else 0)
                if not 1 <= page <= total:
                    raise LocalizedError("Script source page is out of range")
                if operation == "edit":
                    self._player(source)
                    return CommandResult(True, view=script_forms.lines_screen(script, page, lang,
                                                                              self.config.ui_pack_editor))
                text = script_forms.source_pages(script)[page - 1]
                return CommandResult(True, (reply(f"{script.name} ({page}/{total})\n{text}"),))
            if operation == "export" and (len(tail) == 1 or len(tail) == 2 and tail[1].lower() == "overwrite"):
                self._player(source)
                if not self.config.book_export:
                    raise LocalizedError("Book export is disabled on this server")
                pages = source_to_pages(script.source)
                # Writing in the book is replaced only with `overwrite`; the menu asks first, showing it,
                # and its confirmation overwrites only the pages it showed.
                current = self._world().held_book_pages(source.player_id, writable=True)
                expected = None
                if written(current):
                    if len(tail) == 1:
                        if isinstance(guard, script_forms.ScriptGuard):
                            return CommandResult(True, view=script_forms.export_confirm_screen(
                                script, current, pages_to_source(current), lang))
                        raise LocalizedError("The book and quill is not empty; hold an empty one or add overwrite")
                    menu_pages = guard.book if isinstance(guard, script_forms.ScriptGuard) else None
                    expected = list(menu_pages) if menu_pages is not None else current
                self._world().fill_held_book(source.player_id, pages, expected)
                return CommandResult(
                    True, (reply(tr(lang, "Script {name} written into the book ({pages} pages)", name=script.name,
                                    pages=len(pages))),),
                    log_level="info",
                    log_messages=(f"Script {script.name} exported to a book by {self._who(source)}",))
            if operation in {"share", "private"} and len(tail) == 1:
                visibility = "shared" if operation == "share" else "private"
                self.store.set_visibility(script.name, visibility, script.revision)
                return CommandResult(
                    True, (reply(tr(lang, "Script {name} is now {visibility}", name=script.name,
                                    visibility=Text(visibility))),),
                    log_level="info",
                    log_messages=(f"Script {script.name} is now {visibility} by {self._who(source)}",))
            if operation == "history" and len(tail) == 1:
                if source.player_id is not None and isinstance(guard, script_forms.ScriptGuard):
                    return CommandResult(True, view=script_forms.history_screen(
                        script, self.can_edit(script, source), lang))
                lines = [script_forms.revision_line(i, r, lang) for i, r in enumerate(script.history, start=1)]
                return CommandResult(True, (reply(f"{script.name}: " + ("; ".join(lines) or tr(
                    lang, "no earlier revisions"))),))
            if operation == "restore" and len(tail) == 2:
                if not tail[1].isascii() or not tail[1].isdecimal() or len(tail[1]) > 3:
                    raise LocalizedError("Script revision is out of range")
                self._check(source)
                self.store.restore(script.name, int(tail[1]), script.revision)
                return CommandResult(
                    True, (reply(tr(lang, "Script {name} restored to revision {index}", name=script.name,
                                    index=int(tail[1]))),),
                    log_level="info",
                    log_messages=(
                        f"Script {script.name} restored to revision {int(tail[1])} by {self._who(source)}",))
            if operation == "rename" and len(tail) == 2:
                # Running copies keep their old name in `runs`; the revision stays, so open menus remain valid.
                self._check(source)
                self.store.rename(script.name, tail[1], script.revision)
                return CommandResult(
                    True, (reply(tr(lang, "Script {name} renamed to {new}", name=script.name, new=tail[1])),),
                    log_level="info",
                    log_messages=(f"Script {script.name} renamed to {tail[1]} by {self._who(source)}",))
            if operation == "delete" and len(tail) == 1:
                self.store.delete(script.name, script.revision)
                return CommandResult(True, (reply(tr(lang, "Script {name} deleted", name=script.name)),),
                                     log_level="info",
                                     log_messages=(f"Script {script.name} deleted by {self._who(source)}",))
            if operation == "run":
                # Native message tails arrive in one argument. Forms carry a JSON object in their guard.
                values = (
                    guard.values
                    if isinstance(guard, script_forms.ScriptGuard) and guard.values is not None
                    else tail[1:]
                )
                targets = guard.targets if isinstance(guard, script_forms.ScriptGuard) else None
                run = self.start(script, values, source, targets)
                return CommandResult(
                    True, (reply(tr(lang, "Script {name} started: {run}", name=script.name, run=run)),),
                    log_level="info",
                    log_messages=(f"Script {script.name} started: {run} by {self._who(source)}",),
                )
            if operation in {"menu", "parameters"} and len(tail) == 1:
                self._player(source)
                if operation == "parameters":
                    schema = self.inspect(script, source)
                    if not schema:
                        # No parameters: an empty form cannot be submitted, so start the run.
                        run = self.start(script, [], source, None)
                        return CommandResult(
                            True,
                            # The menu echoes no internal `parameters` step; show the command this was.
                            (f"{DIM}/botscript run {script.name}{RESET}",
                             reply(tr(lang, "Script {name} started: {run}", name=script.name, run=run))),
                            log_level="info",
                            log_messages=(f"Script {script.name} started: {run} by {self._who(source)}",),
                        )
                    bots = bot_profiles(self.inputs)
                    groups = self.service.groups.groups if self.service.groups else ()
                    view = script_forms.run_screen(script, schema, groups, bots, source.origin, lang)
                else:
                    view = script_forms.script_screen(
                        script, lang, self.can_edit(script, source), self.config.book_export)
                return CommandResult(True, view=view)
            raise LocalizedError("Use /botscript help")
        except (ValueError, RuntimeError, OSError) as error:
            return CommandResult(True, (reply(localize_error(error, lang), color=ERROR),), accepted=False,
                                 code=error_code(error))

    @staticmethod
    def _player(source):
        if source.player_id is None:
            raise LocalizedError("Script menu is available only to players")

    def _world(self):
        world = getattr(self.service, "world", None)
        if world is None:
            raise LocalizedError("Books are unavailable without the world adapter")
        return world


def math_is_integer(value):
    import math

    return math.isfinite(value) and int(value) == value
