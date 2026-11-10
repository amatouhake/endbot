"""Triggers: stored bindings that start runs for occurrences (BOT-022 step 5b, EVENTS.md E6 to E9).

A trigger binds an event kind and filter to a script and its arguments. Arming
(creating or enabling) approves: it records the script revision, its
``requires {}`` declaration, a hash of its parameters, and the owner's grant
revision. A started run has the owner's authority (a ``run`` principal bound to
that grant revision, so a revoke and regrant never revive it) intersected with
the approved declaration.

Before each start the engine checks, in order: starts are possible (the plugin is
linked, S3), the owner still holds the grant (else the trigger is disabled), the
script still exists and the owner may run it (else paused), its current
declaration is within the approved one (else paused ``declaration grew``) and its
parameters are unchanged (else paused ``arguments no longer valid``). The
policy engine (``trigger_policy``) decides skip, queue, cooldown and the start
cap; going over the cap throttles the trigger (occurrences are dropped or held) and tells the owner once.

Occurrences arrive on the link thread; one worker thread makes every decision
and starts every run, so the link never waits for a script.
"""

from __future__ import annotations

import hashlib
import json
import queue
import threading
import time
from collections.abc import Callable

from endbot_core.events import Event, reason as reason_data
from endbot_core.i18n import LocalizedError
from endbot_core.ownership import owner_key
from endbot_core.principal import Principal
from endbot_core.script_params import declarations, event_parameter, inputs
from endbot_core.script_store import FILE_OWNER
from endbot_core.trigger_policy import TriggerPolicy
from endbot_core.trigger_store import Trigger, policy_from
from endbot_core.watches import check_kind, validate_filter

DEFAULT_MAX_TRIGGERS = 8
# Change notices waiting for a slow listener; beyond this they are dropped (the page re-reads on its next list).
MAX_NOTICES = 256
TICK_SECONDS = 0.25
# Occurrences waiting for the worker; more are dropped and counted as missed so decisions never fall far behind.
MAX_PENDING_OCCURRENCES = 1024
# Stats (starts, missed, skipped, last start) reach triggers.json at most this often; state changes at once.
STATS_FLUSH_SECONDS = 1.0


def params_hash(schema) -> str:
    """What the stored arguments were checked against: the parameters a person supplies, canonically."""
    canonical = json.dumps(list(inputs(schema).values()), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _key(trigger) -> tuple[str, str]:
    return trigger.owner, trigger.name


def _owner(principal: Principal) -> str:
    return owner_key(principal) or "console"


class TriggerEngine:
    """Arms stored triggers, starts their runs and keeps their state and stats."""

    def __init__(self, store, hub, scripts, *, authorize: Callable[[Principal], bool],
                 role: Callable[[str], str | None] = lambda xuid: None,
                 grant_revision: Callable[[str], int | None] = lambda player_id: None,
                 linked: Callable[[], bool] = lambda: True,
                 publish: Callable[[Event], None] | None = None,
                 max_triggers: int = DEFAULT_MAX_TRIGGERS,
                 log: Callable[[str, str], None] | None = None,
                 monotonic: Callable[[], float] = time.monotonic, wall: Callable[[], float] = time.time) -> None:
        self.store, self.hub, self.scripts = store, hub, scripts
        self._authorize, self._role, self._linked = authorize, role, linked
        self._grant_revision = grant_revision
        self._publish = publish
        self.max_triggers = max_triggers
        self._log = log or (lambda level, message: None)
        self._monotonic, self._wall = monotonic, wall
        self._lock = threading.RLock()
        self._armed: dict[tuple, dict] = {}  # key -> {"policy", "subscription", "generation", "declaration"}
        # The last policy of each trigger, kept across disarming, so re-arming never resets its start cap.
        self._policies: dict[tuple, TriggerPolicy] = {}
        self._generation = 0
        # Held from the last check before a start to the start, and by every state change: no start after a
        # disable, remove or pause has returned.
        self._state = threading.RLock()
        self._notices: queue.Queue = queue.Queue()
        self._notifier: threading.Thread | None = None
        self._work: queue.Queue = queue.Queue()
        self._pending_occurrences = 0
        self._overflow: dict[tuple, int] = {}  # occurrences dropped at the full work queue, per trigger
        self._flushed = time.monotonic()
        self._closed = threading.Event()
        self._thread: threading.Thread | None = None
        # Called outside every lock after a state or stats change is stored (the web's trigger subscription).
        self.on_changed: Callable[..., None] | None = None  # (trigger, *, removed: bool)

    # -- lifecycle -------------------------------------------------------

    def start(self) -> TriggerEngine:
        """Arm every armed trigger from the store; their cooldown holds across the restart (E9)."""
        now = self._wall()
        for trigger in self.store.list():
            if trigger.status != "armed":
                continue
            if self.max_triggers == 0:
                continue  # triggers are turned off on this server: nothing is armed, nothing is changed
            if not self._authorize(self._principal(trigger)):
                # Revoked while endbotd was stopped (a GamerTag removed from endbot.toml): disabled, not armed.
                self.disable_for(_key(trigger), "owner revoked")
                continue
            self.store.update(trigger.owner, trigger.name, stats={**trigger.stats, "lastGap": now})
            self._changed(self.store.get(trigger.owner, trigger.name))
            try:
                self._arm(self.store.get(trigger.owner, trigger.name))
            except LocalizedError as error:
                # The kind left the registry (a rollback) or the server-wide watched-block limit shrank.
                self.pause_for(_key(trigger), "kind unavailable" if trigger.kind not in self.hub.kinds
                               else str(error)[:128])
        self._thread = threading.Thread(target=self._loop, name="endbotd-triggers", daemon=True)
        self._thread.start()
        return self

    def close(self) -> None:
        self._closed.set()
        self._work.put(None)
        self._notices.put(None)
        with self._lock:
            armed, self._armed = self._armed, {}
        for entry in armed.values():
            self.hub.unsubscribe(entry["subscription"])
        try:
            self.store.flush()  # stats kept in memory reach the file on a clean stop
        except (OSError, ValueError) as error:
            self._log("warning", f"trigger stats were not saved: {type(error).__name__}: {error}")

    def _changed(self, trigger: Trigger | None, *, removed: bool = False) -> None:
        """Queue a notice of a stored change for the listener (the web); never waits for it."""
        if self.on_changed is None or trigger is None:
            return
        if self._notices.qsize() >= MAX_NOTICES:
            return  # a listener that cannot keep up loses notices, never the engine's time
        self._notices.put((trigger, removed))
        with self._lock:
            if self._notifier is None or not self._notifier.is_alive():
                self._notifier = threading.Thread(target=self._notify_loop, name="endbotd-trigger-notices",
                                                  daemon=True)
                self._notifier.start()

    def _notify_loop(self) -> None:
        while True:
            item = self._notices.get()
            if item is None:
                return
            trigger, removed = item
            callback = self.on_changed
            try:
                if callback is not None:
                    callback(trigger, removed=removed)
            except Exception as error:  # noqa: BLE001 - a listener's failure is logged, not raised into the engine
                self._log("warning", f"trigger change listener failed: {type(error).__name__}: {error}")
            finally:
                self._notices.task_done()

    def flush_notices(self) -> None:
        """Wait until every queued change notice was handed to the listener (tests and shutdown)."""
        self._notices.join()

    def _arm(self, trigger: Trigger) -> None:
        key = _key(trigger)
        last = trigger.stats.get("lastStart")
        elapsed = None if last is None else max(0.0, self._wall() - last)
        policy = TriggerPolicy(trigger.policy, None if elapsed is None else self._monotonic() - elapsed)
        with self._lock:
            previous_policy = self._policies.get(key)
            if previous_policy is not None:
                policy.carry_from(previous_policy)
            self._policies[key] = policy
            self._generation += 1
            generation = self._generation
        # Occurrences carry the generation they were subscribed under; one from a replaced subscription is ignored.
        subscription = self.hub.subscribe(
            trigger.kind, trigger.filter,
            lambda item, key=key, generation=generation: self._offer(key, item, generation),
            lambda reason, key=key: self._work.put(("gap", key, reason)), owner=trigger.owner)
        with self._lock:
            previous = self._armed.pop(key, None)
            queued = previous.get("queue", []) if previous is not None else []
            self._armed[key] = {"policy": policy, "subscription": subscription, "generation": generation,
                                "queue": queued[-policy.pending:] if policy.pending else []}
        if previous is not None:
            self.hub.unsubscribe(previous["subscription"])

    def _offer(self, key: tuple, item: dict, generation: int) -> None:
        """Hand an occurrence to the worker, or count it as missed when the worker is too far behind."""
        with self._lock:
            if self._pending_occurrences >= MAX_PENDING_OCCURRENCES:
                self._overflow[key] = self._overflow.get(key, 0) + 1
                return
            self._pending_occurrences += 1
        self._work.put(("occurrence", key, item, generation))

    def _disarm(self, key: tuple) -> None:
        with self._lock:
            entry = self._armed.pop(key, None)
        if entry is not None:
            self.hub.unsubscribe(entry["subscription"])

    def _loop(self) -> None:
        while not self._closed.is_set():
            try:
                item = self._work.get(timeout=TICK_SECONDS)
            except queue.Empty:
                item = ("tick",)
            if item is None:
                return
            try:
                if item[0] == "occurrence":
                    with self._lock:
                        self._pending_occurrences -= 1
                    self._occurrence(item[1], item[2], item[3])
                elif item[0] == "gap":
                    self._stat(item[1], lastGap=self._wall())
                elif item[0] == "ended":
                    self._ended(item[1])
                self._due()
                self._housekeeping()
            except Exception as error:  # noqa: BLE001 - one trigger's failure must not stop the others
                self._log("warning", f"trigger engine: {type(error).__name__}: {error}")

    def _housekeeping(self) -> None:
        """Count occurrences dropped at the full work queue and write stats at most once per second."""
        with self._lock:
            overflow, self._overflow = self._overflow, {}
        for key, count in overflow.items():
            self._bump(key, "missed", count)
        if time.monotonic() - self._flushed >= STATS_FLUSH_SECONDS:
            self._flushed = time.monotonic()
            self.store.flush()

    # -- decisions -------------------------------------------------------

    def _stat(self, key: tuple, **changes) -> None:
        trigger = self.store.get(*key)
        if trigger is not None:
            self._changed(self.store.update(*key, durable=False, stats={**trigger.stats, **changes}))

    def _bump(self, key: tuple, name: str, count: int = 1) -> None:
        trigger = self.store.get(*key)
        if trigger is not None and count:
            self._changed(self.store.update(*key, durable=False,
                                            stats={**trigger.stats, name: trigger.stats.get(name, 0) + count}))

    def _occurrence(self, key: tuple, item: dict, generation: int) -> None:
        with self._lock:
            entry = self._armed.get(key)
        if entry is None or entry["generation"] != generation:
            return
        policy = entry["policy"]
        if not self._linked():
            policy.skipped_down()
            self._bump(key, "skipped")
            return
        decision = policy.offer(self._monotonic())
        if decision.action == "start":
            self._start(key, item, decision.missed)
        elif decision.action == "missed":
            self._bump(key, "missed")
        elif decision.action == "queued":
            # The policy keeps the newest `queue` occurrences and counts the dropped oldest; keep the same ones.
            pending = entry.setdefault("queue", [])
            pending.append(item)
            dropped = max(0, len(pending) - policy.pending)
            del pending[:dropped]
            self._bump(key, "missed", dropped)
        if policy.throttle_started():
            self._throttled(key, policy)

    def _due(self) -> None:
        with self._lock:
            ready = [(key, entry) for key, entry in self._armed.items() if entry.get("queue")]
        if ready and not self._linked():
            return  # queued occurrences wait for the plugin: no start while it is unlinked (S3)
        for key, entry in ready:
            decision = entry["policy"].due(self._monotonic())
            if entry["policy"].throttle_started():
                self._throttled(key, entry["policy"])
            if decision is None:
                continue
            if decision.action == "start":
                self._start(key, entry["queue"].pop(0), decision.missed)

    def _ended(self, key: tuple) -> None:
        # The kept policy, not only an armed one: a run that ends while its trigger is disabled or paused must
        # still leave it, or re-enabling carries a phantom active run and every occurrence is missed.
        with self._lock:
            policy = self._policies.get(key)
        if policy is not None:
            policy.finished(self._monotonic())

    def _principal(self, trigger: Trigger) -> Principal:
        """A ``run`` principal of the owner, bound to the grant revision approved when the trigger was armed.

        Like any run it outlives the owner's session; a revoke and regrant change the grant revision, so it no
        longer authorizes. An owner without a recorded player id never becomes the console.
        """
        approved = trigger.approved
        if trigger.owner == "console":
            return Principal("console")
        xuid = trigger.owner.removeprefix("xuid:") if trigger.owner.startswith("xuid:") else None
        player_id = approved.get("playerId")
        if player_id is None and trigger.owner.startswith("player:"):
            player_id = trigger.owner.removeprefix("player:")
        if player_id is None:
            player_id = "unknown"  # authorizes nothing: no grant is recorded for it
        return Principal("run", name=trigger.owner_name, xuid=xuid, player_id=player_id,
                         grant_revision=approved.get("grantRevision"), role=self._role(xuid) if xuid else None)

    def _start(self, key: tuple, item: dict, missed: int) -> None:
        trigger = self.store.get(*key)
        if trigger is None or trigger.status != "armed":
            return
        principal = self._principal(trigger)
        if not self._authorize(principal):
            self.disable_for(key, "owner revoked")
            return
        try:
            script = self.scripts.store.get(trigger.script)
        except (KeyError, LocalizedError):
            script = None
        if script is None or not self.scripts.can_run(script, principal):
            self.pause_for(key, "script unavailable")
            return
        found_revision = script.revision
        if (script.owner not in (trigger.owner, FILE_OWNER)
                and found_revision != trigger.approved.get("revision")):
            # Someone else's script runs only at the revision its trigger's owner approved (S16): a new revision
            # would run future code with the owner's authority, so it waits for the owner to enable it again. The
            # operator's script files follow their current revision like one's own (S17): the operator already
            # controls the server.
            self.pause_for(key, "script changed")
            return
        try:
            found = self._declaration(key, script, principal)
        except LocalizedError as error:
            self._stat(key, lastError=str(error)[:256])
            self._bump(key, "skipped")
            return
        if not set(found.requires) <= set(trigger.approved.get("declaration", ())):
            self.pause_for(key, "declaration grew")
            return
        if event_parameter(found.schema) is None or params_hash(found.schema) != trigger.approved.get("paramsHash"):
            self.pause_for(key, "arguments no longer valid")
            return
        occurrence = {**item, "missed": missed}
        with self._state:
            # The inspection took time: start only if nothing changed meanwhile.
            current = self.store.get(*key)
            with self._lock:
                entry = self._armed.get(key)
            try:
                script = self.scripts.store.get(trigger.script)
            except (KeyError, LocalizedError):
                script = None
            if (current is None or current.status != "armed" or entry is None or current.approved != trigger.approved
                    or script is None or script.revision != found_revision or not self._linked()
                    or not self.scripts.can_run(script, principal)):
                return
            try:
                self.scripts.start(script, list(trigger.arguments), principal, requires=tuple(found.requires),
                                   event=occurrence, trigger=trigger.name,
                                   on_end=lambda run, outcome, key=key: self._work.put(("ended", key)))
            except LocalizedError as error:
                # At the owner's concurrent-run limit, or scripting is off: the start is skipped and counted (E7),
                # and the owner is told once until a start succeeds again (S18).
                self._stat(key, lastError=str(error)[:256])
                self._bump(key, "skipped")
                if not entry.get("blocked"):
                    entry["blocked"] = True
                    self._tell(trigger, "trigger.blocked", reason=reason_data(error))
                return
            entry["blocked"] = False
            entry["policy"].started(self._monotonic())
            updated = self.store.update(*key, durable=False,
                                        stats={**current.stats, "starts": current.stats.get("starts", 0) + 1,
                                               "lastStart": self._wall(), "lastError": None})
        self._changed(updated)

    def _declaration(self, key: tuple, script, principal):
        """The script's declaration at its current revision, inspected once per revision."""
        with self._lock:
            entry = self._armed.get(key) or {}
            cached = entry.get("declaration")
        if cached is not None and cached[0] == script.revision:
            return cached[1]
        found = self.scripts.inspect(script, principal)
        with self._lock:
            if key in self._armed:
                self._armed[key]["declaration"] = (script.revision, found)
        return found

    # -- state changes -----------------------------------------------------

    def pause_for(self, key: tuple, reason: str) -> None:
        with self._state:
            self._disarm(key)
            if self.store.get(*key) is None:
                return
            updated = self.store.update(*key, status="paused", reason=reason)
            self._log("info", f"trigger {key[1]} of {key[0]} paused: {reason}")
        self._changed(updated)
        self._tell(updated, "trigger.stopped", status="paused", reason=reason)

    def _tell(self, trigger: Trigger, kind: str, **data) -> None:
        """Tell a trigger's owner in chat when online, and the Event log, why it stopped or cannot start (S18)."""
        if self._publish is not None:
            self._publish(Event(self._principal(trigger), kind,
                                {"name": trigger.name, "owner": trigger.owner_name or trigger.owner, **data}))

    def _throttled(self, key: tuple, policy: TriggerPolicy) -> None:
        """The start cap began dropping or holding occurrences: tell the owner once per episode (E8)."""
        trigger = self.store.get(*key)
        if trigger is None:
            return
        self._log("info", f"trigger {trigger.name} of {trigger.owner_name or trigger.owner} throttled: "
                          f"{trigger.policy['startsPerMinute']} starts per minute")
        self._tell(trigger, "trigger.throttled", limit=trigger.policy["startsPerMinute"],
                   queued=trigger.policy["concurrency"] == "queue")

    def disable_for(self, key: tuple, reason: str) -> None:
        with self._state:
            self._disarm(key)
            trigger = self.store.get(*key)
            if trigger is None:
                return
            updated = self.store.update(*key, status="disabled", reason=reason)
        self._changed(updated)
        self._log("warning", f"trigger {trigger.name} of {trigger.owner_name or trigger.owner} disabled: {reason}")
        self._tell(updated, "trigger.stopped", status="disabled", reason=reason)

    def grants_changed(self) -> None:
        """A grant changed: every trigger whose owner no longer holds the approved grant is disabled (E7)."""
        for trigger in self.store.list():
            if trigger.status == "armed" and not self._authorize(self._principal(trigger)):
                self.disable_for(_key(trigger), "owner revoked")

    # -- operations (commands, forms and the web call these) -----------------

    def _approve(self, principal: Principal, script_name: str, kind: str, filter_: dict, arguments: list, *,
                 stored: bool = False):
        """Check that the principal may arm this binding and what it approves; LocalizedError says why not.

        A ``stored`` filter is already canonical (enable re-approves it): only the kind and its capability are
        checked against the script's current declaration then.
        """
        script = self.scripts._script(script_name, principal)
        found = self.scripts.inspect(script, principal)
        if event_parameter(found.schema) is None:
            raise LocalizedError("Script {name} has no param.event, so a trigger cannot start it", name=script.name)
        # The stored arguments are checked now, as a form run checks its values (E6).
        self.scripts.check_arguments(found.schema, [str(value) for value in arguments], principal)
        if stored:
            check_kind(kind, found.requires)
            canonical = filter_
        else:
            kind, canonical = validate_filter(kind, filter_, found.requires)
        declarations(found.schema)
        return script, found, kind, canonical, {
            "revision": script.revision, "declaration": list(found.requires), "paramsHash": params_hash(found.schema),
            "grantRevision": principal.grant_revision, "playerId": principal.player_id}

    def _visible(self, trigger: Trigger, principal: Principal) -> bool:
        return principal.console or trigger.visibility == "shared" or trigger.owner == _owner(principal)

    def _own(self, owner: str | None, name: str, principal: Principal) -> Trigger:
        owner = owner or _owner(principal)
        trigger = self.store.get(owner, name)
        if trigger is None or not self._visible(trigger, principal):
            raise LocalizedError("Trigger {name} does not exist", name=name)
        if not principal.console and trigger.owner != _owner(principal):
            raise LocalizedError("Only the owner {owner} may change trigger {name}",
                                 owner=trigger.owner_name or "?", name=name)
        return trigger

    def list(self, principal: Principal) -> list[Trigger]:
        return [trigger for trigger in self.store.list() if self._visible(trigger, principal)]

    def visible(self, trigger: Trigger, principal: Principal) -> bool:
        """Whether ``principal`` may see this trigger (the same rule ``get`` and ``list`` use)."""
        return self._visible(trigger, principal)

    def get(self, owner: str | None, name: str, principal: Principal) -> Trigger:
        trigger = self.store.get(owner or _owner(principal), name)
        if trigger is None or not self._visible(trigger, principal):
            raise LocalizedError("Trigger {name} does not exist", name=name)
        return trigger

    def create(self, principal: Principal, *, script: str, kind: str, filter_: dict | None = None,
               arguments: list | None = None, name: str | None = None, visibility: str = "private",
               policy: dict | None = None) -> Trigger:
        """Create and arm a trigger: the creator approves the script's declaration now (E7)."""
        if self.max_triggers == 0:
            raise LocalizedError("Triggers are turned off on this server")
        owner = _owner(principal)
        if not principal.console and self.store.count_armed(owner) >= self.max_triggers:
            raise LocalizedError("A controller may have at most {limit} armed triggers", limit=self.max_triggers)
        found_script, _, kind, canonical, approved = self._approve(principal, script, kind, filter_ or {},
                                                                   arguments or [])
        trigger = Trigger(
            name=name or found_script.name, owner=owner, owner_name=principal.name, visibility=visibility,
            kind=kind, filter=canonical, script=found_script.name, arguments=[str(value) for value in arguments or []],
            approved=approved, status="armed", reason=None, policy=policy_from(policy or {}),
            stats={"starts": 0, "missed": 0, "skipped": 0, "lastStart": None, "lastGap": None, "lastError": None})
        self.store.add(trigger)
        try:
            self._arm(trigger)
        except BaseException:
            self.store.remove(owner, trigger.name)
            raise
        created = self.store.get(owner, trigger.name)
        self._changed(created)
        return created

    def enable(self, principal: Principal, name: str, owner: str | None = None) -> Trigger:
        """Arm again; this re-approves the script's current declaration and parameters (E7).

        The console may enable someone else's trigger; it is then approved under that owner's current grant and
        still counts toward the owner's armed limit.
        """
        if self.max_triggers == 0:
            raise LocalizedError("Triggers are turned off on this server")
        trigger = self._own(owner, name, principal)
        if trigger.status != "armed" and trigger.owner != "console" and \
                self.store.count_armed(trigger.owner) >= self.max_triggers:
            raise LocalizedError("A controller may have at most {limit} armed triggers", limit=self.max_triggers)
        if principal.console and trigger.owner != "console":
            acting = self._principal(trigger)
            acting = Principal("run", name=acting.name, xuid=acting.xuid, player_id=acting.player_id,
                               grant_revision=self._grant_revision(acting.player_id), role=acting.role)
            if not self._authorize(acting):
                raise LocalizedError("The owner {owner} of trigger {name} no longer holds a controller grant",
                                     owner=trigger.owner_name or "?", name=name)
        else:
            acting = principal
        _, _, _, _, approved = self._approve(acting, trigger.script, trigger.kind, trigger.filter, trigger.arguments,
                                             stored=True)
        with self._state:
            trigger = self.store.update(trigger.owner, name, status="armed", reason=None, approved=approved)
            self._arm(trigger)
        self._changed(trigger)
        return trigger

    def disable(self, principal: Principal, name: str, owner: str | None = None) -> Trigger:
        trigger = self._own(owner, name, principal)
        with self._state:
            self._disarm(_key(trigger))
            trigger = self.store.update(trigger.owner, name, status="disabled", reason="disabled by its owner")
        self._changed(trigger)
        return trigger

    def remove(self, principal: Principal, name: str, owner: str | None = None) -> None:
        trigger = self._own(owner, name, principal)
        with self._state:
            self._disarm(_key(trigger))
            self.store.remove(trigger.owner, name)
            with self._lock:
                self._policies.pop(_key(trigger), None)
        self._changed(trigger, removed=True)

    def set_policy(self, principal: Principal, name: str, values: dict, owner: str | None = None) -> Trigger:
        trigger = self._own(owner, name, principal)
        policy = policy_from({**trigger.policy, **values})
        with self._state:
            trigger = self.store.update(trigger.owner, name, policy=policy)
            if trigger.status == "armed":
                self._arm(trigger)
        self._changed(trigger)
        return trigger

    def set_visibility(self, principal: Principal, name: str, visibility: str, owner: str | None = None) -> Trigger:
        trigger = self._own(owner, name, principal)
        trigger = self.store.update(trigger.owner, name, visibility=visibility)
        self._changed(trigger)
        return trigger

