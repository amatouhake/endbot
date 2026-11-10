"""Plugin-owned per-run Luau processes; all Bot work uses the command worker."""

import re
import shlex
import threading
import time
from collections import deque
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import NamedTuple
from uuid import uuid4

from endbot_core import areas, script_forms
from endbot_core.commands import (
    DIM,
    ERROR,
    INPUT_JOBS,
    RESET,
    CommandResult,
    _language,
    error_code,
    parse_command,
    reply,
    strip_colors,
)
from endbot_core.control import bot_profiles
from endbot_core.events import Event, outcome_text, reason
from endbot_core.group_forms import GroupGuard
from endbot_core.i18n import LocalizedError, Text, localize_error, tr
from endbot_core.operations import EVENTS, OPERATIONS, Operation
from endbot_core.ownership import OwnershipRefused, owner_for_gamertag
from endbot_core.ownership import owner_key as ownership_key
from endbot_core.principal import Principal
from endbot_core.script_books import pages_to_source, source_to_pages, written
from endbot_core.script_params import arguments, declarations, event_parameter, required
from endbot_core.script_process import RunnerProcess
from endbot_core.script_store import FILE_SUFFIX, VISIBILITIES, Script, load_files
from endbot_core.watches import MAX_WATCHES_PER_RUN, WatchBuffer, validate_filter

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
# The authoring operations (PERMISSIONS.md S6): a controller is refused them; `edit` only opens the editor whose
# save is refused anyway. Running, `private` and deleting their own scripts stay open to controllers.
AUTHORING = frozenset({"save", "import", "copy", "rename", "restore", "edit"})
LOG_LINES = 2000
LOG_BYTES = 512 * 1024
# The starter's chat shows at most this many log lines per window per run (events.CHAT_LOG_CHARS cuts each).
CHAT_LOG_LINES = 10
CHAT_LOG_SECONDS = 5
SCRIPT_TARGET = re.compile(r"[A-Za-z0-9_]{1,16}|#[A-Za-z_][A-Za-z0-9_]{0,15}(?:\[[1-9][0-9]*\])?")


class Declaration(NamedTuple):
    """What inspection reads from a script: its ``params`` schema and the capabilities it ``requires``."""

    schema: list
    requires: tuple


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
    # The requires {} the inspection that started this run read; None when no inspection did.
    requires: tuple | None = None
    bot_pins: dict = field(default_factory=dict)
    log_lines: int = 0
    log_bytes: int = 0
    log_dropped: bool = False
    # Whether an operation already waited for a free rate slot (the run log notes only the first).
    throttled: bool = False
    # Log lines in the starter's chat: the current window's start and count, and lines held back.
    chat_window: float = 0.0
    chat_lines: int = 0
    chat_skipped: int = 0
    # What the script declared in requires {} (its watches are checked against it), and its open watches.
    declared: tuple = ()
    watches: dict = field(default_factory=dict)
    next_watch: int = 0
    # A run a trigger started (BOT-022): the trigger's name, the occurrence for param.event, and who to tell
    # when it ends.
    trigger: str | None = None
    event: dict | None = None
    on_end: object = None


class ScriptService:
    def __init__(
        self, config, store, service, inputs, data_folder, authorize, *, process_factory=RunnerProcess, bootstrap=None,
        files_directory=None, resolve_owner=None,
    ):
        self.config, self.store, self.service, self.inputs = config, store, service, inputs
        self.executable = config.executable(data_folder)
        # The operator's `<name>.luau` files, loaded at start-up and on `reload`.
        self.files_directory = Path(data_folder) / FILES_DIRECTORY if files_directory is None else Path(files_directory)
        self.authorize, self.process_factory = authorize, process_factory
        # The adapter's 4c GamerTag -> (owner key, display name) lookup for the console's owner command.
        self.resolve_owner = resolve_owner
        self.bootstrap = bootstrap
        self._runs = {}
        self._inspecting = {}  # controller -> declaration checks holding a run slot
        self._lock = threading.RLock()
        self._open = True
        self.ordered = None
        self.notify = None
        # Publishes an Event to its sinks (chat, console log); set by the command runner.
        self.publish = None
        # Called after a run starts or ends (endbotd's run journal).
        self.on_runs_changed = None
        # endbotd's event fan-out (BOT-022); without it watch() is refused.
        self.occurrences = None
        # endbotd's trigger engine (BOT-022 step 5c); None elsewhere, where trigger commands are refused.
        self.triggers = None
        self._watch_owners: dict = {}  # owner key -> open watches across that controller's runs

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

    def _process(self, source, wall_seconds=None, foreground=False):
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
        if foreground:
            limits["foreground"] = True
        return self.process_factory(self.executable, source, bootstrap, OPERATIONS, limits)

    def _declaration(self, process, source, canceled, deadline):
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
                "requires expects a list of capability names", "declare requires before params",
                "declare requires exactly once",
            }:
                raise LocalizedError(
                    "Script parameter declaration at {location}: {error}", location=match[1], error=Text(match[2])
                )
            raise LocalizedError(
                "Script declaration failed: {error}", error=message
            )
        schema = event.get("schema")
        declarations(schema)
        return Declaration(schema, required(event.get("requires")))

    def _inspect(self, script, source):
        seconds = min(5, self.config.wall_seconds or 5)
        process = self._process(script.source, seconds, foreground=True)
        try:
            return self._declaration(process, source, threading.Event(), time.monotonic() + seconds)
        finally:
            process.close()

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
            return self._inspect(script, source)
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
        if source is None or source.player_id is None:
            return "console"
        return ownership_key(source)

    def can_edit(self, script, source):
        """The owner or the console; an unowned (pre-ownership) script is claimed by its next editor.

        Nobody edits a file script in game: its file is the source.
        """
        if script.file:
            return False
        return source.player_id is None or script.owner is None or script.owner == self.owner_key(source)

    @staticmethod
    def _author(source):
        """Whether ``source`` may author scripts (S6). Fails closed: anything but a Principal must be the console."""
        if isinstance(source, Principal):
            return source.can_author
        return getattr(source, "player_id", None) is None and getattr(source, "xuid", None) is None

    def can_run(self, script, source):
        return script.visibility == "shared" or self.can_edit(script, source)

    @staticmethod
    def _script_data(script, *, history=False):
        """A script's plain menu fields; the in-game layer builds the screen from them."""
        data = {
            "name": script.name,
            "source": script.source,
            "revision": script.revision,
            "owner": script.owner,
            "owner_name": script.owner_name,
            "visibility": script.visibility,
            "saved_at": script.saved_at,
        }
        if history:
            data["history"] = [
                {"revision": row.revision, "source": row.source, "saved_at": row.saved_at}
                for row in script.history
            ]
        return data

    @staticmethod
    def _menu_data(menu, lang, **fields):
        """A menu outcome: plain data, never a view; menu_views builds the screen."""
        return {"menu": menu, "lang": lang, **fields}

    def _visible(self, source):
        return tuple(script for script in self.store.list() if self.can_run(script, source))

    def listing(self, source):
        """What the web lists: the same scripts as the in-game menu, under the same rule."""
        self._enabled()
        self._check(source)
        return [{"name": script.name, "revision": script.revision, "ownerName": script.owner_name,
                 "visibility": script.visibility, "file": script.file, "own": script.owner == self.owner_key(source),
                 "canEdit": self.can_edit(script, source) and self._author(source)}
                for script in self._visible(source)]

    def detail(self, name, source):
        """One script for the web editor, or the same refusal as the in-game menu."""
        self._enabled()
        self._check(source)
        script = self._script(name, source)
        editable = self.can_edit(script, source) and self._author(source)
        return {"name": script.name, "source": script.source, "revision": script.revision,
                "ownerName": script.owner_name, "visibility": script.visibility, "file": script.file,
                "savedAt": script.saved_at, "canEdit": editable,
                # Earlier sources are the owner's, as in the menu: a shared script shares only what it is now.
                "history": [{"revision": row.revision, "source": row.source, "savedAt": row.saved_at}
                            for row in script.history] if editable else []}

    def declaration(self, name, source):
        """What a run form asks for, and the targets its parameters can choose from."""
        self._enabled()
        self._check(source)
        script = self._script(name, source)
        found = self.inspect(script, source)
        groups = self.service.visible_groups(source) if self.service.groups else ()
        return {"revision": script.revision, "schema": found.schema, "requires": list(found.requires),
                # With their identities: the run refuses a target that changed while the form was open, as in game.
                "groups": [{"name": group.name, "identity": group.identity, "members": list(group.members)}
                           for group in groups],
                "bots": [{"name": bot["name"], "identity": bot["identityId"]} for bot in bot_profiles(self.inputs)]}

    def compile(self, name, text, source):
        """Check a draft as saving would, without saving it: scripts run only as far as their declaration."""
        self._enabled()
        self._check(source)
        if not self._author(source):
            raise LocalizedError("Your role can run scripts but not create or change them; ask the operator for "
                                 "the scripter role")
        if not isinstance(text, str) or not text.strip() or len(text.encode("utf-8")) > self.config.max_script_bytes:
            raise LocalizedError("Script source is empty or too large")
        found = self.inspect(Script(name, text, "draft"), source)
        return {"schema": found.schema, "requires": list(found.requires)}

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

    def start(self, script, values, source, targets=None, requires=None, *, event=None, trigger=None, on_end=None):
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
                requires=requires,
                trigger=trigger,
                event=event,
                on_end=on_end,
            )
            self._runs[identity] = run
            run.thread = threading.Thread(
                target=self._run, args=(run, script, values), name="endbot-script-" + identity, daemon=True
            )
            run.thread.start()
        if self.on_runs_changed:
            self.on_runs_changed()
        return identity

    def check_arguments(self, schema, values, source):
        """Refuse stored arguments the schema does not accept, as a form run would (a trigger's approval)."""
        groups = self.service._store(source) if self.service.groups is not None else None
        arguments(schema, values, groups=groups, bots=bot_profiles(self.inputs), origin=None)

    def _prepare(self, run, schema, values):
        self._alive(run)
        with self.inputs.scope(run.identity, lambda: self._alive(run)):
            bots = bot_profiles(self.inputs)
        for target in run.parameter_targets.values():
            kind, name, identity, *members = target
            if kind == "group":
                current = self.service._store(run.source).get(name[1:])
                matches = current.identity == identity and current.members == members[0]
            else:
                matches = any(bot["name"] == name and bot["identityId"] == identity for bot in bots)
            if not matches:
                raise LocalizedError("Script parameter target changed; reopen the menu")
        groups = self.service._store(run.source) if self.service.groups is not None else None
        result = arguments(schema, values, groups=groups, bots=bots, origin=run.source.origin, event=run.event)
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
        group, _, _ = self.service._store(run.source).select(target)
        key = group.name.casefold()
        guard = run.guards.setdefault(key, GroupGuard(group.identity, group.name, group.members))
        self.service._form_group(replace(run.source, group_guard=guard), group)
        return guard

    def _reply_chat(self, result, operation: str | None = None) -> bool:
        """Whether this operation is echoed to the invoker's chat (``[scripting] replies``).

        Under ``failures``, a ``destroy`` refused as ``not_a_target`` is not echoed (#186): ``block``/``blocks`` are
        reads, not reservations, so a target can vanish between the read and the swing. The script still gets the
        refusal and its code.
        """
        mode = self.config.replies
        if mode == "failures" and operation == "destroy" and result.code == "not_a_target":
            return False
        return mode == "all" or (mode == "failures" and not result.accepted)

    def _throttle(self, run):
        """Take a slot in the sliding one-second operation window, waiting for one when the window is full (BOT-031).

        The wait runs on the run's own thread, outside ``ordered``, so a throttled run never holds the server task
        queue. It honours the run's deadline and stop like ``wait``. The first throttle of a run is noted in its log.
        """
        while True:
            self._alive(run)
            now = time.monotonic()
            while run.calls and run.calls[0] <= now - 1:
                run.calls.popleft()
            if len(run.calls) < self.config.operations_per_second:
                run.calls.append(now)
                return
            if not run.throttled:
                run.throttled = True
                self._notice(run, "throttled")
            self._wait(run, max(0.0, run.calls[0] + 1 - now))

    def _notice(self, run, notice):
        if self.publish:
            shown = self._chat_log(run)
            self.publish(Event(run.source, "run.log", {
                "run": run.identity, "name": run.name, "text": "", "limited": False, "notice": notice,
                "chat": shown, "skipped": self._skipped_logs(run) if shown else 0}))

    def _operation(self, run, event, *, audit=True):
        self._alive(run)
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
                    _, members, _ = self.service._store(run.source).select(target)
                    profiles = self.service._profiles(source)
                    online = bool(members) and all(
                        profiles.get(identity, {}).get("connectionState") == "online" for _, identity in members
                    )
                else:
                    # A wait reads the Bot's status, so it is checked like `status` (4b).
                    self.service.owner_check(parse_command([target, "status"]), source)
                    online = self.inputs.request("status", name=target).get("connectionState") == "online"
                return {"ok": online}
            try:
                operation = Operation(event.get("operation"), target, event.get("params"))
                result = self.service.execute_operation(operation, source)
            except (RuntimeError, ValueError) as error:
                return {"result": {"ok": False, "code": error_code(error), "reason": localize_error(error, "en")}}
            if audit and self.notify:
                # Preserve existing command audit, even if the controller logged out.
                self.notify(source, result, chat=self._reply_chat(result, operation.op))
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

    def _watch_limit(self):
        limits = getattr(self.service, "limits", None)
        return getattr(limits, "max_watches_per_controller", 32)

    def _watch(self, run, event):
        """Open a watch (EVENTS.md E5): validated, within the limits, buffering from now."""
        if self.occurrences is None:
            raise LocalizedError("Watches need endbotd; they are not available here")
        kind, canonical = validate_filter(event.get("kind"), event.get("filter"), run.declared)
        owner = self.owner_key(run.source)
        limit = self._watch_limit()
        with self._lock:
            if len(run.watches) >= MAX_WATCHES_PER_RUN:
                raise LocalizedError("A run may hold at most {limit} watches", limit=MAX_WATCHES_PER_RUN)
            if self._watch_owners.get(owner, 0) >= limit:
                raise LocalizedError("A controller may hold at most {limit} watches ([limits] "
                                     "max-watches-per-controller)", limit=limit)
            self._watch_owners[owner] = self._watch_owners.get(owner, 0) + 1
        buffer = WatchBuffer(kind)
        try:
            subscription = self.occurrences.subscribe(kind, canonical, buffer.on_item, buffer.on_gap, owner=owner)
        except BaseException:
            with self._lock:
                remaining = self._watch_owners.get(owner, 1) - 1
                if remaining > 0:
                    self._watch_owners[owner] = remaining
                else:
                    self._watch_owners.pop(owner, None)
            raise
        run.next_watch += 1
        run.watches[run.next_watch] = (subscription, buffer, owner)
        return run.next_watch

    def _watch_wait(self, run, event):
        entry = run.watches.get(self._watch_id(event.get("watch")))
        if entry is None:
            raise LocalizedError("Unknown or closed watch")
        ticks = event.get("timeout", 1200)
        if type(ticks) not in (int, float) or not math_is_integer(ticks) or not 0 <= ticks <= 2147483647:
            raise LocalizedError("Invalid script wait")
        result = entry[1].wait(ticks / 20, lambda: self._alive(run))
        self._alive(run)
        return result

    @staticmethod
    def _watch_id(value):
        """A watch id as Luau sends it back; JSON through the runner may turn 7 into 7.0."""
        if type(value) in (int, float) and math_is_integer(value):
            return int(value)
        return None

    def _watch_close(self, run, watch_id):
        entry = run.watches.pop(self._watch_id(watch_id), None)
        if entry is None:
            return
        subscription, _, owner = entry
        try:
            self.occurrences.unsubscribe(subscription)
        finally:
            with self._lock:
                remaining = self._watch_owners.get(owner, 1) - 1
                if remaining > 0:
                    self._watch_owners[owner] = remaining
                else:
                    self._watch_owners.pop(owner, None)

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
            declaration = self._declaration(process, run.source, run.canceled, run.deadline)
            run.declared = tuple(declaration.requires)
            # S3: a run started from an inspection (a menu, later a trigger) must declare what that inspection
            # read, or it stops here, before its first operation; a conditional requires {} cannot widen it.
            if run.requires is not None and declaration.requires != run.requires:
                raise LocalizedError("Script requires changed between inspection and run")
            schema = declaration.schema
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
                elif kind == "watch":
                    payload = {"watch": self._watch(run, event)}
                elif kind == "watchWait":
                    payload = {"result": self._watch_wait(run, event)}
                elif kind == "watchClose":
                    self._watch_close(run, event.get("watch"))
                elif kind in {"operation", "members", "waitOnline"}:
                    # waitOnline(b) waits without end; b:waitOnline{timeout = ...} gives up after its ticks.
                    timeout = event.get("timeout") if kind == "waitOnline" else None
                    if timeout is not None and (type(timeout) not in (int, float) or not math_is_integer(timeout)
                                                or not 0 <= timeout <= 2147483647):
                        raise LocalizedError("Invalid script wait")
                    end = None if timeout is None else time.monotonic() + timeout / 20
                    self._throttle(run)
                    payload = self.ordered(lambda event=event: self._operation(run, event))
                    while kind == "waitOnline" and not payload["ok"]:
                        if end is not None and time.monotonic() >= end:
                            payload = {"ok": False, "reason": "timeout"}
                            break
                        pause = max(0.25, 1 / self.config.operations_per_second)
                        self._wait(run, pause if end is None else min(pause, max(0, end - time.monotonic())))
                        self._throttle(run)
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
            for watch_id in list(run.watches):
                self._watch_close(run, watch_id)
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
            if self.on_runs_changed:
                self.on_runs_changed()
            if self.publish:
                self.publish(ended)
            if run.on_end is not None:
                try:
                    run.on_end(run, outcome)
                except Exception:  # noqa: BLE001, S110 - a trigger's bookkeeping must not break run cleanup
                    pass

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

    # -- triggers (BOT-022 step 5c: the commands and forms only parse, call and reply) -------------

    def _trigger_command(self, tail, source, lang, guard):
        if self.triggers is None:
            raise LocalizedError("Triggers need endbotd; they are not available here")
        if not tail:
            raise LocalizedError("Use /botscript trigger add|list|show|enable|disable|remove|set|visibility ...")
        verb, rest = tail[0].lower(), tail[1:]
        if verb == "add":
            return self._trigger_add(rest, source, lang, guard)
        if verb == "menu":
            self._player(source)
            if not rest:
                triggers = [self._trigger_data(trigger, source) for trigger in self.triggers.list(source)]
                return CommandResult(True, data=self._menu_data("scriptTriggers", lang, triggers=triggers))
            if len(rest) == 1:
                trigger = self._trigger_get(rest[0], source)
                return CommandResult(True, data=self._menu_data("scriptTrigger", lang,
                                                                trigger=self._trigger_data(trigger, source)))
            raise LocalizedError("Use /botscript trigger show <name>")
        if verb == "new":
            self._player(source)
            return self._trigger_new(rest, source, lang, guard)
        if verb == "list" and not rest:
            lines = tuple(reply(script_forms.trigger_line(trigger, lang))
                          for trigger in self.triggers.list(source))
            return CommandResult(True, lines or (reply(tr(lang, "No triggers")),))
        if verb == "show" and len(rest) == 1:
            trigger = self._trigger_get(rest[0], source)
            return CommandResult(True, tuple(reply(line) for line in script_forms.trigger_lines(trigger, lang)))
        if verb == "enable" and len(rest) == 1:
            owner, name = self._trigger_ref(rest[0], source)
            trigger = self.triggers.enable(source, name, owner)
            declaration = trigger.approved.get("declaration") or []
            text = ", ".join(declaration) if declaration else tr(lang, "basic only")
            return CommandResult(
                True, (reply(tr(lang, "Trigger {name} armed; it runs {script} with: {declaration}",
                                  name=trigger.name, script=trigger.script, declaration=text)),),
                log_level="info",
                log_messages=(f"Trigger {trigger.name} of {self._who(source)} armed",))
        if verb == "disable" and len(rest) == 1:
            owner, name = self._trigger_ref(rest[0], source)
            trigger = self.triggers.disable(source, name, owner)
            return CommandResult(
                True, (reply(tr(lang, "Trigger {name} disabled", name=trigger.name)),), log_level="info",
                log_messages=(f"Trigger {trigger.name} of {self._who(source)} disabled",))
        if verb == "remove" and len(rest) == 1:
            owner, name = self._trigger_ref(rest[0], source)
            self.triggers.remove(source, name, owner)
            return CommandResult(
                True, (reply(tr(lang, "Trigger {name} removed", name=name)),), log_level="info",
                log_messages=(f"Trigger {name} of {self._who(source)} removed",))
        if verb == "set" and len(rest) >= 2:
            owner, name = self._trigger_ref(rest[0], source)
            trigger = self.triggers.set_policy(source, name, self._trigger_policy(rest[1:]), owner)
            policy = script_forms.policy_text(trigger.policy)
            return CommandResult(
                True, (reply(tr(lang, "Trigger {name}: {policy}", name=trigger.name,
                                  policy=script_forms.policy_text(trigger.policy, lang))),),
                log_level="info",
                log_messages=(f"Trigger {trigger.name} of {self._who(source)}: {policy}",))
        if verb == "visibility" and len(rest) == 2 and rest[1].lower() in {"shared", "private"}:
            visibility = rest[1].lower()
            owner, name = self._trigger_ref(rest[0], source)
            trigger = self.triggers.set_visibility(source, name, visibility, owner)
            return CommandResult(
                True, (reply(tr(lang, "Trigger {name} is now {visibility}", name=trigger.name,
                                  visibility=Text(visibility))),),
                log_level="info",
                log_messages=(f"Trigger {trigger.name} of {self._who(source)} is now {visibility}",))
        raise LocalizedError("Use /botscript trigger add|list|show|enable|disable|remove|set|visibility ...")

    def _trigger_ref(self, token, source):
        """``(owner key or None, name)`` for ``<name>`` or ``<owner>/<name>``.

        The owner is the display name (case-insensitive) or the owner key; a player's bare name is their own.
        """
        if not isinstance(token, str) or not token:
            raise LocalizedError("Trigger {name} does not exist", name="")
        owner, separator, name = token.partition("/")
        if not separator:
            return None, token
        for trigger in self.triggers.list(source):
            if trigger.owner == owner or (trigger.owner_name or "").casefold() == owner.casefold():
                return trigger.owner, name
        raise LocalizedError("Trigger {name} does not exist", name=name or token)

    def _trigger_get(self, token, source):
        owner, name = self._trigger_ref(token, source)
        return self.triggers.get(owner, name, source)

    @staticmethod
    def _trigger_filter_value(kind, field, value):
        spec = EVENTS.get(kind, {}).get("filters", {}).get(field)
        if spec is None:
            return value
        if spec["type"] == "area":
            try:
                return areas.clause_from_value(value)
            except LocalizedError as error:
                raise LocalizedError("Trigger filter {field}: {reason}", field=field,
                                     reason=Text(str(error))) from None
        if spec["type"] == "integer":
            try:
                return int(value)
            except (TypeError, ValueError):
                raise LocalizedError("Trigger filter {field} must be a whole number", field=field) from None
        return value

    @staticmethod
    def _trigger_policy(words):
        values = {}
        for word in words:
            field, separator, value = word.partition("=")
            if not separator or not field:
                raise LocalizedError("Use /botscript trigger set <name> <policy>=<value> ...")
            if field == "concurrency":
                if value not in {"skip", "queue", "parallel"}:
                    raise LocalizedError("Trigger policy concurrency must be skip, queue or parallel")
                values[field] = value
            elif field in {"cooldown", "startsPerMinute", "queue"}:
                try:
                    values[field] = int(value)
                except (TypeError, ValueError):
                    raise LocalizedError("Trigger policy {field} must be an integer", field=field) from None
            else:
                raise LocalizedError("Unknown trigger policy {field}; policies: concurrency, cooldown, "
                                     "startsPerMinute, queue", field=field)
        return values

    def _trigger_add(self, rest, source, lang, guard):
        if len(rest) < 2:
            raise LocalizedError("Use /botscript trigger add <script> <kind> [field=value ...] [-- <args>]")
        script_name, kind = rest[0], rest[1]
        words = rest[2:]
        if "--" in words:
            separator = words.index("--")
            filter_words, arguments = words[:separator], words[separator + 1:]
        else:
            filter_words, arguments = words, []
        name, visibility, filters = None, "private", {}
        for word in filter_words:
            field, separator, value = word.partition("=")
            if not separator or not field:
                raise LocalizedError("Use /botscript trigger add <script> <kind> [field=value ...] [-- <args>]")
            if field == "name":
                name = value
            elif field == "visibility":
                visibility = value.lower()
            else:
                filters[field] = self._trigger_filter_value(kind, field, value)
        if visibility not in {"shared", "private"}:
            raise LocalizedError("Trigger visibility must be shared or private")
        script = self._script(script_name, source)
        if isinstance(guard, script_forms.ScriptGuard) and guard.revision not in {None, script.revision}:
            raise LocalizedError("Script changed; reopen the menu")
        trigger = self.triggers.create(source, script=script_name, kind=kind, filter_=filters,
                                       arguments=arguments, name=name, visibility=visibility)
        return CommandResult(
            True, (reply(tr(lang, "Trigger {name} armed for {kind}", name=trigger.name, kind=trigger.kind)),),
            log_level="info",
            log_messages=(f"Trigger {trigger.name} of {self._who(source)} armed for {trigger.kind}",))

    def _trigger_new(self, rest, source, lang, guard):
        """The form steps: choose the script, then the kind, then the filters and parameters."""
        if not rest:
            scripts = [{"name": script.name, "revision": script.revision, "owner_name": script.owner_name,
                        "visibility": script.visibility, "file": script.file,
                        "own": script.owner == self.owner_key(source)} for script in self._visible(source)]
            return CommandResult(True, data=self._menu_data("scriptTriggerScripts", lang, scripts=scripts))
        if len(rest) > 2:
            raise LocalizedError("Use /botscript trigger add <script> <kind> [field=value ...] [-- <args>]")
        script = self._script(rest[0], source)
        if isinstance(guard, script_forms.ScriptGuard) and guard.revision not in {None, script.revision}:
            raise LocalizedError("Script changed; reopen the menu")
        declaration = self.inspect(script, source)
        schema = declaration.schema
        if event_parameter(schema) is None:
            raise LocalizedError("Script {name} has no param.event, so a trigger cannot start it", name=script.name)
        bots = bot_profiles(self.inputs)
        groups = self.service.visible_groups(source) if self.service.groups else ()
        script_forms.parameter_choices(schema, groups, bots)
        if len(rest) == 1:
            return CommandResult(True, data=self._menu_data(
                "scriptTriggerKinds", lang, script=self._script_data(script), schema=schema,
                requires=list(declaration.requires), origin=source.origin))
        kind = rest[1]
        if kind not in EVENTS:
            raise LocalizedError("Unknown event kind {kind}; known kinds: {known}", kind=kind,
                                 known=", ".join(sorted(EVENTS)) or "none")
        return CommandResult(True, data=self._menu_data(
            "scriptTriggerFilter", lang, script=self._script_data(script), kind=kind,
            filters=EVENTS[kind]["filters"], schema=schema, requires=list(declaration.requires),
            groups=[{"identity": group.identity, "name": group.name, "members": list(group.members)}
                    for group in groups],
            bots=bots, origin=source.origin))

    def _trigger_data(self, trigger, source):
        """A trigger's plain fields for the menu; the owner may manage it, and so may the console."""
        own = trigger.owner == self.owner_key(source)
        return {
            "name": trigger.name, "owner": trigger.owner, "owner_name": trigger.owner_name,
            "visibility": trigger.visibility, "kind": trigger.kind, "filter": dict(trigger.filter),
            "script": trigger.script, "arguments": list(trigger.arguments),
            "declaration": list(trigger.approved.get("declaration", ())),
            "status": trigger.status, "reason": trigger.reason, "policy": dict(trigger.policy),
            "stats": dict(trigger.stats), "own": own, "can_manage": own or source.player_id is None,
            "ref": trigger.name if own else f"{trigger.owner}/{trigger.name}",
        }

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
            if operation in {"share", "private"} and len(tail) == 1:
                # The 0.4.0 spelling, removed in 0.6.0 as the 0.5.0 notes said.
                raise LocalizedError("share and private were replaced by visibility <name> shared|private")
            # Sharing widens who can use a script, so it is authoring; making one private only narrows it.
            sharing = operation == "visibility" and len(tail) == 2 and tail[1].lower() == "shared"
            if (operation in AUTHORING or sharing) and not self._author(source):
                # S6: the same refusal from a typed command, a form and every later surface.
                return CommandResult(True, (reply(tr(lang, "Your role can run scripts but not create or change "
                                                           "them; ask the operator for the scripter role"),
                                                  color=ERROR),), accepted=False, code="unauthorized")
            if operation == "help" and not tail:
                return CommandResult(
                    True,
                    (
                        reply(
                            tr(
                                lang,
                                "Script help: list | show <name> [page] | run <name> [args...] "
                                "| runs | stop <id|name|all> | delete <name> | copy <name> <new> "
                                "| visibility <name> shared|private | owner <name> <GamerTag> | history <name> "
                                "| restore <name> <n> | rename <name> <new> | import <name> | export <name> "
                                "[overwrite] | reload | trigger add|list|show|enable|disable|remove|set|visibility "
                                "| menu",
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
            if operation == "trigger":
                return self._trigger_command(tail, source, lang, guard)
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
                            folder=f"{self.files_directory.name}/")]
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
                data = None
                if source.player_id is not None:
                    data = (self._menu_data("scriptLines", lang, script=self._script_data(saved),
                                            page=min(guard.page, script_forms.edit_pages(saved)),
                                            whole_editor=self.config.ui_pack_editor)
                            if guard.page is not None
                            else self._menu_data("script", lang, script=self._script_data(saved), editable=True,
                                                 book_export=self.config.book_export, author=self._author(source),
                                                 whole_editor=self.config.ui_pack_editor))
                return CommandResult(True, (reply(tr(lang, "Script {name} saved", name=tail[0])),), log_level="info",
                                     log_messages=(f"Script {tail[0]} saved by {self._who(source)}",), data=data)
            if operation == "menu" and not tail:
                self._player(source)
                scripts = [
                    {"name": script.name, "revision": script.revision, "owner_name": script.owner_name,
                     "visibility": script.visibility, "file": script.file,
                     "own": script.owner == self.owner_key(source)}
                    for script in self._visible(source)
                ]
                return CommandResult(True, data=self._menu_data("scripts", lang, scripts=scripts,
                                                                author=self._author(source),
                                                                whole_editor=self.config.ui_pack_editor,
                                                                triggers=self.triggers is not None))
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
                        return CommandResult(True, data=self._menu_data(
                            "scriptImport", lang, script=self._script_data(existing), text=text))
                    if guard.book != text:
                        raise LocalizedError("The book changed; check it again")
                revision = existing.revision if existing else None
                name = existing.name if existing else tail[0]
                self.inspect(Script(name, text, "draft"), source)
                self._check(source)
                self.store.save(name, text, revision, self.owner_key(source), self._who(source))
                lines = len(text.splitlines())
                data = (self._menu_data("script", lang, script=self._script_data(self.store.get(name)),
                                        editable=True, book_export=self.config.book_export,
                                        author=self._author(source), whole_editor=self.config.ui_pack_editor)
                        if isinstance(guard, script_forms.ScriptGuard) else None)
                return CommandResult(
                    True, (reply(tr(lang, "Script {name} imported from the book ({lines} lines)", name=name,
                                    lines=lines)),),
                    log_level="info", log_messages=(f"Script {name} imported from a book by {self._who(source)}",),
                    data=data)
            if operation == "copy" and len(tail) == 2:
                # Copies start private and belong to the copier; the original is untouched.
                original = self._script(tail[0], source)
                # A form copies what it showed: a newer source is not what the person chose.
                if isinstance(guard, script_forms.ScriptGuard) and guard.revision not in {None, original.revision}:
                    raise LocalizedError("Script changed; reopen the menu")
                self._new_name(tail[1])
                self._check(source)
                self.store.save(tail[1], original.source, None, self.owner_key(source), self._who(source))
                return CommandResult(
                    True, (reply(tr(lang, "Script {name} copied to {copy}", name=original.name, copy=tail[1])),),
                    log_level="info",
                    log_messages=(f"Script {original.name} copied to {tail[1]} by {self._who(source)}",))
            if operation == "owner":
                if len(tail) != 2:
                    raise LocalizedError("Use /botscript owner <name> <GamerTag>")
                if source.player_id is not None:
                    raise OwnershipRefused("Only the console can change owners")
                script = self.store.get(tail[0])
                if script.file:
                    raise LocalizedError("File scripts have no owner; the file is the source")
                if isinstance(guard, script_forms.ScriptGuard) and guard.revision != script.revision:
                    raise LocalizedError("Script changed; reopen the menu")
                owner, owner_name = owner_for_gamertag(self.resolve_owner, tail[1])
                self.store.set_owner(script.name, owner, owner_name, script.revision)
                return CommandResult(
                    True,
                    (reply(tr(lang, "Script {name} now belongs to {owner}", name=script.name, owner=owner_name)),),
                    log_level="info",
                    log_messages=(f"Script {script.name} now belongs to {owner_name} by {self._who(source)}",),
                )
            script = self._script(tail[0], source,
                                  edit=operation in {"delete", "edit", "visibility", "restore", "rename"})
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
                    return CommandResult(True, data=self._menu_data(
                        "scriptLines", lang, script=self._script_data(script), page=page,
                        whole_editor=self.config.ui_pack_editor))
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
                            return CommandResult(True, data=self._menu_data(
                                "scriptExport", lang, script=self._script_data(script), pages=list(current),
                                text=pages_to_source(current)))
                        raise LocalizedError("The book and quill is not empty; hold an empty one or add overwrite")
                    menu_pages = guard.book if isinstance(guard, script_forms.ScriptGuard) else None
                    expected = list(menu_pages) if menu_pages is not None else current
                self._world().fill_held_book(source.player_id, pages, expected)
                return CommandResult(
                    True, (reply(tr(lang, "Script {name} written into the book ({pages} pages)", name=script.name,
                                    pages=len(pages))),),
                    log_level="info",
                    log_messages=(f"Script {script.name} exported to a book by {self._who(source)}",))
            if operation == "visibility" and len(tail) == 2 and tail[1].lower() in VISIBILITIES:
                visibility = tail[1].lower()
                self.store.set_visibility(script.name, visibility, script.revision)
                return CommandResult(
                    True, (reply(tr(lang, "Script {name} is now {visibility}", name=script.name,
                                    visibility=Text(visibility))),),
                    log_level="info",
                    log_messages=(f"Script {script.name} is now {visibility} by {self._who(source)}",))
            if operation == "history" and len(tail) == 1:
                if source.player_id is not None and isinstance(guard, script_forms.ScriptGuard):
                    return CommandResult(True, data=self._menu_data(
                        "scriptHistory", lang, script=self._script_data(script, history=True),
                        editable=self.can_edit(script, source) and self._author(source)))
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
                # A run form carries the declaration its inspection read; the run is held to it (S3).
                requires = guard.requires if isinstance(guard, script_forms.ScriptGuard) else None
                run = self.start(script, values, source, targets, requires)
                return CommandResult(
                    True, (reply(tr(lang, "Script {name} started: {run}", name=script.name, run=run)),),
                    log_level="info",
                    log_messages=(f"Script {script.name} started: {run} by {self._who(source)}",),
                )
            if operation in {"menu", "parameters"} and len(tail) == 1:
                self._player(source)
                if operation == "parameters":
                    declaration = self.inspect(script, source)
                    schema = declaration.schema
                    if not schema:
                        # No parameters: an empty form cannot be submitted, so start the run.
                        run = self.start(script, [], source, None, declaration.requires)
                        return CommandResult(
                            True,
                            # The menu echoes no internal `parameters` step; show the command this was.
                            (f"{DIM}/botscript run {script.name}{RESET}",
                             reply(tr(lang, "Script {name} started: {run}", name=script.name, run=run))),
                            log_level="info",
                            log_messages=(f"Script {script.name} started: {run} by {self._who(source)}",),
                        )
                    bots = bot_profiles(self.inputs)
                    groups = self.service.visible_groups(source) if self.service.groups else ()
                    # The no-target refusal must stay in this try, where its reply is delivered.
                    script_forms.parameter_choices(schema, groups, bots)
                    data = self._menu_data(
                        "scriptParameters", lang, script=self._script_data(script), schema=schema,
                        groups=[{"identity": group.identity, "name": group.name, "members": list(group.members)}
                                for group in groups],
                        bots=bots, origin=source.origin, requires=list(declaration.requires))
                else:
                    data = self._menu_data(
                        "script", lang, script=self._script_data(script),
                        editable=self.can_edit(script, source), book_export=self.config.book_export,
                        author=self._author(source), whole_editor=self.config.ui_pack_editor)
                return CommandResult(True, data=data)
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
