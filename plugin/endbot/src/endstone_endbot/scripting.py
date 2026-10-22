"""Plugin-owned per-run Luau processes; all Bot work uses the command worker."""

import re
import shlex
import threading
import time
from collections import deque
from dataclasses import dataclass, field, replace
from uuid import uuid4

from endstone_endbot import script_forms
from endstone_endbot.commands import ERROR, CommandResult, _language, parse_command, reply, strip_colors
from endstone_endbot.group_forms import GroupGuard
from endstone_endbot.i18n import LocalizedError, Text, localize_error, tr
from endstone_endbot.operations import OPERATIONS, operation_argv
from endstone_endbot.script_params import arguments, declarations
from endstone_endbot.script_process import RunnerProcess
from endstone_endbot.script_store import Script


@dataclass
class ScriptRun:
    identity: str
    name: str
    source: object
    canceled: threading.Event = field(default_factory=threading.Event)
    deadline: float = 0
    state: str = "running"
    guards: dict = field(default_factory=dict)
    calls: deque = field(default_factory=deque)
    thread: object = None
    parameter_targets: dict = field(default_factory=dict)
    bot_pins: dict = field(default_factory=dict)


class ScriptService:
    def __init__(
        self, config, store, service, inputs, data_folder, authorize, *, process_factory=RunnerProcess, bootstrap=None
    ):
        self.config, self.store, self.service, self.inputs = config, store, service, inputs
        self.executable = config.executable(data_folder)
        self.authorize, self.process_factory = authorize, process_factory
        self.bootstrap = bootstrap
        self._runs = {}
        self._lock = threading.RLock()
        self._open = True
        self.ordered = None
        self.notify = None

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
        if run.canceled.is_set() or time.monotonic() >= run.deadline:
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
            message = str(event.get("message", event.get("type")))
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

    def inspect(self, script, source):
        # Inspection executes only up to params and never accepts body operations.
        with self._lock:
            if len(self._runs) >= self.config.server_runs or len(self.active(source)) >= self.config.controller_runs:
                raise LocalizedError("Script concurrency limit reached")
        process = self._process(script.source, min(5, self.config.wall_seconds))
        try:
            return self._schema(process, source, threading.Event(), time.monotonic() + min(5, self.config.wall_seconds))
        finally:
            process.close()

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

    def start(self, script, values, source, targets=None):
        self._enabled()
        self._check(source)
        if len(script.source.encode("utf-8")) > self.config.max_script_bytes:
            raise LocalizedError("Script source is empty or too large")
        with self._lock:
            if len(self._runs) >= self.config.server_runs or len(self.active(source)) >= self.config.controller_runs:
                raise LocalizedError("Script concurrency limit reached")
            identity = uuid4().hex[:12]
            source = replace(source, script_run_id=identity, group_guard=None)
            run = ScriptRun(
                identity,
                script.name,
                source,
                deadline=time.monotonic() + self.config.wall_seconds,
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
            bots = self.inputs.request("list")
        for target in run.parameter_targets.values():
            kind, name, identity, *members = target
            if kind == "group":
                current = self.service._store().get(name[1:])
                matches = current.identity == identity and current.members == members[0]
            else:
                matches = any(bot["name"] == name and bot["identityId"] == identity for bot in bots)
            if not matches:
                raise LocalizedError("Script parameter target changed; reopen the menu")
        result = arguments(schema, values, groups=self.service.groups, bots=bots, origin=run.source.script_origin)
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

    def _operation(self, run, event):
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
                argv = operation_argv(target, event.get("operation"), event.get("params"))
                parse_command(argv)  # Semantic combinations use exactly the command grammar.
                result = self.service.execute(argv, source)
            except (RuntimeError, ValueError) as error:
                return {"result": {"ok": False, "reason": localize_error(error, "en")}}
            if self.notify:
                # Preserve existing command audit, even if the controller logged out.
                self.notify(source, result)
            return {
                "result": {
                    "ok": result.accepted,
                    "data": result.data,
                    "messages": [strip_colors(m) for m in result.messages],
                    **({"reason": strip_colors(result.message or "")} if not result.accepted else {}),
                }
            }

    def _wait(self, run, seconds):
        end = min(time.monotonic() + seconds, run.deadline)
        while time.monotonic() < end:
            self._alive(run)
            run.canceled.wait(min(0.1, end - time.monotonic()))
        self._alive(run)

    def _run(self, run, script, values):
        process, outcome_en = None, "completed"
        outcome = tr(_language(run.source), "completed")
        try:
            process = self._process(script.source)
            schema = self._schema(process, run.source, run.canceled, run.deadline)
            args = self.ordered(lambda: self._prepare(run, schema, values))
            self._alive(run)
            process.send({"type": "reply", "id": 1, "accepted": True, "args": args})
            next_id, messages = 2, 0
            while True:
                event = process.receive(run.deadline, run.canceled, lambda: self._alive(run))
                messages += 1
                # Bound total IPC traffic using the configured operation/time limits.
                if messages > self.config.operations_per_second * self.config.wall_seconds * 4:
                    raise LocalizedError("Script operation rate limit reached")
                kind = event.get("type")
                if kind == "done":
                    break
                if kind == "error":
                    raise LocalizedError("Script runner failed: {error}", error=str(event.get("message", ""))[:1024])
                if kind == "log":
                    if self.notify:
                        text = strip_colors(str(event.get("text", "")))[:4096].replace("\r", " ").replace("\n", " ")
                        self.notify(
                            run.source,
                            CommandResult(
                                True,
                                (),
                                log_level="info",
                                log_messages=(f"Endbot script {run.identity}: {text}",),
                            ),
                        )
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
                elif kind in {"operation", "members", "waitOnline"}:
                    payload = self.ordered(lambda event=event: self._operation(run, event))
                    while kind == "waitOnline" and not payload["ok"]:
                        self._wait(run, max(0.25, 1 / self.config.operations_per_second))
                        payload = self.ordered(lambda event=event: self._operation(run, event))
                    if kind == "waitOnline":
                        payload = {"result": payload}
                else:
                    raise LocalizedError("Invalid script runner request")
                self._alive(run)
                process.send({"type": "reply", "id": event["id"], "accepted": True, **payload})
        except Exception as error:  # noqa: BLE001 - isolate failures to this run
            outcome = localize_error(error, _language(run.source))
            outcome_en = localize_error(error, "en")
        finally:
            cleanup_ok = False
            if process is not None:
                try:
                    process.close()
                except Exception as error:  # noqa: BLE001 - final cleanup must release the run slot
                    outcome = localize_error(error, _language(run.source))
                    outcome_en = localize_error(error, "en")
            try:
                failures = self.ordered(lambda: self.inputs.cleanup(run.identity))
                cleanup_ok = not failures
                if failures:
                    outcome += "; " + tr(_language(run.source), "Script input cleanup failed; use /bot stop")
                    outcome_en += "; Script input cleanup failed; use /bot stop"
            except Exception:  # noqa: BLE001 - record cleanup failure and always retire the run
                outcome += "; " + tr(_language(run.source), "Script input cleanup failed; use /bot stop")
                outcome_en += "; Script input cleanup failed; use /bot stop"
            with self._lock:
                run.state = outcome
                self._runs.pop(run.identity, None)
            if self.notify:
                self.notify(
                    run.source,
                    CommandResult(
                        True,
                        (
                            reply(
                                tr(
                                    _language(run.source),
                                    "Script {name} ({run}) ended: {reason}",
                                    name=run.name,
                                    run=run.identity,
                                    reason=outcome,
                                )
                            ),
                        ),
                        log_level="info",
                        log_messages=(
                            f"Endbot script {run.identity} {run.name} ended: {outcome_en}",
                            f"Endbot script {run.identity}: input cleanup {'completed' if cleanup_ok else 'failed'}",
                        ),
                    ),
                )

    def stop(self, source, selector):
        selected = [
            run for run in self.active(source) if run.identity == selector or run.name.casefold() == selector.casefold()
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
                                "| runs | stop <id|name> | delete <name> | menu",
                            )
                        ),
                        reply(
                            tr(
                                lang,
                                "Arguments follow parameter declaration order; quote positions. "
                                "Create/edit by menu paste only.",
                            )
                        ),
                    ),
                )
            if operation == "list" and not tail:
                text = ", ".join(s.name for s in self.store.list()) or tr(lang, "No stored scripts")
                return CommandResult(True, (reply(text),))
            if operation == "runs" and not tail:
                text = ", ".join(f"{r.identity} {r.name}" for r in self.active(source)) or tr(
                    lang, "No active script runs"
                )
                return CommandResult(True, (reply(text),))
            if operation == "stop" and len(tail) == 1:
                return CommandResult(
                    True, (reply(tr(lang, "Stopping {count} script runs", count=self.stop(source, tail[0]))),)
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
                self.inspect(Script(tail[0], tail[1], "draft"), source)
                self._check(source)
                self.store.save(tail[0], tail[1], guard.revision)
                return CommandResult(True, (reply(tr(lang, "Script {name} saved", name=tail[0])),))
            if operation == "menu" and not tail:
                self._player(source)
                return CommandResult(True, view=script_forms.list_screen(self.store.list(), lang))
            if not tail:
                raise LocalizedError("Use /botscript help")
            script = self.store.get(tail[0])
            if isinstance(guard, script_forms.ScriptGuard) and guard.revision != script.revision:
                raise LocalizedError("Script changed; reopen the menu")
            if operation in {"show", "source"} and len(tail) <= 2:
                page = int(tail[1]) if len(tail) == 2 else 1
                pages = script_forms.source_pages(script)
                total = len(pages)
                if not 1 <= page <= total:
                    raise LocalizedError("Script source page is out of range")
                if operation == "source":
                    self._player(source)
                    return CommandResult(True, view=script_forms.source_screen(script, page, lang))
                text = pages[page - 1]
                return CommandResult(True, (reply(f"{script.name} ({page}/{total})\n{text}"),))
            if operation == "delete" and len(tail) == 1:
                self.store.delete(script.name, script.revision)
                return CommandResult(True, (reply(tr(lang, "Script {name} deleted", name=script.name)),))
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
                    True, (reply(tr(lang, "Script {name} started: {run}", name=script.name, run=run)),)
                )
            if operation in {"menu", "edit", "parameters"} and len(tail) == 1:
                self._player(source)
                if operation == "edit":
                    view = script_forms.edit_screen(script, lang)
                elif operation == "parameters":
                    schema = self.inspect(script, source)
                    bots = self.inputs.request("list")
                    groups = self.service.groups.groups if self.service.groups else ()
                    view = script_forms.run_screen(script, schema, groups, bots, source.script_origin, lang)
                else:
                    view = script_forms.script_screen(script, lang)
                return CommandResult(True, view=view)
            raise LocalizedError("Use /botscript help")
        except (ValueError, RuntimeError, OSError) as error:
            return CommandResult(True, (reply(localize_error(error, lang), color=ERROR),), accepted=False)

    @staticmethod
    def _player(source):
        if source.player_id is None:
            raise LocalizedError("Script menu is available only to players")


def math_is_integer(value):
    import math

    return math.isfinite(value) and int(value) == value
