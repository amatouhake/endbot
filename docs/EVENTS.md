# Events, watches and triggers

How scripts observe the world: event kinds in the registry, watches that a run waits on, and triggers that start
runs. Shipped in 0.7.0. This document describes the registry format, the delivery path and the watch and trigger
rules, with the reason for each. Code comments cite the decisions and settled points by number (E1 to E10, S1 to
S18). What each kind guarantees rests on the measurements in [EVENT_MEASUREMENTS.md](EVENT_MEASUREMENTS.md); what was
checked by people is in [COMPATIBILITY.md](COMPATIBILITY.md#events-watches-and-triggers).

It follows from [Decision 5](DIRECTION.md#decision-5-events-are-a-registry-defined-framework-scripts-wait-triggers-start-runs)
and builds on endbotd ([ENDBOTD.md](ENDBOTD.md)), the permission model ([PERMISSIONS.md](PERMISSIONS.md)) and the web
([WEB.md](WEB.md)).

**Naming.** `endbot_core.events.Event` names Event log entries (audit lines, run logs, run ends). A registry entry is
an **event kind** and one delivered instance an **occurrence**, so the two never mix.

## Overview

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

`operations.json` has a top-level `events` object keyed by kind name (`category.action`). The schema requires every
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
  "guarantee": {"class": "persist", "ticks": null, "each": [], "record": "EVENT_MEASUREMENTS.md#block-changes"}
}
```

- `source`: `plugin`, `runtime` or `endbotd`. It decides which adapter reports the kind and what a gap means (E4).
- `capability`: the PERMISSIONS principle 2 name. Basic-set kinds (`read.world`) need no declaration; others
  (`observe.chat`, `observe.presence`) must appear in the script's `requires {}`.
- `visibility`: the PERMISSIONS data class; every listed kind is `public`. `not-visible` and `private` are schema values
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
  measurement found backed by a reliable Endstone event: `player-break` and `player-place`).
- `record` points at the validation document and anchor that measured the guarantee. A registry test requires the
  anchor to exist and, for `persist`, `ticks` to be a positive integer. **A kind with `ticks: null` fails the test**,
  so a kind cannot be listed before it has been measured; the JSON above is the shape, not a listing.
- The docs and `api.luau` print the guarantee from the registry ("a change that lasts at least 4 ticks is delivered;
  player breaking and placing are delivered individually"), so it is stated once.

### E3. `block.change`: Endstone events where measured, a batched poll for the rest

- **Event-backed causes.** For each of those causes the plugin listens at monitor priority and, one
  tick later, reads the position on the server thread and reports only if the block actually changed. Cancelled or
  rewritten actions are therefore never reported, and the payload is what the world holds, not what an event
  proposed.
- **Polled positions.** The plugin reads every watched position once every 4 ticks, a quarter of them on each tick,
  and compares it with the last value it saw; a difference is reported with cause `poll`. The period was measured
  and is fixed, not an operator setting, so `guarantee.ticks` (4) stays true
  ([record](EVENT_MEASUREMENTS.md#block-changes)).
- **Unloaded chunks** are skipped by the poll. When the chunk is loaded again a difference is reported late, with
  cause `poll`. Reading an unloaded position returns air rather than failing, so the source checks which chunks are
  loaded and keeps the baseline of the others ([record](EVENT_MEASUREMENTS.md#block-changes)).
- **Bounds**: a watch or trigger filter is an area (S7) of at most `maxBlocks` (512) cells; the server watches
  at most `[events] max-watched-blocks` positions in total (default 2048 from the cost measurement, at most
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
- `w:wait{timeout}` follows interface guide D2: same `timeout` rules and default as the job waits, `ok=true` with
  `data` = the payload plus `kind`, `ok=false, code="timeout"` at the timeout. After a gap the next wait returns once
  `ok=false` with the temporary-failure code and reason `source restarted`, then continues; a script that cannot
  tolerate a gap re-reads with `block()`.
- **The wait is pushed, not polled**: the runner host sends one wait request and endbotd answers when an occurrence
  is buffered or the timeout passes. A wait uses no operation budget and counts as one IPC event; it rechecks
  authority on return, as the job waits do.
- **Limits**: 16 watches per run; `[limits] max-watches-per-controller` (default 32) across the controller's runs and
  triggers' runs; the controller's `max-watched-blocks-per-controller` and the server-wide `max-watched-blocks`. A watch is owned by the run's principal and ends with the run,
  which already ends on revoke.
- Manual polling with `block()` and `wait()` keeps working.

### E6. Triggers are stored bindings in endbotd

`state/triggers.json` (`schemaVersion: 1`, atomic writes), registered with the store migration marker and the
doctor `stores` check ([ENDBOTD.md P7](ENDBOTD.md#p7-stores-live-in-state-and-both-update-directions-carry-them)). Versions before 0.7.0 ignore the unknown file, so a rollback keeps the triggers on disk and simply
does not run them.

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
  [PERMISSIONS.md](PERMISSIONS.md#two-layers-the-person-and-the-script)) and records `approved`. Only someone who may run the script may arm it; `[limits]
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
  `disable`, `remove`, `set <name> <policy>=<value>`, names per the [interface guide](INTERFACE_GUIDE.md#d3-command-roots).
  `enable` is the approval.
- **Forms**: the script menu has Triggers: a list, and an add form generated from the kind's `filters` and the
  script's params, the same generator the run form uses. Forms mirror the commands and add no meaning.
- **Web**: the web protocol (WEB P2) carries `trigger.list|get|create|update|enable|disable|remove` and
  `event.kinds` (the registry `events` object); `subscribe trigger:<name>` pushes state and
  stats, and `subscribe triggers` the same for every visible trigger, for the list. Each message calls the same endbotd trigger operation as the command, so authorization is identical
  (principle 1). The UI lists the triggers the principal may see and edits their own, with the editor generated from
  the registry like the Bot control forms, and shows the declaration before enabling.
- The web's registry-driven form generator takes a field list, so the trigger editor reuses it for filters. The web
  never shows occurrences live.

## Settled points

- **S1. Human players' names are not in `block.change`**; only Bot names (`bot`) are given. A name next to a
  coordinate tells a watcher where that player is, which PERMISSIONS puts in "not normally visible".
- **S2. Default limits** are those in E3, E5 and E8: 8 triggers and 32 watches per controller, 10 starts per minute,
  a 20-tick cooldown and `skip`; `max-watched-blocks` (2048) and the fixed 4-tick poll come from the block-change measurement;
  1024 watched blocks per controller (S15).
- **S3. No trigger starts while BDS is stopped**; such starts are counted as skipped, and `server.started` fires when
  BDS comes back.
- **S4. Changes during a gap are reported as a gap, never reconstructed.** Replaying from a stored baseline would
  deliver changes late and out of order, and the gap result lets a script re-read.
- **S5. A trigger runs the script's current revision** while its declaration does not grow and its params accept the
  stored arguments (E7); this is how PERMISSIONS' "bound to the script revision its owner approved" is read. Pinning
  the exact source would pause every trigger on every edit, including fixes to one's own script, and would require
  the store to keep old revisions runnable. S16 narrows this for someone else's script.

- **S6. Events and the web ship together**, in 0.7.0, with one human pass for both.
- **S7. Area filters use the area value of the commands.** `block.change` takes one `area` filter (cell, box, sphere and
  `except`, as `within`/`except` and Luau `area.*`), refused when its resolved cells exceed `maxBlocks` (512). A watch
  or trigger has no Bot to resolve against, so `~` and the Bot-centred sphere are refused with a message saying to
  build the area from a position (`area.box(...)` with a Bot's coordinates); the stored and reported area is always
  absolute. One cell replaces the former `x`/`y`/`z` filter.
- **S8. The link is `PROTOCOL = 3`.** Areas moved it to 2 and events to 3.
- **S9. Block changes are measured with Bots, not a human.** A Bot's break and place are real player actions on BDS
  and raise the same events; the [measurement](EVENT_MEASUREMENTS.md#block-changes) says so, and a human breaking and
  placing was checked in the 0.7.0 release pass.
- **S10. `player.chat` is an operator opt-in** (`[events] chat`, off by default). On the
  locked Endstone the chat event comes before a behavior pack's `chatSend` mute and BDS's chat filter run, so a
  message nobody saw could reach a script; operators who do not mute chat with a pack turn it on
  ([record](EVENT_MEASUREMENTS.md#presence-and-chat)).

- **S11. `bot.online` and `bot.offline` are a sample, not a feed.** endbotd reads the runtime's Bot profiles once a
  second, so their guarantee is `persist` 40 ticks (a state that lasts two seconds is delivered), they are
  runtime-sourced, a failed poll is a gap, and a watch or trigger made after the last sample gets no change that may
  predate it ([record](EVENT_MEASUREMENTS.md#runtime-and-server-kinds)).
- **S12. Area filters and the poll.** A watched area is absolute (`~` and the sphere around a Bot are refused: a watch
  has no Bot to resolve against), holds at most 512 cells and at most 16 `except` clauses in 1024 characters (every
  occurrence is tested against every shape on the link thread), and spells the End `end` as commands do. The 4-tick
  poll is fixed, not an operator setting, so `guarantee.ticks` stays true.
- **S13. A trigger pauses when its script's parameters change** (`arguments no longer valid`), even if the stored
  arguments would still be accepted; enabling it again re-approves and checks the arguments. Stored arguments are
  also checked when the trigger is armed.

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

## Not included

Cancelling or modifying events (Decision 2); other players' positions or inventories and direct messages; kinds beyond
the first list in Decision 5 (`server.stopped`, `run.finished`, ... come on request with their own registry entry);
replaying or reconstructing occurrences across gaps; live occurrence streams on the web; triggers starting other
triggers' scripts by name or calls between scripts; persisting a run's watches across endbotd restarts.
