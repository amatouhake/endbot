# endbotd

endbotd is the process that owns Endbot's policy and state outside BDS. It has shipped since 0.5.0. This document
describes the process split, the link protocol and the store layout, with the reason for each choice. Code comments
cite the decisions below by number (P1 to P9, S1 to S4). What was checked on a real server is in
[COMPATIBILITY.md](COMPATIBILITY.md#permissions-and-endbotd).

It follows from [Decision 1](DIRECTION.md#decision-1-endbot-is-a-distribution-and-its-centre-moves-out-of-bds) of the
product direction. The web ([WEB.md](WEB.md)), permissions ([PERMISSIONS.md](PERMISSIONS.md)) and triggers
([EVENTS.md](EVENTS.md)) build on it.

## Processes

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
  execution, jobs (mining loops and waits), the event log, the web link and triggers.
- **The plugin is a world adapter.** It registers the commands, resolves Endstone senders, reports presence,
  forwards command lines and form replies to endbotd, answers world calls on the server thread, renders forms and
  delivers chat. It holds no policy and no store.
- **The runtime keeps its own protocol**, and only endbotd (and the supervisor's `shutdown`) talks to it. The
  plugin does not hold the runtime token.

Start order: runtime → endbotd → BDS. endbotd needs the runtime to execute Bot operations, and the plugin needs
endbotd to accept commands. The supervisor waits for `endbotd_ready` the same way it waits for
`endbot_runtime_ready`.

## Decisions

### P1. Process split: three children under a thin supervisor

endbotd owns the instance's state, talks to the runtime and answers the CLI, as its own child process.

Rejected alternative: endbotd inside the supervisor process. It saves one process but couples the console, the stop
sequence and product logic in one interpreter; a crash or a hung lock in policy code would stop the instance. Refusing
in-game commands while endbotd is down also assumes it can be down while BDS runs.

Restart policy: like the runtime (1 s, 2 s, 4 s ... capped at 30 s; 5 failures in 5 minutes stop the instance).
While endbotd is down the plugin refuses every Endbot command with "Endbot service is restarting" and keeps the
last control-permission attachments, so command visibility does not flicker.

### P2. One persistent duplex link between plugin and endbotd

The plugin connects out to endbotd; the plugin never listens. Over that one connection both sides send requests and
replies, newline-delimited JSON, each with an id:

```text
hello      plugin → endbotd   {protocol, pluginVersion, registryHash, epoch, boot, firstLink}
           endbotd → plugin   {protocol, endbotdVersion, config: {language, hotbar...}}
presence   plugin → endbotd   snapshot | join | quit | locale   (see P4)
submit     plugin → endbotd   {root: bot|botgroup|botscript|botcontrollers, argv | operation, sender, guard?}
world      endbotd → plugin   {method, params}  → {result | error}   (the ServerThreadWorld methods)
deliver    endbotd → plugin   {to, lines[], view?}                   chat lines and an optional form view
permission endbotd → plugin   {playerId, allowed}                    control attachment for one player
```

Why one persistent connection instead of a connection per request: endbotd must call the plugin (world reads,
deliveries), and the order of presence reports and commands on one stream is what makes revision checks race-free
(P4). Why the plugin dials out: no new listener inside BDS, and BDS restarts reconnect on their own.

Bounds, as in the runtime protocol: loopback only (both ends reject other addresses), a 1 MiB frame limit, the
token sent in `hello` and compared in constant time, per-request deadlines, explicit errors, token never logged.
A protocol or registry-hash mismatch refuses the link and `endbot doctor` reports it; mixed versions are never run.

World calls are answered on the server thread through `ServerThreadBridge`; the link's reader thread
never touches Endstone objects. endbotd keeps the per-call deadline below its own command deadline so a stuck tick
fails one call, not the worker.

### P3. Shared code lives in one pure package

`endbot_core` under `core/` (S4) holds everything that needs no Endstone import. endbotd
runs it; the plugin imports only what it needs to render: the message catalog (`i18n`), the operation registry and
the form builders that turn Outcome data into screens. Endstone imports stay confined to the plugin package. Both
are installed into the same private Python, so there is one copy of the code and the registry hash always matches
inside one app version.

The web and crypto dependencies are imported only by endbotd. They are installed in the same
environment but never loaded into BDS.

### P4. Authorization state lives in endbotd; presence gives the session revision

- **Grant revision is persisted** per controller identity in its own file, `state/grant-revisions.json`, because runs and
  triggers outlive BDS restarts. A revoke bumps it; a regrant never revives work holding the old value. A separate file, not
  a version 2 of `controller-policy.json`: a pre-endbotd version rejects an unknown policy version, so a v2 policy
  would break a rollback, while an unknown file is simply ignored and the old version falls back to its in-memory
  revisions. A missing or reset file counts every revision as 0. That is safe because the file is read only at
  start-up, when no work from before can still be running.
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

The check itself has two layers, described in [PERMISSIONS.md](PERMISSIONS.md#two-layers-the-person-and-the-script).

### P5. Command execution, jobs and mining loops run in endbotd

One executor serializes commands, script operations and cleanup, so players see one order. Mining loops are renewed
by endbotd every 200 ms; their raycasts go over the link as one batched `world` call per poll
(`resolve_mining_batch`). The runtime lease bounds orphaned mining when endbotd dies.

### P6. What happens when something stops

| Down | Effect | Recovery |
|---|---|---|
| runtime | Bot operations fail with a temporary-failure code | supervisor restarts it |
| endbotd | in-game commands refused with a reason; runs and jobs end; mining stops at the runtime lease | supervisor restarts it; the plugin reconnects and sends a snapshot; endbotd ends what the journal lists (below) |
| plugin link (BDS restarting or crashed) | world calls fail with a temporary-failure code; no player principal is online; runs keep running and see those failures | the plugin reconnects with a new epoch |
| BDS exits | the supervisor stops runtime and endbotd and exits with BDS's code; with `[web] enabled`, endbotd and the runtime keep running ([WEB.md P10](WEB.md#p10-endbotd-keeps-running-while-bds-is-stopped)) | — |

**Run journal.** endbotd writes `state/run/endbotd-runs.json` atomically with each run's id, owner, script name and
the input channels it owns (with UUID pins). After an unclean endbotd exit the next start stops those channels on the
runtime, logs "Script <name> (<run>) ended: Endbot service restarted" for each and, when the owner is online,
tells them in chat. Runs are never resumed: a Luau VM's state is not persisted, and replaying operations would act
twice.

### P7. Stores live in `state/`, and both update directions carry them

| Before 0.5.0 | Since 0.5.0 |
|---|---|
| `server/plugins/endbot/groups.json` | `state/groups.json` |
| `server/plugins/endbot/scripts.json` | `state/scripts.json` |
| `state/controller-policy.json` (v1) | unchanged (v1) |
| grant revisions in plugin memory | `state/grant-revisions.json` (new, see P4) |
| `state/controllers.json` | unchanged, now written by endbotd |
| `server/plugins/endbot/scripts/*.luau` | `<instance>/scripts/*.luau` (S1) |
| plugin `config.toml` `[authorization] [limits] [scripting] [ui]` | `state/generated/endbotd.toml`, generated from `endbot.toml` |
| plugin `config.toml` `[runtime] token-file/host/port` | replaced by the endbotd link settings and `state/secrets/endbotd.token` |

- **Update** (`endbot update` to the first endbotd version): after the new doctor passes and before `app/current`
  switches, copy the plugin data files into `state/`, write `state/migrations.json` (`{"stores": 2}`), back the
  originals up under `backups/`, and leave the originals in place. The new version reads only `state/`.
- **Rollback** (`endbot update --rollback` from an endbotd version): the current CLI, which knows both layouts,
  writes the Groups and scripts in `state/` back to the plugin data folder and the script files back to the
  server's script folder, resets the marker, then switches `app/current`. The controller files need nothing: their
  format is unchanged, and the old version ignores `state/grant-revisions.json`. Without this step a rollback would
  silently lose every Group or script change made since the update.
- **Fresh setup** writes the new layout directly.
- The first update from a version before endbotd is driven by that
  version's CLI, which has no migration step, so the new version runs the same copy, backup and marker step on its
  first `endbot start` (before endbotd starts) or `endbot setup` when the marker is missing; the `stores` doctor check
  reports the pending migration as a WARN so the old CLI still switches.
- Every migration step is idempotent and checked by `endbot doctor` (`stores` check: marker, both layouts, which one
  is authoritative).

### P8. Console and CLI controller management talk to endbotd

- `botcontrollers` typed on the BDS console is forwarded over the link as a console principal.
- `endbot controllers add|remove|list` connects to endbotd's loopback CLI endpoint with the endbotd token and gets
  the result directly. Correlation, replay protection and "result unknown" handling are ordinary request ids on one
  connection.
- While endbotd is down the CLI says so instead of timing out. `endbot controllers reset`, which edits
  `state/controllers.json` while the instance is stopped, stays an offline command.

### P9. Logs

endbotd writes its own log, which the supervisor collects as `state/run/logs/<start>/endbotd.log` and echoes with
an `[endbotd]` prefix. The Event log sink (audit lines, run logs, run ends) writes there. The BDS console keeps only
what the plugin itself does (link state, world-call failures). `scripts/raknet_spike.py` starts endbotd as well as BDS
and the runtime, and reads audit lines from endbotd's log.

## Settled points

- **S1. Operator script files live in `<instance>/scripts/`**, next to `endbot.toml`: they are operator content,
  so they belong neither in `state/` (managed by Endbot) nor in the server directory. The update moves the files once
  and leaves a `README.txt` in the old folder pointing to the new one; a rollback moves them back.
- **S2. BDS exit stops the instance** unless the web is enabled: the supervisor stops runtime and endbotd and exits
  with BDS's code ([WEB.md P10](WEB.md#p10-endbotd-keeps-running-while-bds-is-stopped) is the exception).
- **S3. No in-process fallback.** Developers with a manual plugin install run endbotd themselves
  (`python -m endbotd --config ...`); the product plugin always uses the link. An in-process mode would keep two
  authorization paths alive. Tests use an in-process harness that goes through the real protocol.
- **S4. The shared package is `endbot_core`, in `core/`.**
