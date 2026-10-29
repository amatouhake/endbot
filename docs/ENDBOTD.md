# endbotd design (BOT-020)

Status: **Design agreed** (2026-10-29 JST): the operator accepted the proposals and the recommended answers to
the former open points (see [settled points](#settled-points)). Nothing here is implemented yet.

This document turns [Decision 1](DIRECTION.md#decision-1-endbot-is-a-distribution-and-its-centre-moves-out-of-bds)
into a concrete process split, protocol, migration and step plan. It changes nothing players can see, adds no web code
and no new operations. The web (BOT-021), permissions (BOT-024) and persistent triggers (BOT-022) build on it, so it
must leave room for them without designing them here.

## Where we start

After BOT-019 the plugin already has a process-neutral core, but it still runs inside BDS:

- **Only three modules import Endstone**: `plugin.py`, `world.py` and `script_books.py`. Everything else
  (`commands.py`, `groups.py`, `scripting.py`, `script_store.py`, `controller_policy.py`, `enrollment.py`, `i18n.py`,
  `operations.py`, `events.py`, `principal.py`, ...) is pure Python tested with fakes.
- **The plugin talks to the runtime directly** over the loopback JSON protocol (`control.py`, one TCP connection per
  request, token in `state/secrets/control.token`).
- **World access is already a port**: `ServerThreadWorld` wraps every Endstone call the service makes
  (`observe`, `target`, `block_info`, `find_blocks`, `teleport`, `resolve_mining`, spawn placement, book pages, name
  checks, ...) and marshals it onto the server thread. Capacity (`max-players`, humans online) is one more callback.
- **Authorization state is split**: the grant/denial policy is persisted in `state/controller-policy.json` and
  `state/controllers.json`, but the grant revision and the per-session authorization revision exist only in plugin
  memory (`_grant_versions`, `_authorization_versions`).
- **Stores live in the plugin data folder**: `server/plugins/endbot/groups.json`, `scripts.json` and the operator's
  `scripts/*.luau`, outside `state/`.
- **One ordered worker** (`AsyncCommandRunner`) serializes every command, script operation and cleanup; mining loops
  renew on a separate poll worker every 4 ticks with server-thread raycasts.
- **Script menus still build views** inside `ScriptService` (`script_forms`), unlike `/bot` and `/botgroup`, whose
  views moved to `menu_views` in BOT-019 step 5.
- **Controller management from the CLI** goes through files: `endbot controllers` writes a request under
  `state/run/controller-requests/`, the supervisor types `botcontrollers request <id>` on the BDS console and the
  plugin writes the response file.
- **The supervisor** starts the runtime, then BDS, forwards console input, restarts the runtime with backoff, and
  stops everything when BDS exits.

## Target

```text
                    endbot CLI (controllers, status, stop)
                                │ loopback, endbotd token
                                ▼
  supervisor (endbot start) ── starts and restarts ──► endbotd ◄── loopback link, endbotd token ── Endbot plugin
        │                                              │  │                                         (inside BDS)
        │                                              │  └─ runner children (Luau, unchanged)
        │                                              ▼
        └── starts ──► runtime (Node)  ◄── loopback control, runtime token (endbotd only)
        └── starts ──► BDS + Endstone + plugin
```

- **The supervisor stays a thin parent.** It starts runtime, endbotd and BDS, forwards the console, restarts runtime
  and endbotd with backoff, and runs the stop sequence. It holds no product logic, so an endbotd bug cannot take the
  supervisor (and with it BDS) down.
- **endbotd is a separate child process**, Python, started from the same `app/<version>/python` as the CLI. It owns
  authorization, controller policy and enrollment, Groups, the script store, runner orchestration, Bot command
  execution, jobs (mining loops and waits), the event log and, later, the web link and triggers.
- **The plugin is a world adapter.** It registers the commands, resolves Endstone senders, reports presence,
  forwards command lines and form replies to endbotd, answers world calls on the server thread, renders forms and
  delivers chat. It holds no policy and no store.
- **The runtime keeps its role and protocol**, but only endbotd (and the supervisor's `shutdown`) talks to it. The
  plugin no longer needs the runtime token.

Start order: runtime → endbotd → BDS. endbotd needs the runtime to execute Bot operations, and the plugin needs
endbotd to accept commands. The supervisor waits for `endbotd_ready` the same way it waits for
`endbot_runtime_ready`.

## Decisions

### P1. Process split: three children under a thin supervisor

This refines "grown from today's supervisor" in the direction document: endbotd grows out of the supervisor's
role (owning the instance's state, talking to the runtime, answering the CLI), but as its own child process.

Rejected alternative: endbotd inside the supervisor process. It saves one process but couples the console, the stop
sequence and product logic in one interpreter; a crash or a hung lock in policy code would stop the instance. The
direction document's claim "while endbotd is down, in-game commands are refused" also assumes it can be down while
BDS runs.

Restart policy: like the runtime (1 s, 2 s, 4 s ... capped at 30 s; 5 failures in 5 minutes stop the instance).
While endbotd is down the plugin refuses every Endbot command with "Endbot service is restarting" and keeps the
last control-permission attachments, so command visibility does not flicker.

### P2. One persistent duplex link between plugin and endbotd

The plugin connects out to endbotd; the plugin never listens. Over that one connection both sides send requests and
replies, newline-delimited JSON, each with an id:

```text
hello      plugin → endbotd   {protocol, pluginVersion, registryHash, epoch}
           endbotd → plugin   {protocol, endbotdVersion, config: {language, hotbar...}}
presence   plugin → endbotd   snapshot | join | quit | locale   (see P4)
submit     plugin → endbotd   {root: bot|botgroup|botscript|botcontrollers, argv | operation, sender, guard?}
world      endbotd → plugin   {method, params}  → {result | error}   (the ServerThreadWorld methods)
deliver    endbotd → plugin   {to, lines[], view?}                   chat lines and an optional form view
permission endbotd → plugin   {playerId, allowed}                    control attachment for one player
```

Why one persistent connection instead of today's connect-per-request: endbotd must call the plugin (world reads,
deliveries), and the order of presence reports and commands on one stream is what makes revision checks race-free
(P4). Why the plugin dials out: no new listener inside BDS, and BDS restarts reconnect on their own.

Bounds as today's runtime protocol: loopback only (both ends reject other addresses), a 1 MiB frame limit, the
token sent in `hello` and compared in constant time, per-request deadlines, explicit errors, token never logged.
A protocol or registry-hash mismatch refuses the link and `endbot doctor` reports it; mixed versions are never run.

World calls are answered on the server thread through the existing `ServerThreadBridge`; the link's reader thread
never touches Endstone objects. endbotd keeps the per-call deadline below its own command deadline so a stuck tick
fails one call, not the worker.

### P3. Shared code moves into one pure package

A new package, `endbot_core` under `core/` (S4), holds everything that today is pure plugin code. endbotd
runs it; the plugin imports only what it needs to render: the message catalog (`i18n`), the operation registry and
the form builders that turn Outcome data into screens. Endstone imports stay confined to the plugin package. Both
are installed into the same private Python, so there is one copy of the code and the registry hash always matches
inside one app version.

Web and crypto dependencies that BOT-021 adds are imported only by endbotd. They are installed in the same
environment but never loaded into BDS.

### P4. Authorization state moves to endbotd; presence replaces in-memory revisions

- **Grant revision is persisted** per XUID in the controller policy (policy file version 2). Today it lives in
  plugin memory, which was enough while every run died with BDS; it is not once runs, and later triggers, outlive BDS
  restarts. A revoke bumps it; a regrant never revives work holding the old value.
- **Session revision comes from presence.** The plugin reports a full snapshot on every (re)connect with a new
  `epoch`, then `join`, `quit` and permission-relevant changes. endbotd derives each player's session revision from
  `(epoch, join counter)`. Because presence and commands share one ordered stream, a command can never be checked
  against presence older than the quit that preceded it.
- **Enrollment and binding** (first Xbox-authenticated join binds a GamerTag to its XUID) run in endbotd on the
  `join` report. endbotd answers with `permission` so the plugin can set the Endstone attachment that controls
  command visibility.
- **Fail closed.** A join while endbotd is down gets no new attachment; the snapshot after reconnect resolves it.
  Unknown presence (no plugin connected) means no player principal is online, so only runs, jobs and the console
  pass the online-independent layer.

The two-layer check from BOT-019 is unchanged; only its inputs move.

### P5. Command execution, jobs and mining loops run in endbotd

The ordered worker moves as is: one executor serializes commands, script operations and cleanup, so ordering stays
what players see today. Mining loop renewal also moves; its raycast becomes a `world` call
(`resolve_mining`) on the link, as one loopback round trip every 200 ms per active loop. The runtime lease already
bounds orphaned mining when endbotd dies. Measure the link's round trip under a Group-sized mining load in step 3;
if it is too slow, batch the loop raycasts into one world call per poll rather than moving loops back.

### P6. What happens when something stops

| Down | Effect | Recovery |
|---|---|---|
| runtime | Bot operations fail with a temporary-failure code, as today | supervisor restarts it, as today |
| endbotd | in-game commands refused with a reason; runs and jobs end; mining stops at the runtime lease | supervisor restarts it; the plugin reconnects and sends a snapshot; endbotd ends what the journal lists (below) |
| plugin link (BDS restarting or crashed) | world calls fail with a temporary-failure code; no player principal is online; runs keep running and see those failures | the plugin reconnects with a new epoch |
| BDS exits | as today: the supervisor stops runtime and endbotd and exits with BDS's code | — |

**Run journal.** endbotd writes `state/run/endbotd-runs.json` atomically with each run's id, owner, script name and
the input channels it owns (with UUID pins). After an unclean endbotd exit the next start stops those channels on the
runtime, logs "Script <name> (<run>) ended: Endbot service restarted" for each and, when the owner is online,
tells them in chat. Runs are never resumed: a Luau VM's state is not persisted, and replaying operations would act
twice.

BDS exit keeps today's whole-instance stop in BOT-020. Keeping endbotd alive while BDS is stopped (for the web's
status and script editing) is a supervisor policy change for BOT-021; P2, P4 and P6 already make endbotd survive a
missing plugin, so that change is small.

### P7. Stores move into `state/`, and both update directions carry them

| Today | After |
|---|---|
| `server/plugins/endbot/groups.json` | `state/groups.json` |
| `server/plugins/endbot/scripts.json` | `state/scripts.json` |
| `state/controller-policy.json` (v1) | `state/controller-policy.json` (v2, adds grant revisions) |
| `state/controllers.json` | unchanged, now written by endbotd |
| `server/plugins/endbot/scripts/*.luau` | `<instance>/scripts/*.luau` (S1) |
| plugin `config.toml` `[authorization] [limits] [scripting] [ui]` | `state/generated/endbotd.toml`, generated from `endbot.toml` |
| plugin `config.toml` `[runtime] token-file/host/port` | replaced by the endbotd link settings and `state/secrets/endbotd.token` |

- **Update** (`endbot update` to the first endbotd version): after the new doctor passes and before `app/current`
  switches, copy the plugin data files into `state/`, write `state/migrations.json` (`{"stores": 2}`), back the
  originals up under `backups/`, and leave the originals in place. The new version reads only `state/`.
- **Rollback** (`endbot update --rollback` from an endbotd version): the current CLI, which knows both layouts,
  writes `state/` back to the plugin data folder (policy v2 → v1 drops only the grant revisions, which the old
  version keeps in memory), resets the marker, then switches `app/current`. Without this step a rollback would
  silently lose every Group or script change made since the update.
- **Fresh setup** writes the new layout directly.
- Every migration step is idempotent and checked by `endbot doctor` (`stores` check: marker, both layouts, which one
  is authoritative).

### P8. Console and CLI controller management talk to endbotd

- `botcontrollers` typed on the BDS console is forwarded over the link as a console principal.
- `endbot controllers add|remove|list` connects to endbotd's loopback CLI endpoint with the endbotd token and gets
  the result directly. The `controller-requests/` file channel and the `botcontrollers request <id>` console path are
  removed once this works; correlation, replay protection and "result unknown" handling become ordinary request
  ids on one connection.
- While endbotd is down the CLI says so instead of timing out. `endbot controllers reset`, which edits
  `state/controllers.json` while the instance is stopped, stays an offline command.

### P9. Logs

endbotd writes its own log, which the supervisor collects as `state/run/logs/<start>/endbotd.log` and echoes with
an `[endbotd]` prefix. The Event log sink (audit lines, run logs, run ends) moves there. The BDS console keeps only
what the plugin itself does (link state, world-call failures). `scripts/raknet_spike.py` starts BDS and the runtime
itself and reads audit lines from the server log, so it must also start endbotd and read its log; that change lands
in the same step as the move.

## Settled points

Decided 2026-10-29 JST, following the recommendations of the draft.

- **S1. Operator script files move to `<instance>/scripts/`**, next to `endbot.toml`: they are operator content,
  so they belong neither in `state/` (managed by Endbot) nor in the server directory. The update moves the files once
  and leaves a `README.txt` in the old folder pointing to the new one; a rollback moves them back.
- **S2. BDS exit policy is unchanged in BOT-020**: the supervisor stops runtime and endbotd and exits with BDS's
  code. Keeping endbotd running while BDS is stopped is revisited in BOT-021.
- **S3. No in-process fallback.** Developers with a manual plugin install run endbotd themselves
  (`python -m endbotd --config ...`); the product plugin always uses the link. An in-process mode would keep two
  authorization paths alive. Tests use the in-process harness of step 2, which goes through the real protocol.
- **S4. The shared package is `endbot_core`, in `core/`.**

## Steps

Each step is about one PR, keeps main releasable and changes nothing players see. Steps 1–2 still run everything
inside BDS.

1. **Pure package.** Move the pure modules into the shared package; the plugin imports them. Move script-menu views
   out of `ScriptService` into Outcome data plus plugin-side screens (the BOT-019 step 5 rule, applied to
   `/botscript`). No behavior change.
2. **Link protocol with an in-process harness.** Define the link messages, a Python client and server, and the
   plugin-side adapter (presence, world calls, deliveries, permissions). Tests run core and adapter through the real
   protocol over a socket pair with fake Endstone objects. The product still calls the core in process.
3. **endbotd process.** Add the `endbotd` entry, the supervisor child with ready line and restarts, `endbotd.toml`
   and `endbotd.token` generation, the doctor checks, the run journal and the log move. The plugin becomes the link
   client; the runtime token leaves the plugin config. Grant revisions persist (policy v2). Measure the link round
   trip under mining load (P5).
4. **Store migration.** `state/` layout, update and rollback in both directions, `migrations.json`, doctor `stores`
   check, and the S1 move.
5. **CLI controller management through endbotd.** Remove the file request channel.
6. **Validation.** Live RakNet smoke on the locked pair (all existing `--scripting`, `--mining`, `--item-use` cases,
   plus: kill endbotd during a run and during `destroy continuous`, restart BDS's plugin link, update then roll back
   with Groups and scripts changed in between). Human Windows Client check: commands, forms, script menus and
   messages unchanged; a refused command while endbotd restarts; achievement history unaffected.

## Not in BOT-020

Web link, accounts and the Worker (BOT-021); roles, ownership and script declarations (BOT-024); event kinds,
watches and persistent triggers (BOT-022); keeping endbotd running while BDS is stopped; any new operation.
