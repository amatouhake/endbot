# Events, watches and triggers design (BOT-022)

Status: **Design draft** (2026-10-29 JST), awaiting the operator's agreement. Nothing here is implemented.

This document turns [Decision 5](DIRECTION.md#decision-5-events-are-a-registry-defined-framework-scripts-wait-triggers-start-runs)
into a registry format, a delivery path, watch and trigger semantics, and a step plan. It builds on endbotd
([ENDBOTD.md](ENDBOTD.md)), the permission model ([PERMISSIONS.md](PERMISSIONS.md)) and the web
([WEB.md](WEB.md)). BOT-020, BOT-024, BOT-021 and BOT-022 ship in one release with one validation pass
([Delivery](DIRECTION.md#delivery)); the web comes before BOT-022, so BOT-022 adds its messages to the web protocol.

## Where we start

- **No world events reach scripts.** Scripts poll with `block()` and `wait(ticks)`. The waits from BOT-016
  (`job:wait`, `waitOnline`, `waitMining`) poll endbotd-side status every `max(0.25, 1 / operations-per-second)` wall
  seconds, take `{timeout = ticks}` (default 1200) and return the common result shape `{ok, code, reason, ...}`
  ([Jobs and waits](SCRIPTING.md#jobs-and-waits)).
- **The plugin registers no Endstone block, chat or presence listeners** for scripts. Presence (join, quit, locale)
  is reported over the link for authorization only (ENDBOTD P4).
- **The registry** is `core/src/endbot_core/operations.json` (`operations`, `reservedBotNames`) with
  `operations.schema.json`. The link's `hello` carries `registryHash`, a hash of the whole registry, and a mismatch
  refuses the link (#133).
- **The link** (ENDBOTD P2, merged as #133 with an in-process harness) carries `hello`, `presence`, `submit`, `world`,
  `deliver` and `permission`, newline-delimited JSON with a 1 MiB frame bound, `PROTOCOL = 1`. It is not released, so
  BOT-022 can extend it without compatibility cost.
- **Endstone block event coverage on the locked pair is unknown.** Which changes raise an event (player break and
  place, pistons, fluids, redstone, explosions, growth, decay, falling blocks) has not been measured. This design does
  not assume any of it.
- **Naming.** `endbot_core.events.Event` already names Event log entries (audit lines, run logs, run ends). This
  document calls a registry entry an **event kind** and one delivered instance an **occurrence**; new code uses
  those names so the two never mix.

## Target

```text
  Endstone listeners ─┐                         ┌─► watch buffers ─► w:wait{} in runs
  batched block poll ─┼─ plugin ── link ──► endbotd ─┤
  (world time)       ─┘  (filters at origin)    └─► trigger engine ─► starts runs (owner ∩ approved declaration)
  runtime (Bot online/offline/died) ───────────►    ▲
  endbotd itself (server.started, schedule, job.finished) ┘
                         ▲
        interest set (what to watch) from endbotd
```

- **endbotd owns watches, triggers and fan-out.** It computes one interest set from every live watch and armed
  trigger and sends it to the source; sources report only what is in it.
- **The plugin is a filter and a reporter.** It holds no ownership or visibility logic; it reports occurrences
  matching the interest set, batched per tick.
- **Every kind is a registry entry**, so Luau completion, `api.luau` docs, the in-game trigger form and the web
  trigger editor are generated from it ([interface guide](INTERFACE_GUIDE.md)).

## Decisions

### E1. Event kinds live in the registry under `events`

`operations.json` gains a top-level `events` object keyed by kind name (`category.action`). The schema requires every
field:

```json
"block.change": {
  "source": "plugin",
  "capability": "read.world",
  "visibility": "public",
  "filters": {
    "x": {"type": "coordinate"}, "y": {"type": "coordinate"}, "z": {"type": "coordinate"},
    "area": {"type": "box", "maxBlocks": 512},
    "dimension": {"type": "dimension", "default": "overworld"},
    "from": {"type": "block"}, "to": {"type": "block"}
  },
  "require": [["x", "y", "z"], ["area"]],
  "payload": {"x": "integer", "y": "integer", "z": "integer", "dimension": "string",
              "from": "block", "to": "block", "cause": "string", "bot": "string?"},
  "guarantee": {"class": "persist", "ticks": null, "each": [], "record": "BLOCK_EVENTS_SPIKE.md#result"}
}
```

- `source`: `plugin`, `runtime` or `endbotd`. It decides which adapter reports the kind and what a gap means (E4).
- `capability`: the PERMISSIONS principle 2 name. Basic-set kinds (`read.world`) need no declaration; others
  (`observe.chat`, `observe.presence`) must appear in the script's `requires {}`.
- `visibility`: the PERMISSIONS data class, `public` only in BOT-022. `not-visible` and `private` are schema values
  that the registry test rejects for any listed kind, so "other players' positions" and `/tell` cannot be listed by
  mistake.
- `filters`: field name and type; `require` lists the alternative sets of which one must be given. A filter with a
  bound (`maxBlocks`) states it here, and every surface enforces it from here.
- `payload`: the fields a run sees in `r.data` or `a.event`; `?` marks an optional field.

**Versioning.** Kinds are additive: a released kind may gain optional payload fields and optional filters, never lose
or retype one. An incompatible change is a new kind name. No per-kind version number: the link already refuses a
registry mismatch (both ends are one app version), and the web refuses a `web-protocol` mismatch (WEB P2), so the only
consumer that outlives a version is a stored trigger, which E7 handles by name.

### E2. The guarantee field states a measured minimum

Every kind delivers **at most once, after the fact, in source order**, and never across a source gap (E4). On top of
that, `guarantee.class` is one of:

| Class | Meaning | Required fields |
| --- | --- | --- |
| `each` | Every occurrence while the source is connected is delivered | — |
| `persist` | A state that holds for at least `ticks` ticks is delivered; shorter states may be missed or coalesced | `ticks` |

- `each` on a `persist` kind lists the causes that are delivered individually (for `block.change`, the causes the
  spike finds backed by a reliable Endstone event).
- `record` points at the validation document and anchor that measured the guarantee. A registry test requires the
  anchor to exist and, for `persist`, `ticks` to be a positive integer. **A kind with `ticks: null` fails the test**,
  so `block.change` cannot be listed until step 1 has measured it; the JSON above is the shape, not a listing.
- The docs and `api.luau` print the guarantee from the registry ("a change that lasts at least 4 ticks is delivered;
  player breaking and placing are delivered individually"), so it is stated once.

### E3. `block.change`: Endstone events where measured, a batched poll for the rest

- **Event-backed causes.** For each cause the spike finds covered, the plugin listens at monitor priority and, one
  tick later, reads the position on the server thread and reports only if the block actually changed. Cancelled or
  rewritten actions are therefore never reported, and the payload is what the world holds, not what an event
  proposed.
- **Polled positions.** Every `block-poll-ticks` ticks (`[events]` in `endbot.toml`; the default and the resulting
  `guarantee.ticks` come from step 1) the plugin reads every watched position in one server-thread pass and compares
  it with the last value it saw. A difference is reported with cause `poll`.
- **Unloaded chunks** are skipped by the poll. When the chunk is loaded again a difference is reported late, with
  cause `poll`; the spike confirms that reading an unloaded position is detectable rather than a default block.
- **Bounds**: a watch or trigger filter covers one position or a box of at most `maxBlocks` (512); the server watches
  at most `[events] max-watched-blocks` positions in total (default from step 1's cost measurement, 4096 assumed).
  A watch that would exceed it fails at creation with the limit named.
- **Who did it** (O1): `cause` is `player-break`, `player-place`, another measured cause, or `poll`; `bot` is set
  when a Bot did it. A human player's name is not in the payload, because it would reveal that player's position.

### E4. Delivery through the link

Two link messages, and `PROTOCOL = 2`:

```text
interest   endbotd → plugin   {revision, block: [{dimension, x, y, z} | {dimension, from, to}], kinds: [...]}
events     plugin → endbotd   {revision, epoch, tick, items: [{kind, ...payload}], dropped}
```

- `interest` replaces the whole set; endbotd sends it on every change and after every `hello`. The plugin applies it
  on the server thread and resets its poll baseline for new positions (a new position is never reported as changed).
- `events` batches one tick's occurrences into one frame, at most 256 items; the rest are counted in `dropped`.
  Items carry no owner: endbotd maps them to watches and triggers and applies visibility.
- **Gaps.** A source is in a gap from a link drop, a new `epoch`, `dropped > 0`, a runtime restart or an endbotd
  restart until its next report. Every watch and trigger fed by that source records the gap (E5, E8). Occurrences
  during a gap are not replayed or reconstructed (O4).
- Runtime-sourced kinds (`bot.online`, `bot.offline`, `bot.died`) come from the runtime status endbotd already
  receives; if the runtime protocol has no push for them, step 6b adds one long-poll request rather than polling per
  watch. endbotd-sourced kinds (`server.started`, `schedule`, `job.finished`) are raised inside endbotd.

### E5. Watches

```lua
local w = watch("block.change", {x = 100, y = 64, z = 200, to = "minecraft:air"})
local r = w:wait{timeout = 1200}   -- {ok, code, reason, data, missed}
w:close()                          -- optional; a watch ends with its run
```

- `watch()` validates the kind, filter names, types, `require` and bounds from the registry, and the kind's
  capability against the run's declaration, before returning; any failure is an error naming the field or capability.
- **Buffering starts at `watch()`**, not at `wait()`, so nothing is lost between waits. Each watch keeps at most 64
  occurrences, dropping the oldest; `r.missed` counts drops since the previous successful wait.
- `w:wait{timeout}` follows interface guide D2: same `timeout` rules and default as the BOT-016 waits, `ok=true` with
  `data` = the payload plus `kind`, `ok=false, code="timeout"` at the timeout. After a gap the next wait returns once
  `ok=false` with the temporary-failure code and reason `source restarted`, then continues; a script that cannot
  tolerate a gap re-reads with `block()`.
- **The wait is pushed, not polled**: the runner host sends one wait request and endbotd answers when an occurrence
  is buffered or the timeout passes. A wait uses no operation budget and counts as one IPC event; it rechecks
  authority on return, as today's waits do.
- **Limits**: 16 watches per run; `[limits] max-watches-per-controller` (default 32) across the controller's runs and
  triggers' runs; the server-wide `max-watched-blocks`. A watch is owned by the run's principal and ends with the run,
  which already ends on revoke.
- Manual polling with `block()` and `wait()` keeps working.

### E6. Triggers are stored bindings in endbotd

`state/triggers.json` (`schemaVersion: 1`, atomic writes), registered with the BOT-020 step 4 migration marker and the
doctor `stores` check. Older versions ignore the unknown file, so a rollback keeps the triggers on disk and simply
does not run them; the release notes state it.

```text
trigger = { name, owner (identity id), visibility (private|shared),
            kind, filter, script, arguments,
            approved: {revision, declaration, paramsHash},
            state: armed | paused(reason) | disabled(reason),
            policy: {concurrency, cooldown, startsPerMinute, queue},
            stats: {starts, missed, skipped, lastStart, lastGap, lastError} }
```

- `name` is unique per owner and defaults to the script name; the console addresses `owner/name`.
- The script declares `param.event("event")` in `params{}`; binding a script without it is refused. A manual run gets
  `a.event = nil`, so one script works both ways. `a.event` carries the payload, `kind` and `missed`.
- `arguments` are validated against the params schema at approval, like a form run.
- Ownership follows PERMISSIONS: the owner edits, enables, disables and removes; `shared` only makes the trigger
  visible in other people's lists; the console can do everything and reassign owners.

### E7. Approval, authority and pausing

- **Arming approves.** Creating or enabling a trigger shows the script's declaration (as the run form does,
  PERMISSIONS 2b) and records `approved`. Only someone who may run the script may arm it; `[limits]
  max-triggers-per-controller` (default 8, `0` disables triggers on the server) bounds armed triggers per owner.
- **A started run** has the owner's authority intersected with `approved.declaration`, the `trigger` channel and the
  online-independent layer, like any run. It counts toward the owner's concurrent-run limit; at the limit the start
  is skipped and counted.
- **Revisions** (O5): the trigger runs the script's current revision while that revision's declaration is a subset of
  the approved one and its params still accept the stored arguments. Otherwise it pauses with the reason
  (`declaration grew`, `arguments no longer valid`) until the owner enables it again, which re-approves.
- **Other pauses and disables**: the script deleted or no longer runnable by the owner → paused; the kind no longer in
  the registry (only possible across a rollback) → paused `kind unavailable`; the owner revoked → disabled, not
  deleted, and a regrant does not re-enable it by itself (PERMISSIONS Revocation).

### E8. Rate limits and runaway chains

| Policy | Default | Bounds | Effect |
| --- | --- | --- | --- |
| `concurrency` | `skip` | `skip`, `queue`, `parallel` | `skip`: occurrences while a run from this trigger is active or in cooldown are dropped and counted in `missed`. `queue`: keep the newest `queue` and start them in order. `parallel`: up to 2 runs at once |
| `cooldown` | 20 ticks | 0–72000 | Minimum time between two starts |
| `startsPerMinute` | 10 | 1–60 | Starts in a sliding 60 s window; one more **disables** the trigger (`rate limit`) |
| `queue` | 4 | 1–16 | Pending occurrences under `queue`; the oldest is dropped and counted |

- The cap is what stops a chain (a Bot breaks a block that starts the trigger that breaks blocks). Disabling notifies
  the owner in chat when online, in the Event log and on the web, and the reason stays in `state` until re-enabled.
- Each start passes the `missed` count since the previous start in `a.event.missed`.
- Ordinary run limits (CPU, operation rate, `wall-seconds`) apply to triggered runs unchanged.

### E9. Restarts

| What restarts | Watches | Triggers |
| --- | --- | --- |
| endbotd | End with their runs (run journal, ENDBOTD P6) | Loaded from the store with state, `lastStart` (so cooldown holds) and stats; the minute window restarts empty; `lastGap` is set |
| BDS / plugin link | Gap on plugin-sourced watches; runs keep running | Stay armed; no trigger starts while the link is down (skipped, counted); interest set resent after `hello` |
| runtime | Gap on runtime-sourced watches | Gap recorded; `bot.*` reports resume |

- `server.started` is raised on the first `hello` after BDS starts (a new `epoch` from a fresh plugin process, not a
  reconnect), so "on server start" triggers run once per BDS start.
- With the web enabled and BDS stopped (WEB P10), `schedule` triggers do not start runs: Bot operations would fail
  anyway. They are skipped and counted (O3).

### E10. Commands, forms and the web

- **Commands**: `/botscript trigger add <script> <kind> [field=value ...] [-- <args>]`, `list`, `show`, `enable`,
  `disable`, `remove`, `set <name> <policy>=<value>`, names per the [interface guide](INTERFACE_GUIDE.md) and
  confirmed there in step 5c. `enable` is the approval.
- **Forms**: the script menu gains Triggers: a list, and an add form generated from the kind's `filters` and the
  script's params, the same generator the run form uses. Forms mirror the commands and add no meaning.
- **Web**: BOT-022 adds to the web protocol (WEB P2) `trigger.list|get|create|update|enable|disable|remove` and
  `event.kinds` (the registry `events` object), bumping `web-protocol`; `subscribe trigger:<name>` pushes state and
  stats. Each message calls the same endbotd trigger operation as the command, so authorization is identical
  (principle 1). The UI lists the triggers the principal may see and edits their own, with the editor generated from
  the registry like the WEB step 7 Bot control forms, and shows the declaration before enabling.
- **Coordination with BOT-021**: WEB step 3a's protocol schema reserves the `trigger.*` and `event.*` namespaces;
  WEB step 7's registry-driven form generator takes a field list, so step 7 here reuses it for filters. The web
  never shows occurrences live in BOT-022.

## Open points

Each has a recommended answer; agreeing the document agrees the recommendation unless the operator says otherwise.

- **O1. Human players' names in `block.change`.** Recommended: omitted; only Bot names (`bot`) are given. A name next
  to a coordinate tells a watcher where that player is, which PERMISSIONS puts in "not normally visible".
- **O2. Default limits.** Recommended: those in E3, E5 and E8 (8 triggers and 32 watches per controller, 10 starts
  per minute, a 20-tick cooldown, `skip`), with `max-watched-blocks` and `block-poll-ticks` set from step 1.
- **O3. Triggers while BDS is stopped.** Recommended: none start; they are counted as skipped, and `server.started`
  fires when BDS comes back.
- **O4. Changes during a gap.** Recommended: reported as a gap, never reconstructed. Replaying from a stored baseline
  would deliver changes late and out of order, and the gap result lets a script re-read.
- **O5. Which revision a trigger runs.** PERMISSIONS says a trigger is "bound to the script revision its owner
  approved" and pauses when an edit grows the declaration. Recommended reading: it runs the current revision while
  the declaration does not grow (E7), not a frozen copy of the approved source. Pinning the exact source would pause
  every trigger on every edit, including fixes to one's own script, and would require the store to keep old revisions
  runnable.

## Steps

Each step is split into PRs that keep main releasable. **Claude** marks protocol, threading, security boundaries and
the measurement; **DeepSeek** marks pieces handed over with a precise spec and acceptance tests, reviewed by Claude.
No release is cut from main until BOT-020, BOT-024, BOT-021 and BOT-022 are all in.

1. **Spike: block change coverage on the locked pair.** Can start now. A probe plugin logs every Endstone block,
   piston, fluid, explosion, growth and decay event together with a per-tick read of a test area, while a scenario
   exercises: human break and place, Bot break and place, piston push and retract, lever and redstone toggling a
   door, water and lava flow, sand falling, TNT, fire, leaf decay, crop growth, and a block changed and restored
   within 1, 2, 4 and 8 ticks. Also measured: poll cost per 1000 positions per pass, reads in unloaded chunks, and
   whether `player.join`, `player.leave`, `player.chat` (public only; a `/tell` must raise nothing the plugin
   forwards) and death messages have usable events. Recorded in `docs/BLOCK_EVENTS_SPIKE.md` with a `#result`
   section that sets `guarantee.ticks`, `each`, `block-poll-ticks` and `max-watched-blocks`. *Claude.*
2. **Registry.**
   - **2a. The `events` schema** (E1, E2), the registry tests (required fields, `require` sets, `visibility` must be
     `public`, `record` anchor exists, `persist` needs `ticks`), and `api.luau`/docs generation of kind and guarantee
     text. No kind listed yet. Can land now. *DeepSeek.*
3. **Delivery** (E3, E4). After BOT-020 3c; tests run through the step-2 link harness.
   - **3a. Link `interest` and `events`, `PROTOCOL = 2`, and endbotd fan-out with gap tracking.** *Claude.*
   - **3b. The plugin's `block.change` source**: listeners for the measured causes with the next-tick confirm, the
     batched poll, baselines, unloaded-chunk handling, `dropped`; and the `block.change` registry entry with step 1's
     numbers. *Claude* (server-thread work).
4. **Watches** (E5): `watch()` and `w:wait` in `runner/api.luau` and the host, endbotd buffers, push-style waits,
   capability check against `requires {}` (needs BOT-024 2a), limits. Docs: a Watches section in SCRIPTING.md.
   *Claude.*
5. **Triggers.** After BOT-020 4 (store registration) and BOT-024 3b and 4b (roles and ownership in the check).
   - **5a. Trigger store and policy engine** (E6, E8): `state/triggers.json`, doctor `stores`, and the policy state
     machine as a pure module with injected time, specified by the E8 table. *DeepSeek* for the engine and its table
     tests; *Claude* reviews the store registration.
   - **5b. Approval, authority and pausing** (E7): starting runs with owner ∩ approved declaration, the revision rule,
     pauses, disable on revoke, notification; restart behavior (E9). SECURITY.md updated here. *Claude.*
   - **5c. Commands and forms** (E10). *DeepSeek.*
6. **The other first kinds.** Each adds one source adapter and one registry entry with its guarantee record.
   - **6a. `player.join`, `player.leave`, `player.chat`** with `observe.presence` and `observe.chat`; public chat
     only, from what step 1 found. *Claude* (the visibility boundary).
   - **6b. `bot.online`, `bot.offline`, `bot.died`, `job.finished`, `server.started`.** *DeepSeek.*
   - **6c. `schedule` (wall clock, endbotd timer) and `world.time` (day and night, from the plugin's poll).**
     *DeepSeek.*
7. **Web** (E10). After WEB steps 3a and 7.
   - **7a. `trigger.*` and `event.kinds` messages and `subscribe trigger:`**, calling the step-5 operations. *DeepSeek*
     against the WEB protocol schema; Claude reviews authorization.
   - **7b. Trigger list and editor UI** from the registry, with the declaration shown before enabling. *DeepSeek.*
8. **Validation**, inside the combined release pass (below). Recorded in a docs PR.

### Validation in the single final pass

Live RakNet smoke on the locked pair (`scripts/raknet_spike.py` gains an `--events` case set):

- `block.change` for every cause step 1 listed as `each`, and a change held for `guarantee.ticks` delivered by the
  poll; a change shorter than that is allowed to be missed but never reported twice.
- Watch timeout, `missed` after overflowing the buffer, a filter over the box bound refused, the watch and
  watched-block limits refused with the limit named.
- Trigger skip, queue, cooldown, and a self-feeding chain disabled by the start cap with the owner notified.
- A shared script edited to grow its declaration pauses another person's trigger; enabling re-approves.
- Revoke disables the owner's triggers and ends their watches; regrant leaves them disabled.
- Kill endbotd: triggers come back armed with their stats; restart BDS: watches see the gap, `server.started` fires
  once, no trigger starts while BDS is down.
- `player.chat` from public chat delivered; a `/tell` never delivered; a human's name never in `block.change`.
- Update with triggers, roll back (the old version runs without them and ignores `state/triggers.json`), update again
  (they are back).

Human Windows Client check: the Triggers form and add form, the declaration shown before enabling, the disable
notification in chat, and achievement history unaffected. With the web deployed: create, edit, enable and disable a
trigger from a phone, and the same refusal as in game for someone else's trigger.

## Not in BOT-022

Cancelling or modifying events (Decision 2); other players' positions or inventories and direct messages; kinds beyond
the first list in Decision 5 (`server.stopped`, `run.finished`, ... come on request with their own registry entry);
replaying or reconstructing occurrences across gaps; live occurrence streams on the web; triggers starting other
triggers' scripts by name or calls between scripts; persisting a run's watches across endbotd restarts.
