"""endbotd's service: the control plane the plugin used to run inside BDS (docs/ENDBOTD.md P4, P5).

It owns authorization, Groups, scripting and the ordered command worker, and
reaches the world only through the plugin link. Outbound traffic to the plugin
(chat lines, form views, control permissions) leaves on one delivery thread,
in the order it was produced, so the link's ordered thread never waits on the
plugin.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

from endbot_core.command_runner import AsyncCommandRunner
from endbot_core.commands import reply, strip_colors
from endbot_core.control import RuntimeControlClient
from endbot_core.events import LoggerSink
from endbot_core.groups import GroupCommandService, GroupStore
from endbot_core.i18n import DEFAULT_LANGUAGE_SETTING, LocalizedError, language_setting, tr
from endbot_core.limits import BotLimits
from endbot_core.link import LinkError, LinkServer, Submission, load_token
from endbot_core.principal import Principal
from endbot_core.script_config import ScriptConfig
from endbot_core.script_inputs import OwnedInputs
from endbot_core.script_store import ScriptStore
from endbot_core.scripting import ScriptService
from endbot_core.wire_views import decode_view, encode_view
from endbotd import __version__
from endbotd.authority import Authority
from endbotd.config import EndbotdConfig
from endbotd.journal import RunJournal

LOGGER = logging.getLogger("endbotd")
# Mining loops renew every 4 ticks, as they did on the BDS scheduler.
POLL_SECONDS = 0.2
# Below the link's request deadline, so a stuck server tick fails one world call, not the worker.
WORLD_SECONDS = 8.0
DELIVERY_SECONDS = 5.0
CLOSE_SECONDS = 10.0
# Why the runs a previous endbotd left have ended (docs/ENDBOTD.md P6).
RESTARTED = "Endbot service restarted"


_CONTROLS = dict.fromkeys([*range(0x20), *range(0x7F, 0xA0)], " ")


class _Sender:
    """A player or the console, reached through the plugin with ``deliver``."""

    def __init__(self, service: EndbotService, to: str) -> None:
        self.to = to
        self._service = service

    def send_message(self, message: str) -> None:
        self._service.deliver_now(self.to, [message])


class _LocalBridge:
    """What the command worker expects of the server thread: ``call`` runs under the
    authorization lock, ``post`` queues on the delivery thread."""

    def __init__(self, lock: threading.RLock, delivery: ThreadPoolExecutor) -> None:
        self._lock = lock
        self._delivery = delivery

    def call(self, callback, timeout=None):
        with self._lock:
            return callback()

    def post(self, callback) -> bool:
        try:
            self._delivery.submit(callback)
        except RuntimeError:
            return False
        return True


class EndbotService:
    def __init__(self, config: EndbotdConfig, *, control=None, log: Callable[[str, str], None] | None = None,
                 world_timeout: float = WORLD_SECONDS) -> None:
        self.config = config
        self._log = log or (lambda level, message: getattr(LOGGER, level, LOGGER.info)(message))
        try:
            self._language_setting = language_setting(config.ui.get("language"))
        except ValueError as error:
            self._language_setting = DEFAULT_LANGUAGE_SETTING
            self._log("warning", f"{error}; using {DEFAULT_LANGUAGE_SETTING}")
        try:
            limits = BotLimits.from_config(config.limits)
        except ValueError as error:
            # A limits typo must not take /bot down with it; use the safe defaults.
            limits = BotLimits()
            self._log("warning", f"Invalid [limits] config: {error}; using the default limits")
        try:
            script_config = ScriptConfig.from_config(config.scripting)
        except (TypeError, ValueError) as error:
            # A scripting typo must not take /bot down with it; scripts stay off.
            script_config = ScriptConfig()
            self._log("warning", f"Invalid [scripting] config: {error}; scripting is disabled")
        self.link = LinkServer(
            config.link_host, config.link_port, load_token(config.link_token_file), version=__version__,
            config={"language": self._language_setting, "hotbar": dict(config.hotbar)},
            on_submit=self._submit, on_presence=self._presence, grant_revision=self._grant_revision,
            log=self._log, world_timeout=world_timeout, cli_handlers={"controllers": self._cli_controllers},
        )
        self.authority = Authority.from_config(self.link.presence, config.authorization, config.base, log=self._log)
        for gamertag in self.authority.controllers.pending():
            self._log("info", f"Controller {gamertag} is pending; it binds on its first Xbox-authenticated join.")
        self._delivery = ThreadPoolExecutor(max_workers=1, thread_name_prefix="endbotd-delivery")
        self._closed = threading.Event()
        if control is None:
            control = RuntimeControlClient(config.runtime_host, config.runtime_port, config.runtime_token_file,
                                           timeout=config.runtime_timeout)
        control = OwnedInputs(control)
        self._inputs = control
        journal_file = config.journal_file if config.journal_file.is_absolute() else config.base / config.journal_file
        self.journal = RunJournal(journal_file, self._log)
        # Read before anything can rewrite it; start() ends these runs.
        self._journaled = self.journal.load()
        # Runs ended by the restart whose owners hear about it if online at the plugin's first snapshot.
        self._notices: list[dict] = []
        world = self.link.world
        stores = config.stores_directory
        try:
            groups = GroupStore(stores / "groups.json")
        except (ValueError, OSError) as error:
            # A broken groups file must not take /bot down with it.
            self._log("error", f"Groups disabled: groups.json could not be loaded ({error}); fix or move it "
                               "and restart Endbot")
            groups = None
        self.commands = GroupCommandService(
            control, world, groups, authorize=self.authority.authorize, limits=limits,
            capacity=lambda bot_ids: tuple(world.capacity(sorted(bot_ids))),
        )
        try:
            scripts = ScriptStore(stores / "scripts.json", script_config)
        except (ValueError, OSError, KeyError) as error:
            self._log("error", f"Scripts unavailable: scripts.json could not be loaded ({error})")
            scripts = None
        self.scripts = ScriptService(script_config, scripts, self.commands, control, config.base,
                                     authorize=self.authority.authorize, files_directory=config.scripts_folder)
        if scripts is not None and script_config.enabled:
            # Start-up only reads the operator's files; `/botscript reload` also checks their declarations.
            try:
                self.scripts.files_directory.mkdir(exist_ok=True)
                loaded, problems = self.scripts.reload_files()
                self._log("info", f"Loaded {len(loaded)} script files from {self.scripts.files_directory}")
                for name, error in problems:
                    self._log("warning", f"Script file {name} skipped: {error}")
            except (LocalizedError, OSError) as error:
                self._log("error", f"Script files unavailable: {error}")
        self.runner = AsyncCommandRunner(
            self.commands, _LocalBridge(self.authority.lock, self._delivery), self._resolve_sender,
            authorize=self.authority.authorize, log_sink=LoggerSink(LOGGER), show_view=self._show_view,
            scripts=self.scripts,
        )

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> EndbotService:
        self._recover()
        self._inputs.on_change = self.scripts.on_runs_changed = self._record_runs
        self.link.start()
        threading.Thread(target=self._poll_loop, name="endbotd-mining", daemon=True).start()
        return self

    def close(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        # Cleanup (stopping scripts, Group work and mining) still reaches the runtime.
        self.runner.close(timeout=CLOSE_SECONDS)
        # A clean stop leaves only runs whose input cleanup did not finish for the next start.
        self.journal.write([run for run in self._journal_runs() if run["channels"]], final=True)
        self.link.stop()
        self._delivery.shutdown(wait=False)

    # -- run journal (docs/ENDBOTD.md P6) ------------------------------------

    def _journal_runs(self) -> list[dict]:
        return [{"run": run.identity, "name": run.name, "channels": self._inputs.owned(run.identity),
                 "owner": {"playerId": run.source.player_id, "xuid": run.source.xuid, "name": run.source.name,
                           "language": run.source.language}}
                for run in self.scripts.active()]

    def _record_runs(self) -> None:
        self.journal.write(self._journal_runs())

    def _recover(self) -> None:
        """End the runs an unclean exit left: stop their channels, log each, and queue the owners' notices."""
        runs, self._journaled = self._journaled, []
        for run in runs:
            name = strip_colors(run["name"]).translate(_CONTROLS)
            self._inputs.adopt(run["run"], run["channels"])
            try:
                failures = self._inputs.cleanup(run["run"])
            except Exception as error:  # noqa: BLE001 - a recovery failure must not stop endbotd's start
                failures = [f"{type(error).__name__}: {error}"]
            self._log("info", tr("en", "Script {name} ({run}) ended: {reason}", name=name, run=run["run"],
                                 reason=RESTARTED))
            if failures:
                self._log("warning", f"Script {name} ({run['run']}): input cleanup failed: "
                                     f"{'; '.join(failures)[:500]}")
            if run["owner"]["playerId"] or run["owner"]["xuid"]:
                self._notices.append({**run, "name": name})
        self.journal.write([])

    def _notify_restarted(self, records: list[dict]) -> None:
        notices, self._notices = self._notices, []
        for notice in notices:
            owner = notice["owner"]
            record = next((record for record in records
                           if record["playerId"] == owner["playerId"]
                           or (owner["xuid"] and record.get("xuid") == owner["xuid"])), None)
            if record is not None:
                language = owner["language"]
                self.deliver(record["playerId"], [reply(tr(language, "Script {name} ({run}) ended: {reason}",
                                                           name=notice["name"], run=notice["run"],
                                                           reason=tr(language, RESTARTED)))])

    def _poll_loop(self) -> None:
        while not self._closed.wait(POLL_SECONDS):
            self.runner.poll()

    # -- link callbacks (the link's ordered thread) -------------------------

    def _grant_revision(self, player_id: str) -> int:
        return self.authority.grant_revisions.get(player_id)

    def _presence(self, kind: str, records: list[dict]) -> None:
        if kind == "snapshot" and self._notices:
            self._notify_restarted(records)
        if kind in {"snapshot", "join"}:
            self._send_permissions(self.authority.joined(records, snapshot=kind == "snapshot"))
        elif kind == "quit":
            for record in records:
                self.authority.quit(record["playerId"])

    def _submit(self, submission: Submission) -> dict:
        principal = submission.principal
        if submission.guard is not None:
            try:
                principal = replace(principal, group_guard=decode_view(submission.guard))
            except ValueError as error:
                raise LinkError("invalid", f"invalid form guard: {error}") from None
        to = principal.player_id or "console"
        if principal.kind == "player":
            record = self.link.presence.player(principal.player_id)
            with self.authority.lock:
                allowed = record is not None and self.authority.allows(record)
            if not allowed:
                self.deliver(to, [f"Endbot: {tr(principal.language, 'you do not have Bot control permission')}"])
                return {"accepted": False, "code": "unauthorized"}
        if submission.root == "botcontrollers":
            if not principal.console:
                self.deliver(to, ["Endbot: controller management is available only from the server console"])
                return {"accepted": False, "code": "unauthorized"}
            lines, permissions = self.authority.manage(list(submission.argv or ()))
            self.deliver("console", lines)
            self._send_permissions(permissions)
            return {"accepted": True, "code": "ok"}
        if submission.argv is None:
            raise LinkError("unsupported", "operation submissions are not supported yet")
        if not self.runner.submit(list(submission.argv), principal, command=submission.root):
            self.deliver(to, [f"Endbot: {tr(principal.language, 'command service is unavailable')}"])
            return {"accepted": False, "code": "unavailable"}
        return {"accepted": True, "code": "ok"}

    # -- the endbot CLI (its connection's ordered thread) ---------------------

    def _cli_controllers(self, body: dict) -> dict:
        """``endbot controllers add|remove|list`` (P8): run as the console, answered on the CLI connection."""
        operation, target = body.get("operation"), body.get("target")
        if operation not in {"add", "remove", "list"}:
            raise ValueError("expected add, remove or list")
        if operation == "list":
            if target is not None:
                raise ValueError("list takes no target")
        elif not isinstance(target, str) or not target.strip() or not target.isprintable() or len(target) > 256:
            raise ValueError("a non-empty printable target of at most 256 characters is required")
        # The target is one whole name; it is never reparsed as console text.
        result, permissions = self.authority.operation([operation], target)
        self._send_permissions(permissions)
        if operation != "list":
            self._log("info", f"endbot CLI controllers {operation}: {result['messages'][0]}")
        return {"ok": result["ok"], "messages": list(result["messages"]), "entries": result.get("entries", [])}

    # -- toward the plugin (the delivery thread) ----------------------------

    def _resolve_sender(self, source: Principal):
        if source.player_id is None:
            return _Sender(self, "console")
        return _Sender(self, source.player_id) if self.link.presence.player(source.player_id) else None

    def _show_view(self, sender: _Sender, view) -> None:
        try:
            wire = encode_view(view)
        except TypeError as error:
            self._log("warning", f"A form view could not be sent to the plugin: {error}")
            return
        self.deliver_now(sender.to, [], wire)

    def deliver(self, to: str, lines: list[str]) -> None:
        """Queue chat lines behind every earlier delivery."""
        try:
            self._delivery.submit(self.deliver_now, to, lines)
        except RuntimeError:
            pass

    def deliver_now(self, to: str, lines: list[str], view: dict | None = None) -> None:
        if to == "console":
            lines = [strip_colors(line) for line in lines]
        try:
            self.link.deliver(to, lines, view, timeout=DELIVERY_SECONDS)
        except LinkError as error:
            if error.code != "unavailable":
                self._log("warning", f"Delivery to the plugin failed: {error}")

    def _send_permissions(self, permissions: list[tuple[str, bool]]) -> None:
        def send(player_id: str, allowed: bool) -> None:
            try:
                self.link.permission(player_id, allowed, timeout=DELIVERY_SECONDS)
            except LinkError as error:
                if error.code != "unavailable":
                    self._log("warning", f"Control permission update failed: {error}")

        for player_id, allowed in permissions:
            try:
                self._delivery.submit(send, player_id, allowed)
            except RuntimeError:
                return
