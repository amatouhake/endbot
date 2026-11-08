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
cap; going over the cap disables the trigger and tells the owner.

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

from endbot_core.events import Event
from endbot_core.i18n import LocalizedError
from endbot_core.ownership import owner_key
from endbot_core.principal import Principal
from endbot_core.script_params import declarations, event_parameter, inputs
from endbot_core.trigger_policy import TriggerPolicy
from endbot_core.trigger_store import Trigger, policy_from
from endbot_core.watches import validate_filter

DEFAULT_MAX_TRIGGERS = 8
TICK_SECONDS = 0.25


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
                 linked: Callable[[], bool] = lambda: True,
                 publish: Callable[[Event], None] | None = None,
                 max_triggers: int = DEFAULT_MAX_TRIGGERS,
                 log: Callable[[str, str], None] | None = None,
                 monotonic: Callable[[], float] = time.monotonic, wall: Callable[[], float] = time.time) -> None:
        self.store, self.hub, self.scripts = store, hub, scripts
        self._authorize, self._role, self._linked = authorize, role, linked
        self._publish = publish
        self.max_triggers = max_triggers
        self._log = log or (lambda level, message: None)
        self._monotonic, self._wall = monotonic, wall
        self._lock = threading.RLock()
        self._armed: dict[tuple, dict] = {}  # key -> {"policy", "subscription", "declarations"}
        self._work: queue.Queue = queue.Queue()
        self._closed = threading.Event()
        self._thread: threading.Thread | None = None

    # -- lifecycle -------------------------------------------------------

    def start(self) -> TriggerEngine:
        """Arm every armed trigger from the store; their cooldown holds across the restart (E9)."""
        now = self._wall()
        for trigger in self.store.list():
            if trigger.status == "armed":
                self.store.update(trigger.owner, trigger.name, stats={**trigger.stats, "lastGap": now})
                self._arm(self.store.get(trigger.owner, trigger.name))
        self._thread = threading.Thread(target=self._loop, name="endbotd-triggers", daemon=True)
        self._thread.start()
        return self

    def close(self) -> None:
        self._closed.set()
        self._work.put(None)
        with self._lock:
            armed, self._armed = self._armed, {}
        for entry in armed.values():
            self.hub.unsubscribe(entry["subscription"])

    def _arm(self, trigger: Trigger) -> None:
        key = _key(trigger)
        last = trigger.stats.get("lastStart")
        elapsed = None if last is None else max(0.0, self._wall() - last)
        policy = TriggerPolicy(trigger.policy, None if elapsed is None else self._monotonic() - elapsed)
        subscription = self.hub.subscribe(
            trigger.kind, trigger.filter,
            lambda item, key=key: self._work.put(("occurrence", key, item)),
            lambda reason, key=key: self._work.put(("gap", key, reason)))
        with self._lock:
            previous = self._armed.pop(key, None)
            self._armed[key] = {"policy": policy, "subscription": subscription}
        if previous is not None:
            self.hub.unsubscribe(previous["subscription"])

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
                    self._occurrence(item[1], item[2])
                elif item[0] == "gap":
                    self._stat(item[1], lastGap=self._wall())
                elif item[0] == "ended":
                    self._ended(item[1])
                self._due()
            except Exception as error:  # noqa: BLE001 - one trigger's failure must not stop the others
                self._log("warning", f"trigger engine: {type(error).__name__}: {error}")

    # -- decisions -------------------------------------------------------

    def _stat(self, key: tuple, **changes) -> None:
        trigger = self.store.get(*key)
        if trigger is not None:
            self.store.update(*key, stats={**trigger.stats, **changes})

    def _bump(self, key: tuple, name: str, count: int = 1) -> None:
        trigger = self.store.get(*key)
        if trigger is not None and count:
            self.store.update(*key, stats={**trigger.stats, name: trigger.stats.get(name, 0) + count})

    def _occurrence(self, key: tuple, item: dict) -> None:
        with self._lock:
            entry = self._armed.get(key)
        if entry is None:
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
        elif decision.action == "disable":
            self._bump(key, "missed")
            self.disable_for(key, decision.reason or "rate limit", notify=True)
        elif decision.action == "queued":
            entry.setdefault("queue", []).append(item)

    def _due(self) -> None:
        with self._lock:
            ready = [(key, entry) for key, entry in self._armed.items() if entry.get("queue")]
        for key, entry in ready:
            decision = entry["policy"].due(self._monotonic())
            if decision is not None and decision.action == "start":
                self._start(key, entry["queue"].pop(0), decision.missed)

    def _ended(self, key: tuple) -> None:
        with self._lock:
            entry = self._armed.get(key)
        if entry is not None:
            entry["policy"].finished(self._monotonic())

    def _principal(self, trigger: Trigger) -> Principal:
        approved = trigger.approved
        if trigger.owner == "console":
            return Principal("console")
        xuid = trigger.owner.removeprefix("xuid:") if trigger.owner.startswith("xuid:") else None
        return Principal("player", name=trigger.owner_name, xuid=xuid, player_id=approved.get("playerId"),
                         grant_revision=approved.get("grantRevision"), role=self._role(xuid) if xuid else None)

    def _start(self, key: tuple, item: dict, missed: int) -> None:
        trigger = self.store.get(*key)
        if trigger is None or trigger.status != "armed":
            return
        principal = self._principal(trigger)
        if not self._authorize(principal):
            self.disable_for(key, "owner revoked", notify=False)
            return
        try:
            script = self.scripts.store.get(trigger.script)
        except (KeyError, LocalizedError):
            script = None
        if script is None or not self.scripts.can_run(script, principal):
            self.pause_for(key, "script unavailable")
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
        try:
            self.scripts.start(script, list(trigger.arguments), principal, requires=tuple(found.requires),
                               event=occurrence, trigger=trigger.name,
                               on_end=lambda run, outcome, key=key: self._work.put(("ended", key)))
        except LocalizedError as error:
            # At the owner's concurrent-run limit, or scripting is off: the start is skipped and counted (E7).
            self._stat(key, lastError=str(error)[:256])
            self._bump(key, "skipped")
            return
        with self._lock:
            entry = self._armed.get(key)
        if entry is not None:
            entry["policy"].started(self._monotonic())
        trigger = self.store.get(*key)
        self.store.update(*key, stats={**trigger.stats, "starts": trigger.stats.get("starts", 0) + 1,
                                       "lastStart": self._wall(), "lastError": None})

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
        self._disarm(key)
        if self.store.get(*key) is not None:
            self.store.update(*key, status="paused", reason=reason)
            self._log("info", f"trigger {key[1]} of {key[0]} paused: {reason}")

    def disable_for(self, key: tuple, reason: str, *, notify: bool) -> None:
        self._disarm(key)
        trigger = self.store.get(*key)
        if trigger is None:
            return
        self.store.update(*key, status="disabled", reason=reason)
        self._log("warning", f"trigger {trigger.name} of {trigger.owner_name or trigger.owner} disabled: {reason}")
        if notify:
            self._notify(trigger, reason)

    def _notify(self, trigger: Trigger, reason: str) -> None:
        """The owner's chat when online, and the console log; the reason also stays in the trigger's state."""
        if self._publish is not None:
            self._publish(Event(self._principal(trigger), "trigger.disabled",
                                {"name": trigger.name, "reason": reason, "owner": trigger.owner_name or trigger.owner}))

    def grants_changed(self) -> None:
        """A grant changed: every trigger whose owner no longer holds the approved grant is disabled (E7)."""
        for trigger in self.store.list():
            if trigger.status == "armed" and not self._authorize(self._principal(trigger)):
                self.disable_for(_key(trigger), "owner revoked", notify=False)

    # -- operations (commands, forms and the web call these) -----------------

    def _approve(self, principal: Principal, script_name: str, kind: str, filter_: dict, arguments: list):
        """Check that the principal may arm this binding and what it approves; LocalizedError says why not."""
        script = self.scripts._script(script_name, principal)
        found = self.scripts.inspect(script, principal)
        if event_parameter(found.schema) is None:
            raise LocalizedError("Script {name} has no param.event, so a trigger cannot start it", name=script.name)
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
        return self.store.get(owner, trigger.name)

    def enable(self, principal: Principal, name: str, owner: str | None = None) -> Trigger:
        """Arm again; this re-approves the script's current declaration and parameters (E7)."""
        trigger = self._own(owner, name, principal)
        if trigger.status != "armed" and not principal.console and \
                self.store.count_armed(trigger.owner) >= self.max_triggers:
            raise LocalizedError("A controller may have at most {limit} armed triggers", limit=self.max_triggers)
        acting = principal if not principal.console or trigger.owner == "console" else self._principal(trigger)
        _, _, _, _, approved = self._approve(acting, trigger.script, trigger.kind, trigger.filter, trigger.arguments)
        trigger = self.store.update(trigger.owner, name, status="armed", reason=None, approved=approved)
        self._arm(trigger)
        return trigger

    def disable(self, principal: Principal, name: str, owner: str | None = None) -> Trigger:
        trigger = self._own(owner, name, principal)
        self._disarm(_key(trigger))
        return self.store.update(trigger.owner, name, status="disabled", reason="disabled by its owner")

    def remove(self, principal: Principal, name: str, owner: str | None = None) -> None:
        trigger = self._own(owner, name, principal)
        self._disarm(_key(trigger))
        self.store.remove(trigger.owner, name)

    def set_policy(self, principal: Principal, name: str, values: dict, owner: str | None = None) -> Trigger:
        trigger = self._own(owner, name, principal)
        policy = policy_from({**trigger.policy, **values})
        trigger = self.store.update(trigger.owner, name, policy=policy)
        if trigger.status == "armed":
            self._arm(trigger)
        return trigger

    def set_visibility(self, principal: Principal, name: str, visibility: str, owner: str | None = None) -> Trigger:
        trigger = self._own(owner, name, principal)
        return self.store.update(trigger.owner, name, visibility=visibility)

