# Events, watches and triggers design (BOT-022)

Status: **Steps 1 to 7 on `main`** (2026-11-08 JST, #234 to #247); design agreed 2026-10-29 JST, amended
2026-11-08 JST by S6 to S13 (see [settled points](#settled-points)). The Bot-only live smoke passed
([record](EVENTS_VALIDATION.md#bot-only-live-smoke)); step 8, the human checks, ran in the combined release pass on
2026-11-11 and 2026-11-12 JST ([record](EVENTS_VALIDATION.md#release-pass)).

This document turns [Decision 5](DIRECTION.md#decision-5-events-are-a-registry-defined-framework-scripts-wait-triggers-start-runs)
into a registry format, a delivery path, watch and trigger semantics, and a step plan. It builds on endbotd
([ENDBOTD.md](ENDBOTD.md)) and the permission model ([PERMISSIONS.md](PERMISSIONS.md)), both released in 0.5.0, and
on the web ([WEB.md](WEB.md)). BOT-022 is released in 0.7.0 together with BOT-021, after one
validation pass for both ([Delivery](DIRECTION.md#delivery)); the web comes before BOT-022, so BOT-022 adds its
messages to the web protocol.

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
    "area": {"type": "area", "maxBlocks": 512},
    "dimension": {"type": "dimension", "default": "overworld"},
    "from": {"type": "block"}, "to": {"type": "block"}
  },
  "require": [["area"]],
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
- **Polled positions.** The plugin reads every watched position once every 4 ticks, a quarter of them on each tick,
  and compares it with the last value it saw; a difference is reported with cause `poll`. The period comes from step 1
  and is fixed, not an operator setting, so `guarantee.ticks` (4) stays true
  ([record](BLOCK_EVENTS_SPIKE.md#result)).
- **Unloaded chunks** are skipped by the poll. When the chunk is loaded again a difference is reported late, with
  cause `poll`; the spike confirms that reading an unloaded position is detectable rather than a default block.
- **Bounds**: a watch or trigger filter is a BOT-027 area (S7) of at most `maxBlocks` (512) cells; the server watches
  at most `[events] max-watched-blocks` positions in total (default 2048 from step 1's cost measurement, at most
  16384 so the interest set fits one link frame), and one controller's watches and triggers at most `[limits]
  max-watched-blocks-per-controller` of them (default 1024, `0` for no separate share; S15).
  A watch that would exceed either fails at creation with the limit named.
- **Who did it** (S1): `cause` is `player-break`, `player-place`, another measured cause, or `poll`; `bot` is set
  when a Bot did it. A human player's name is not in the payload, because it would reveal that player's position.

### E4. Delivery through the link

Two link messages, and `PROTOCOL = 3` (S8):

```text
interest   endbotd → plugin   {revision, block: [{dimension, cells: [[x, y, z], ...]}], kinds: [...]}
events     plugin → endbotd   {revision, epoch, tick, items: [{kind, ...payload}], dropped}
```

- `interest` replaces the whole set; endbotd sends it on every change and after every `hello`, one at a time in
  revision order (the plugin ignores an older revision). It lists every watched cell, so `[events]
  max-watched-blocks` is capped at 16384, which keeps the set inside the 1 MiB frame. The plugin applies it
  on the server thread and resets its poll baseline for new positions (a new position is never reported as changed).
- `events` batches one tick's occurrences into one frame, at most 256 items; the rest are counted in `dropped`.
  Items carry no owner: endbotd maps them to watches and triggers and applies visibility.
- **Gaps.** A source is in a gap from a link drop, a new `epoch`, `dropped > 0`, a runtime restart or an endbotd
  restart until its next report. Every watch and trigger fed by that source records the gap (E5, E8). Occurrences
  during a gap are not replayed or reconstructed (S4).
- Runtime-sourced kinds (`bot.online`, `bot.offline`) come from the runtime status endbotd already receives (a
  sample, S11). `bot.died` is plugin-sourced: the plugin reports a Bot's death event over the link like
  `block.change`. endbotd-sourced kinds (`server.started`, `schedule`, `job.finished`) are raised inside endbotd.

### E5. Watches

```lua
local w = watch("block.change", {area = area.cell(100, 64, 200), to = "minecraft:air"})
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
  triggers' runs; the controller's `max-watched-blocks-per-controller` and the server-wide `max-watched-blocks`. A watch is owned by the run's principal and ends with the run,
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
- **Revisions** (S5, S16): on the owner's own script the trigger runs the current revision while that revision's
  declaration is a subset of the approved one and its params still accept the stored arguments. Otherwise it pauses
  with the reason (`declaration grew`, `arguments no longer valid`) until the owner enables it again, which
  re-approves. On someone else's script it runs only the approved revision: any new revision pauses it
  (`script changed`) until the trigger's owner enables it again. The operator's script files count as one's own
  (S17).
- **Telling the owner** (S18): a pause, a disable by the engine (`owner revoked`) and a start refused at the owner's
  run limit are told to the owner in chat when online and in the Event log; the refusal once until a start succeeds.
  A triggered run that ends cleanly is logged but not shown in chat; any other end is shown as for any run.
- **Other pauses and disables**: the script deleted or no longer runnable by the owner → paused; the kind no longer in
  the registry (only possible across a rollback) → paused `kind unavailable`; the owner revoked → disabled, not
  deleted, and a regrant does not re-enable it by itself (PERMISSIONS Revocation).

### E8. Rate limits and runaway chains

| Policy | Default | Bounds | Effect |
| --- | --- | --- | --- |
| `concurrency` | `skip` | `skip`, `queue`, `parallel` | `skip`: occurrences while a run from this trigger is active or in cooldown are dropped and counted in `missed`. `queue`: keep the newest `queue` and start them in order. `parallel`: up to 2 runs at once |
| `cooldown` | 20 ticks | 0–72000 | Minimum time between two starts |
| `startsPerMinute` | 10 | 1–60 | Starts in a sliding 60 s window; over it the trigger is **throttled**: `skip` and `parallel` drop the occurrence as missed, `queue` holds it until the window allows a start |
| `queue` | 4 | 1–16 | Pending occurrences under `queue`; the oldest is dropped and counted |

- The cap bounds a chain (a Bot breaks a block that starts the trigger that breaks blocks) to `startsPerMinute`
  starts a minute without stopping ordinary busy triggers (S14). When a trigger starts being throttled its owner is
  told once in chat when online and in the Event log; the next start ends that episode. The dropped occurrences count
  in `missed`.
- `cooldown` with `skip` drops, it does not delay: an occurrence inside the cooldown is counted in `missed`. Use
  `queue` to have it start later.
- Stats reach `triggers.json` at most once a second and on every state change; occurrences waiting for the trigger
  worker are bounded (1024), and those beyond it are counted in `missed`.
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
  anyway. They are skipped and counted (S3).

### E10. Commands, forms and the web

- **Commands**: `/botscript trigger add <script> <kind> [field=value ...] [-- <args>]`, `list`, `show`, `enable`,
  `disable`, `remove`, `set <name> <policy>=<value>`, names per the [interface guide](INTERFACE_GUIDE.md) and
  confirmed there in step 5c. `enable` is the approval.
- **Forms**: the script menu gains Triggers: a list, and an add form generated from the kind's `filters` and the
  script's params, the same generator the run form uses. Forms mirror the commands and add no meaning.
- **Web**: BOT-022 adds to the web protocol (WEB P2) `trigger.list|get|create|update|enable|disable|remove` and
  `event.kinds` (the registry `events` object), bumping `web-protocol`; `subscribe trigger:<name>` pushes state and
  stats, and `subscribe triggers` the same for every visible trigger, for the list (added in the 0.7.0 release pass). Each message calls the same endbotd trigger operation as the command, so authorization is identical
  (principle 1). The UI lists the triggers the principal may see and edits their own, with the editor generated from
  the registry like the WEB step 7 Bot control forms, and shows the declaration before enabling.
- **Coordination with BOT-021**: WEB step 3a's protocol schema reserves the `trigger.*` and `event.*` namespaces;
  WEB step 7's registry-driven form generator takes a field list, so step 7 here reuses it for filters. The web
  never shows occurrences live in BOT-022.

## Settled points

Decided 2026-10-29 JST, following the recommendations of the draft.

- **S1. Human players' names are not in `block.change`**; only Bot names (`bot`) are given. A name next to a
  coordinate tells a watcher where that player is, which PERMISSIONS puts in "not normally visible".
- **S2. Default limits** are those in E3, E5 and E8: 8 triggers and 32 watches per controller, 10 starts per minute,
  a 20-tick cooldown and `skip`; `max-watched-blocks` (2048) and the fixed 4-tick poll come from step 1;
  1024 watched blocks per controller (S15).
- **S3. No trigger starts while BDS is stopped**; such starts are counted as skipped, and `server.started` fires when
  BDS comes back.
- **S4. Changes during a gap are reported as a gap, never reconstructed.** Replaying from a stored baseline would
  deliver changes late and out of order, and the gap result lets a script re-read.
- **S5. A trigger runs the script's current revision** while its declaration does not grow and its params accept the
  stored arguments (E7); this is how PERMISSIONS' "bound to the script revision its owner approved" is read. Pinning
  the exact source would pause every trigger on every edit, including fixes to one's own script, and would require
  the store to keep old revisions runnable. S16 narrows this for someone else's script.

Decided 2026-11-08 JST, before implementation started:

- **S6. All of steps 1 to 7 ship in the next release** with BOT-021; step 8 is part of the combined human pass.
- **S7. Area filters use the BOT-027 area value.** `block.change` takes one `area` filter (cell, box, sphere and
  `except`, as `within`/`except` and Luau `area.*`), refused when its resolved cells exceed `maxBlocks` (512). A watch
  or trigger has no Bot to resolve against, so `~` and the Bot-centred sphere are refused with a message saying to
  build the area from a position (`area.box(...)` with a Bot's coordinates); the stored and reported area is always
  absolute. One cell replaces the former `x`/`y`/`z` filter.
- **S8. The link becomes `PROTOCOL = 3`.** Areas (BOT-027) already moved it to 2, which this document predates.
- **S9. Step 1 measures with Bots, not a human.** A Bot's break and place are real player actions on BDS and raise
  the same events; the spike record says "measured with Bots", and human break and place are checked in the release
  pass. Validation uses NetherNet Bots only (RakNet was removed in 0.5.0).
- **S10. `player.chat` is an operator opt-in** (`[events] chat`, off by default; decided 2026-11-08 JST). On the
  locked Endstone the chat event comes before a behavior pack's `chatSend` mute and BDS's chat filter run, so a
  message nobody saw could reach a script; operators who do not mute chat with a pack turn it on
  ([record](EVENTS_VALIDATION.md#presence-and-chat)).

Approved by the operator 2026-11-08 JST after implementation (they changed the design during review):

- **S11. `bot.online` and `bot.offline` are a sample, not a feed.** endbotd reads the runtime's Bot profiles once a
  second, so their guarantee is `persist` 40 ticks (a state that lasts two seconds is delivered), they are
  runtime-sourced, a failed poll is a gap, and a watch or trigger made after the last sample gets no change that may
  predate it ([record](EVENTS_VALIDATION.md#runtime-and-server-kinds)).
- **S12. Area filters and the poll.** A watched area is absolute (`~` and the sphere around a Bot are refused: a watch
  has no Bot to resolve against), holds at most 512 cells and at most 16 `except` clauses in 1024 characters (every
  occurrence is tested against every shape on the link thread), and spells the End `end` as commands do. The 4-tick
  poll is fixed, not an operator setting, so `guarantee.ticks` stays true.
- **S13. A trigger pauses when its script's parameters change** (`arguments no longer valid`), even if the stored
  arguments would still be accepted; enabling it again re-approves and checks the arguments. Stored arguments are
  also checked when the trigger is armed.

Approved by the operator 2026-11-10 JST after a design review of the implementation (S17 and S18 on 2026-11-11
after a user-perspective review):

- **S14. The start cap throttles, it does not disable.** Disabling at the cap made ordinary busy triggers (a farm
  area, public chat) stop until enabled by hand, so the cost of chain protection fell on normal traffic. Over the cap
  occurrences are dropped (or held under `queue`) and the owner is told once per episode; a chain still costs at most
  `startsPerMinute` starts a minute per trigger, within the owner's trigger and concurrent-run limits.
- **S15. Watched blocks have a per-controller share.** `[limits] max-watched-blocks-per-controller` (default 1024,
  two full-size areas) keeps one controller from filling the server's `max-watched-blocks` and refusing everyone
  else's watches.
- **S16. Someone else's script is pinned to the approved revision.** A trigger on another controller's shared script
  would otherwise run that controller's future code, unattended, with the trigger owner's authority, since `act.bot`
  never appears in a declaration. A new revision pauses it (`script changed`); the trigger's owner enables it again
  to approve the new code. Triggers on one's own script still follow its current revision (S5).
- **S17. The operator's script files follow their current revision.** Pinning them (S16) paused every player's
  trigger on a typo fix and `reload`, and the operator already controls the server, so a file script is trusted like
  one's own: its declaration and parameters are still checked.
- **S18. A trigger that stops tells its owner, and its ordinary runs stay quiet.** Pauses and engine disables were
  only logged, so a trigger simply stopped reacting; now the owner is told why, and a start refused at the run limit
  is told once. A clean end of a triggered run no longer reaches chat, where a busy trigger would fill it.

## Steps

Each step is split into PRs that keep main releasable. **Claude** marks protocol, threading, security boundaries and
the measurement; **DeepSeek** marks pieces handed over with a precise spec and acceptance tests, reviewed by Claude.
BOT-021 and BOT-022 ship together in one release; no release is cut between them.

1. **Spike: block change coverage on the locked pair.** Can start now. A probe plugin logs every Endstone block,
   piston, fluid, explosion, growth and decay event together with a per-tick read of a test area, while a scenario
   exercises: human break and place, Bot break and place, piston push and retract, lever and redstone toggling a
   door, water and lava flow, sand falling, TNT, fire, leaf decay, crop growth, and a block changed and restored
   within 1, 2, 4 and 8 ticks. Also measured: poll cost per 1000 positions per pass, reads in unloaded chunks, and
   whether `player.join`, `player.leave`, `player.chat` (public only; a `/tell` must raise nothing the plugin
   forwards) and death messages have usable events. Recorded in `docs/BLOCK_EVENTS_SPIKE.md` with a `#result`
   section that sets `guarantee.ticks`, `each`, `block-poll-ticks` and `max-watched-blocks`. *Claude.* **Done
   2026-11-08 JST** ([record](BLOCK_EVENTS_SPIKE.md#result)): poll every 4 ticks, `guarantee.ticks` 4, `each` player
   break and place, `max-watched-blocks` 2048.
2. **Registry.**
   - **2a. The `events` schema** (E1, E2), the registry tests (required fields, `require` sets, `visibility` must be
     `public`, `record` anchor exists, `persist` needs `ticks`), and `api.luau`/docs generation of kind and guarantee
     text. No kind listed yet. Can land now. *DeepSeek.*
3. **Delivery** (E3, E4). After BOT-020 3c; tests run through the step-2 link harness.
   - **3a. Link `interest` and `events`, `PROTOCOL = 3`, and endbotd fan-out with gap tracking.** *Claude.*
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

### Validation in the release pass

Live Bot-only NetherNet smoke on the locked pair (`scripts/raknet_spike.py`, which despite its name runs over
NetherNet, gains an `--events` case set):

- `block.change` for every cause step 1 listed as `each`, and a change held for `guarantee.ticks` delivered by the
  poll; a change shorter than that is allowed to be missed but never reported twice.
- Watch timeout, `missed` after overflowing the buffer, a filter over the box bound refused, the watch and
  watched-block limits refused with the limit named.
- Trigger skip, queue, cooldown, and a self-feeding chain throttled by the start cap with the owner notified once.
- A shared script edited by its owner pauses another person's trigger (`script changed`); enabling re-approves.
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
