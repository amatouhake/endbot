# Operating Endbot

How to run an Endbot server day to day, and the reference for its files and settings. To get a server running from a
release bundle first, see [INSTALL.md](INSTALL.md).

Endbot runs as one product. It uses a private Python (patched Endstone and the Endbot plugin) and a private Node.js
(the Bot runtime), but you never install Python or Node.js, create a venv, or run pip or npm. The transport is
NetherNet only: BDS 1.26.60 refuses players on RakNet.

How the processes work inside is in [ARCHITECTURE.md](ARCHITECTURE.md#operator-cli-and-supervisor-internals) and
[ENDBOTD.md](ENDBOTD.md).

**Contents**

- [Start and stop](#start-and-stop)
  - [Start](#start)
  - [Stop](#stop)
  - [Send a line to the BDS console](#send-a-line-to-the-bds-console)
  - [When a process exits](#when-a-process-exits)
  - [With the web enabled](#with-the-web-enabled)
  - [Logs](#logs)
- [Granting access](#granting-access)
  - [Controllers in endbot.toml](#controllers-in-endbottoml)
  - [Runtime grants and revocations](#runtime-grants-and-revocations)
  - [What a grant change does](#what-a-grant-change-does)
  - [Policy files](#policy-files)
  - [Web accounts](#web-accounts)
- [The web](#the-web)
- [Configuration](#configuration)
  - [Example endbot.toml](#example-endbottoml)
  - [Settings](#settings)
  - [Bot limits](#bot-limits)
  - [Chunk radius](#chunk-radius)
  - [Hotbar sync](#hotbar-sync)
  - [Block watches and chat events](#block-watches-and-chat-events)
  - [Generated files](#generated-files)
- [Updating and rolling back](#updating-and-rolling-back)
  - [Update](#update)
  - [Roll back](#roll-back)
  - [The web after an update or rollback](#the-web-after-an-update-or-rollback)
  - [Store layout moves](#store-layout-moves)
  - [Version notes](#version-notes)
- [Checking health](#checking-health)
- [Reference](#reference)
  - [Directory layout](#directory-layout)
  - [What is replaced and what is kept](#what-is-replaced-and-what-is-kept)
  - [Backups](#backups)
  - [NetherNet signalling](#nethernet-signalling)
  - [Server trust](#server-trust)
  - [Local Bots and the BDS allow-list](#local-bots-and-the-bds-allow-list)
  - [Release files](#release-files)

<a id="5a-process-lifecycle"></a>

## Start and stop

### Start

```text
endbot start
endbot start --exit-with-bds
```

`endbot start` runs in the foreground and supervises three children: the runtime, endbotd and Endstone/BDS. Run the
other `endbot` commands from another terminal.

What it does, in order:

1. Loads `endbot.toml` and refuses if a supervisor is already running (a stale `state/run/supervisor.pid` is removed
   with a warning).
2. Moves the Group and script stores into `state/` if they are still in the old layout
   ([store layout moves](#store-layout-moves)).
3. Regenerates every [generated file](#generated-files).
4. Runs the doctor checks (without `--live`) and refuses on any FAIL. Two exceptions are printed as WARN instead:
   - a missing control token or owner key on the very first start (the runtime creates them during this start);
   - a FAIL of the `web` or `web-trusted-site` check: that part of the web stays off and the server starts anyway.
5. Checks the network ports:
   - with `nethernet-signalling = 'lan'`, refuses when another program holds UDP 7551;
   - on Linux, waits up to 75 s for TCP `server-port` to leave TIME-WAIT ("waiting for TCP <port> ...") and refuses
     if another program really holds it. Windows skips this wait.
6. Starts the runtime, then endbotd, then Endstone/BDS, each after the previous one reports ready.
7. Bots that were online when the server last stopped reconnect.

The `bds-version` doctor check fails when `<server>/version.txt` is not the locked BDS version. Start then refuses
and points at `endbot update`; it never lets Endstone update BDS on its own.

### Stop

```text
endbot stop
endbot stop --timeout <seconds>
```

`endbot stop` asks the running supervisor to stop and waits for it to exit (default 120 s). Ctrl+C in the
`endbot start` terminal runs the same stop sequence.

The stop sequence:

| Step | Process | Waits up to |
| --- | --- | --- |
| 1 | `stop` on the BDS console | 60 s |
| 2 | endbotd (ends its script runs, Group work and mining loops) | 20 s |
| 3 | Runtime, through a graceful `shutdown` | 15 s |

- A step that times out kills that process tree. The stop is then reported as UNCLEAN and `endbot stop` exits with 1.
- Bots keep their desired-online state and reconnect on the next start.

Which commands need a running supervisor:

| Command | Needs |
| --- | --- |
| `endbot stop`, `endbot console` | A running supervisor |
| `endbot controllers add`, `remove`, `list` | A running supervisor and endbotd |
| `endbot web revoke` | A running supervisor and endbotd |
| `endbot controllers reset`, `endbot setup`, `endbot update` | No supervisor running |

### Send a line to the BDS console

```text
endbot console <line>
```

Sends one line to the BDS console of the running server, for example `endbot console allowlist add <GamerTag>`.
Lines typed in the `endbot start` terminal reach the BDS console as well.

### When a process exits

| What exits | What the supervisor does |
| --- | --- |
| BDS, on its own | Stops endbotd and the runtime gracefully and exits with BDS's exit code. BDS is never restarted automatically. With the web enabled, see [below](#with-the-web-enabled). |
| The runtime or endbotd, while BDS runs | Restarts it after 1 s, 2 s, 4 s ... (at most 30 s). A replacement that does not become ready counts as another failure. |
| The same child, 5 times within 5 minutes | Stops BDS and exits non-zero. |

While endbotd restarts:

- every Endbot command is refused with "Endbot service is restarting";
- the plugin keeps the control permissions it last applied and reconnects on its own;
- script runs and mining loops end (mining stops at the runtime lease).

### With the web enabled

With `[web] enabled = true`, BDS exiting (a console `stop` or a crash) stops only the runtime. endbotd keeps running
so the web stays up, and the supervisor logs BDS's exit code. Two console lines then apply, typed in the
`endbot start` terminal or sent with `endbot console`:

| Line | Effect |
| --- | --- |
| `start` | Starts the runtime and then BDS, with the checks a first start runs. Stays stopped if one fails. |
| `exit` | Stops endbotd and exits with BDS's last exit code. |

- Other lines are refused while BDS is stopped.
- `endbot stop` stops everything, as always.
- `start` keeps the configuration of the running supervisor. After editing `endbot.toml`, use `endbot stop` and
  `endbot start`.
- `endbot start --exit-with-bds` restores the whole-instance stop, for a service manager that restarts on exit.

### Logs

| File | Contents |
| --- | --- |
| `state/run/logs/<start>/runtime.log` | Runtime output |
| `state/run/logs/<start>/endbotd.log` | endbotd output: audit lines, script run logs, run ends, controller bindings |
| `state/run/logs/<start>/server.log` | Endstone/BDS output |
| `state/run/last-exit.json` | The latest exit codes |

- The last 10 starts are kept.
- The supervisor echoes runtime and endbotd lines in its terminal with `[runtime]` and `[endbotd]` prefixes.
- Audit lines and script logs are not in the BDS console or `server.log`.

<a id="3-controller-enrollment"></a>

## Granting access

Controllers are the players allowed to control Bots. You name them by Xbox GamerTag, either in `endbot.toml` (the
bootstrap list) or at runtime with `endbot controllers`. What each role may do is in [PERMISSIONS.md](PERMISSIONS.md).

A controller must also pass the BDS allow-list to join at all. Controller commands never edit `allowlist.json`.

### Controllers in endbot.toml

```toml
[controllers]
gamertags = ["ExampleTag"]
```

1. Each listed GamerTag starts as **pending**.
2. When a player joins through normal Microsoft/Xbox authentication with a non-empty XUID and a name matching a pending
   GamerTag (case-insensitive), endbotd binds that GamerTag to the player's XUID and UUID in `state/controllers.json`
   and logs the binding in `endbotd.log`.
3. From then on control follows the XUID, not the name. A GamerTag change keeps control; another account that later
   takes the old GamerTag gets nothing.

- Players with an empty XUID, including every local Bot, can never bind.
- Removing a GamerTag from the list removes its bootstrap grant on the next start. Runtime grants are separate.
- `endbot controllers reset <GamerTag>` returns one bootstrap binding to pending. It needs the instance stopped and
  does not change runtime grants or revocations.
- **Bind early.** Until the owner's first join, whoever currently holds a pending GamerTag can claim it. `endbot doctor`
  shows pending entries, and setup tells you to join once.

The allow-list:

- `endbot setup --fresh` adds every `--controller` GamerTag to `<server>/allowlist.json`.
- On an adopted server with `allow-list=true`, add each controller yourself: `endbot console allowlist add <GamerTag>`
  while the server runs, or edit `allowlist.json` while it is stopped.
- The `allowlist` doctor check reports bootstrap controllers missing from it. Allow-list runtime grants separately.

Old configurations: the `[authorization] allowed-uuids` and `allowed-xuids` lists of early versions are still accepted
for normally authenticated players. Runtime identity revocations override them, and an empty or invalid XUID never
grants control, including through a UUID entry. The `controllers-legacy` doctor check counts them.

### Runtime grants and revocations

While `endbot start` is running, from another terminal:

```text
endbot controllers add "Player With Spaces"
endbot controllers add "Player With Spaces" --role scripter
endbot controllers remove "Player With Spaces"
endbot controllers remove xuid:<id>
endbot controllers list
```

| Command | Effect |
| --- | --- |
| `add <GamerTag>` | Grants control. A new grant is a **controller**; an existing grant keeps its role. |
| `add <GamerTag> --role scripter\|controller` | Grants control with that role, or changes the role of an existing grant |
| `remove <GamerTag>` | Revokes control |
| `remove xuid:<id>`, `remove uuid:<id>` | Revokes a legacy identity entry |
| `list` | Shows each identity's effective state and reason |

The same commands work on the BDS console as `botcontrollers add|remove|list`, with the same `--role` option. Only a
real server console may administer access; controllers and operator players cannot grant or revoke.

Results:

- The CLI runs the change in endbotd as the server console and prints the result. Success exits with 0, a rejected
  change with 1, and every change is recorded in `endbotd.log`.
- While endbotd is down (the supervisor restarts it), the CLI says so at once instead of waiting.
- A timeout, or a connection lost after the request was sent, means the outcome is unknown. Check `controllers list`
  before retrying. Requests are never retried automatically.

How targets are matched:

- An online authenticated player is bound by XUID at once.
- An unknown offline GamerTag stays pending until its first normal Microsoft/Xbox-authenticated join.
- An offline GamerTag that is already bound keeps its XUID when granted again.
- Removing an offline unknown name fails. Use a bound name, or `xuid:<id>` / `uuid:<id>` for legacy entries.
- Names match case-insensitively; quote names with spaces.

`controllers list` states:

| State | Meaning |
| --- | --- |
| `allowed` | May control Bots |
| `revoked` | Explicitly revoked |
| `pending` | Waiting for the first authenticated join |
| `denied` | Not allowed |
| `conditional` | A legacy entry whose identity and name are checked on an authenticated join |

`endbot doctor` combines the saved configuration, bindings, runtime policy and legacy lists to predict the next start.
`endbot doctor --live` asks the running endbotd instead. Each result names its source, so an edited but not yet
reloaded `endbot.toml` is not confused with current access.

### What a grant change does

- Online players' control permission and command visibility change at once, and pending commands are rechecked.
- A revoke followed by a regrant invalidates commands queued under the earlier grant and ends continuous `destroy`
  loops and script runs started under it.
- An operation the command worker already started may finish. A revoke does not undo dispatched operations or stop
  Bot movement; run `/bot <name> stop` from the console if that must stop too.
- A revoke ends that player's web sessions at once (see [web accounts](#web-accounts)).
- No operator or vanilla command permission is ever granted.
- Which Bots a controller may operate follows ownership and visibility (`[permissions]`, see
  [PERMISSIONS.md](PERMISSIONS.md)).

Revocations are sticky:

- An explicit identity revocation overrides bootstrap bindings and legacy UUID/XUID lists, survives name changes and
  restarts, and is not undone by rejoining.
- A pending GamerTag never enrolls a revoked XUID or UUID, even if that account renames itself to the pending name.
  Run `add` while that account is online to clear its revocation.
- Runtime grants survive edits to the bootstrap list. Use `remove` to revoke them.

### Policy files

endbotd is the only writer of these files and writes each one atomically. Keep them with the private instance state,
include them in backups, and never edit them while the server runs.

| File | Contents | Missing | Corrupt |
| --- | --- | --- | --- |
| `state/controller-policy.json` | Runtime grants and revocations | No overrides | endbotd does not start |
| `state/grant-revisions.json` | A revision per revoked identity; work captured under an earlier revision stays refused after a regrant, also across restarts | Every revision is 0 | endbotd does not start; with Endbot stopped, deleting it is a safe reset |
| `state/controller-roles.json` | Role per XUID; a grant with no entry (for example from before 0.5.0) is a scripter | Every controller is a scripter | endbotd does not start |
| `state/ownership.json` | Owner and visibility per Bot and Group | Everything is unowned and shared | endbotd does not start |

The `grant-revisions` file is read only when endbotd starts. Versions that predate it ignore it.

### Web accounts

```text
endbot web revoke <GamerTag|xuid:id>
endbot web revoke --site-sessions
```

- `endbot web revoke <GamerTag|xuid:id>` deletes one player's web account, passkeys and sessions while `endbot start`
  runs. The controller grant stays, so the player can link again; to keep someone out, remove the grant.
- Removing a grant ends that player's web sessions at once and keeps the account and passkeys for a later regrant.
- `--site-sessions` ends every session and removes every passkey that came through the optional trusted site, for a
  suspected key exposure ([WEB.md P12](WEB.md#p12-trusted-external-site-optional-off-by-default)).
- The store is `state/web/` (owner-only); endbotd is its only writer
  ([WEB.md P6](WEB.md#p6-revocation-reaches-the-web-at-once)).

## The web

The [deployment guide](WEB_DEPLOY.md) is the walkthrough. The commands:

```text
endbot web deploy --account-id <id>
endbot web deploy --account-id <id> --domain <host> --zone-id <id>
endbot web rotate-key
endbot doctor --live
```

- `endbot web deploy` uploads the prebuilt Worker to your own Cloudflare account through its API; no Node or Wrangler
  is needed. It writes `[web]` into `endbot.toml` and waits for the link.
- It creates the link key once, in `state/secrets/web-link.key`, and installs only its public half.
  `endbot web rotate-key` replaces it.
- The Cloudflare API token comes from a hidden prompt or `CLOUDFLARE_API_TOKEN`. It is never an argument and never
  stored.
- `--domain <host> --zone-id <id>` uses a custom domain. A later deploy without `--domain` keeps it.
- Moving to another origin makes every passkey unusable, so it needs `--confirm-domain-change` and then deletes all
  passkeys and sessions.
- `endbot doctor --live` reports the link. A web problem is a FAIL in `endbot doctor` but only a warning for
  `endbot start`: the broken part stays off and the server runs.

<a id="2-configuration"></a>

## Configuration

`endbot.toml` in the instance directory is the only file you edit. Everything else Endbot needs is
[generated](#generated-files) from it and `state/` on every `endbot start`.

Rules:

- `[server]` and `[controllers]` are required; every other section and key is optional.
- Unknown sections and keys are refused, with the known keys listed.
- Identifiers are strings: quote GamerTags, even all-digit ones.
- Write paths as TOML literal strings (`'C:\endbot\server'`), so Windows backslashes need no escaping.
- `endbot start` and `endbot doctor` refuse an invalid file with a message naming the key and the fix.
- Changes apply on the next `endbot start` (the **Applies** column). With the web enabled, a console `start` after BDS
  stopped keeps the running configuration; use `endbot stop` and `endbot start`.

### Example endbot.toml

```toml
[server]
path = 'server'                  # BDS directory, relative to the instance or absolute
transport = 'nethernet'
nethernet-signalling = 'direct'

[controllers]
gamertags = ["ExampleTag"]

[runtime]
control-port = 19142
chunk-radius = 5
hotbar-sync-interval-ticks = 4
hotbar-sync-resend-ticks = 20

[endbotd]
link-port = 19143

[limits]
max-bots = 0
max-bots-per-controller = 0
reserved-player-slots = 1

[ui]
language = "auto"

[scripting]
enabled = false

[permissions]
bots-default = "shared"
groups-default = "shared"

[events]
max-watched-blocks = 2048
chat = false
```

### Settings

**`[server]`** (required)

| Setting | Default | Effect | Applies |
| --- | --- | --- | --- |
| `path` | none (required) | The BDS directory, relative to the instance or absolute | Next start |
| `transport` | `nethernet` | Must be `nethernet` and match `transport` in `server.properties`. `raknet` is an error: BDS 1.26.60 refuses players on RakNet. | Next start |
| `nethernet-signalling` | `direct` | How Bots reach BDS: `direct` (HTTP on `server-port`) or `lan` (UDP 7551 discovery). See [NetherNet signalling](#nethernet-signalling). Neither changes `server.properties`. | Next start |

**`[controllers]`** (required)

| Setting | Default | Effect | Applies |
| --- | --- | --- | --- |
| `gamertags` | none (required; `[]` allowed) | GamerTags allowed to control Bots, bound to an XUID on first join ([granting access](#controllers-in-endbottoml)) | Next start |

**`[runtime]`**

| Setting | Default | Effect | Applies |
| --- | --- | --- | --- |
| `control-port` | `19142` | Loopback TCP port of the runtime's control endpoint (1 to 65535) | Next start |
| `chunk-radius` | `5` | Default chunk radius a Bot requests, 2 to 32 ([chunk radius](#chunk-radius)) | Next start; `/bot <name> radius` changes one Bot at once |
| `hotbar-sync-interval-ticks` | `4` | How often the plugin reads each online Bot's hotbar, 1 to 1200 ([hotbar sync](#hotbar-sync)) | Next start |
| `hotbar-sync-resend-ticks` | `20` | How often an unchanged hotbar is resent as a correction, 1 to 72000 | Next start |

**`[endbotd]`**

| Setting | Default | Effect | Applies |
| --- | --- | --- | --- |
| `link-port` | `19143` | Loopback TCP port where endbotd accepts the plugin link and the CLI. Must differ from `control-port`. | Next start |

**`[limits]`** ([Bot limits](#bot-limits))

| Setting | Default | Effect | Applies |
| --- | --- | --- | --- |
| `max-bots` | `0` | Cap on Bots online or connecting; `0` means no separate cap | Next start |
| `max-bots-per-controller` | `0` | Bots one controller may own; `0` means no cap | Next start |
| `reserved-player-slots` | `1` | Server slots kept free for human players | Next start |
| `max-watches-per-controller` | `32` | Watches across one controller's script runs, 0 to 1024; `0` turns watches off ([SCRIPTING.md](SCRIPTING.md)) | Next start |
| `max-triggers-per-controller` | `8` | Armed triggers per owner, 0 to 256; `0` turns triggers off | Next start |
| `max-watched-blocks-per-controller` | `1024` | Watched positions across one controller's watches and triggers, 0 to 16384; `0` leaves only the server-wide `[events] max-watched-blocks` | Next start |

**`[ui]`**

| Setting | Default | Effect | Applies |
| --- | --- | --- | --- |
| `language` | `auto` | Language of player-facing replies, help and forms. `auto` gives Japanese clients Japanese and every other client English; `en` or `ja` fixes one language for everyone. Command words, the server console and logs stay English ([COMMANDS.md](COMMANDS.md#reply-language)). | Next start |

**`[scripting]`** (setup and every option: [SCRIPTING_SETUP.md](SCRIPTING_SETUP.md))

| Setting | Default | Effect | Applies |
| --- | --- | --- | --- |
| `enabled` | `false` | Lets controllers run Luau scripts ([SCRIPTING.md](SCRIPTING.md)). Bundles ship the runner, so no build is needed. | Next start |
| `runner` | the running version's bundled runner | Path to another runner executable. A relative path resolves from `state/generated/`. | Next start |
| `book-export` | `true` | Lets `/botscript export` write a script into the book and quill a controller holds. No item is created; a book that already has writing is replaced only with `export <name> overwrite`, which the menu confirms first. Importing from a book always works while scripting is on. | Next start |
| `os-confinement` | `true` | See [SCRIPTING_SETUP.md](SCRIPTING_SETUP.md) | Next start |
| `process-memory-bytes` | `268435456` | `0`, or at least `100663296`. See SCRIPTING_SETUP.md. | Next start |
| `cpu-seconds` | `60` | See SCRIPTING_SETUP.md | Next start |
| `chat-logs` | `true` | See SCRIPTING_SETUP.md | Next start |
| `ui-pack-editor` | `false` | See [UI_PACK.md](UI_PACK.md) | Next start |
| `replies` | `failures` | `all`, `failures` or `none`. See SCRIPTING_SETUP.md. | Next start |

**`[permissions]`** ([PERMISSIONS.md](PERMISSIONS.md))

| Setting | Default | Effect | Applies |
| --- | --- | --- | --- |
| `bots-default` | `shared` | Visibility of a Bot a controller creates: `shared` or `private` (visible and usable only by its owner) | Items created after the next start |
| `groups-default` | `shared` | The same for Groups | Items created after the next start |

- Existing items keep their visibility.
- The owner is recorded in `state/ownership.json`. An item with no entry there is unowned and shared, and Bots and
  Groups created from the console stay unowned.
- Someone else's private Bot or Group is refused (a private Group resolves as not found).
- `forget`, `rename` and `visibility` on a Bot stay with its owner and the console, even when it is shared.

**`[events]`** ([block watches and chat events](#block-watches-and-chat-events))

| Setting | Default | Effect | Applies |
| --- | --- | --- | --- |
| `max-watched-blocks` | `2048` | Positions watched for block changes, all watches and triggers together, 0 to 16384; `0` turns block watches off | Next start |
| `chat` | `false` | Lets scripts that declare `observe.chat` watch public chat (`player.chat`) | Next start |

**`[web]`** ([WEB_DEPLOY.md](WEB_DEPLOY.md))

| Setting | Default | Effect | Applies |
| --- | --- | --- | --- |
| `enabled` | `false` | Turns the web on. Also keeps endbotd running when BDS exits ([with the web enabled](#with-the-web-enabled)). | Next start |
| `origin` | empty | `https://host`, normally written by `endbot web deploy`. With it set, `/botweb link` issues codes and the outbound link opens when `state/secrets/web-link.key` exists. | Next start |
| `display-name` | empty | Shown on the sign-in page, at most 64 printable characters. It is the one thing of yours Cloudflare keeps. | Next start |
| `account-id`, `worker-name` | empty | Written by `endbot web deploy`; not secret | Written by `endbot web deploy` |
| `[web.trusted-site]` | absent | The optional trusted site ([WEB.md P12](WEB.md#p12-trusted-external-site-optional-off-by-default)); `endbot doctor` checks it | Next start |

### Bot limits

`[limits]` keeps a controller who spawns many Bots from filling every server slot.

- `reserved-player-slots` is subtracted from the BDS `max-players`. `spawn`, `resume` and `reconnect` are refused when
  the Bot would take a reserved slot or go over `max-bots`, per member for a Group or a script.
- `max-bots-per-controller` refuses a new `spawn` by a controller who already owns that many Bots. Reconnecting an
  existing Bot and the console are never limited. The count comes from `state/ownership.json`, so `forget` frees a
  place.
- Existing Bots are never despawned by a limit.
- `max-bots = 0` with `reserved-player-slots = 0` removes the slot limit.

The `capacity` doctor check compares `[limits]` in `endbot.toml` with `max-players` in `server.properties`, prints the
resulting budget, and warns when it cannot apply: no Bot limit and no reserved slot, a reserve that leaves no slot for
a Bot, or a `max-bots` larger than the slots left after the reserve.

### Chunk radius

`[runtime] chunk-radius` is the radius every Bot requests unless it has its own. BDS can lower or raise the request.

```text
/bot <name> radius <2-32>
/bot <name> radius default
/bot <name> radius
```

- `/bot <name> radius <2-32>` overrides one Bot, also while it is online or offline. `default` removes the override.
- The override is stored with the Bot's profile and survives reconnect, rename and runtime restart.
- `/bot <name> radius` reports the requested value, its source and the last value BDS confirmed (unconfirmed until
  BDS replies, or while offline).
- Endbot requests the radius at spawn, reconnect and dimension changes.
- It does not change BDS `view-distance` or `tick-distance`, and does not unload chunks other players or server
  systems keep loaded.

### Hotbar sync

The plugin reads each online Bot's nine hotbar slots on the server thread. Changes are sent at the next read;
unchanged state is resent only as a periodic correction.

| Setting | At 20 TPS | Longer values |
| --- | --- | --- |
| `hotbar-sync-interval-ticks = 4` | About 0.2 s between reads | Less SDK, NBT and JSON work, but pickups are recognized later |
| `hotbar-sync-resend-ticks = 20` | About one correction per second | Fewer unchanged packets, but a predicted drop that BDS rejected is corrected later |

- Resends happen only at reads, so their spacing rounds up to a multiple of the read interval.
- Low TPS stretches the wall time.
- Both apply to all Bots; there is no per-Bot override and no automatic load adjustment.
- Neither disables the native inventory and equipment updates.
- All nine slots and their item NBT are read on every scan. Unchanged observations are suppressed between resends.

### Block watches and chat events

`[events] max-watched-blocks` bounds what `block.change` watches cost the server thread. Every watched position is
read once every 4 ticks, a quarter of them per tick. That cost about 1 ms per tick per 2000 positions on the PC that
measured it ([record](EVENT_MEASUREMENTS.md#block-changes)). A watch that would exceed the limit is refused with the
limit named.

`[events] chat` is off by default. On the locked Endstone the chat event comes before a behavior pack's `chatSend` mute
and BDS's chat filter run, so a message those block may still reach a script although no player saw it
([record](EVENT_MEASUREMENTS.md#presence-and-chat)). Leave it off on a server that mutes chat with a pack.

### Generated files

`endbot start` overwrites these on every start. Never edit them by hand.

| File | Contents |
| --- | --- |
| `state/generated/endbot-runtime.json` | Runtime configuration: `state/` as its data directory, the control token and owner keys in `state/secrets/`, the control port, chunk radius, transport and signalling, and the BDS `server-port` from `server.properties` |
| `state/generated/endbotd.toml` | endbotd's configuration ([ENDBOTD.md](ENDBOTD.md)): its link, the runtime it drives, authorization files, `[limits]`, `[permissions]`, `[events]`, `[ui]`, `[scripting]` (with the runner path), `[web]`, hotbar sync, and the stores (`state/` and `<instance>/scripts/`) |
| `<server>/plugins/endbot/config.toml` | Only how the plugin reaches endbotd: loopback host, `link-port` and `state/secrets/endbotd.token` |
| `[local-bot-auth]` in `<server>/endstone.toml` | `enabled = true`, the issuer and audience defaults, and `public-key-file` pointing at `state/secrets/owner-public.pem`. Other tables stay as you wrote them. |
| `state/secrets/endbotd.token` | Created once if missing; never printed or logged |

- With `direct` signalling, start refuses when `server.properties` has no valid `server-port`.
- With `lan` signalling, the runtime configuration also names the server and world to look for
  ([NetherNet signalling](#nethernet-signalling)).
- Generated files hold absolute paths, so a moved instance needs one start before `update` and `doctor` pass
  ([directory layout](#directory-layout)).
- Generated JSON is never the place to choose a transport.

## Updating and rolling back

### Update

```text
endbot update <archive>
endbot update <archive> --sums <path>
endbot update <archive> --backup-worlds
endbot update <archive> --force
```

The archive is the platform bundle from a release: `.zip` on Windows, `.tar.gz` on Linux. Stop the server first;
update refuses while a supervisor runs.

1. Verifies the archive against `--sums`, or against `SHA256SUMS` beside the archive when present. A mismatch refuses.
2. Extracts only `app/<new version>/` and the launcher. `state/`, `endbot.toml`, the server directory and the current
   application are never touched.
3. Runs the new version's doctor against the existing instance.
4. If the new version locks a different BDS (a `bds-version` FAIL), prints what Endstone will overwrite, takes a
   [backup](#backups), runs the new version's Endstone acquisition and runs its doctor again. There is no separate
   confirmation step, so back up `worlds/` first or pass `--backup-worlds`.
5. On any other doctor FAIL, keeps the old version current and reports the FAIL.
6. Moves the stores if needed ([store layout moves](#store-layout-moves)), switches `app/current` to the new version
   and replaces the launcher. The previous version stays in `app/`.

`--force` replaces an `app/<version>/` that an earlier, refused attempt already extracted.

### Roll back

```text
endbot update --rollback
```

Switches `app/current` back to the most recently modified previous version directory in `app/`, after writing the
stores back to the old layout ([store layout moves](#store-layout-moves)). It does not edit `endbot.toml`; see the
[version notes](#version-notes) for keys an older version refuses.

### The web after an update or rollback

With the web deployed, both directions then redeploy the Worker of the version now current, so the Worker and endbotd
stay matched ([WEB.md P9](WEB.md#p9-the-cloudflare-api-token-is-entered-by-the-operator-and-never-stored)).

- The token comes from `CLOUDFLARE_API_TOKEN` or a hidden prompt. Enter skips it; with no terminal and no variable it
  is skipped without asking.
- The app has already switched by then, so a skipped or failed deploy is a warning and the command still succeeds.
- The old Worker keeps serving. If its web protocol differs from the new endbotd's, the web is offline until
  `endbot web deploy` runs.
- Rolling back to a version without the web leaves the Worker in place with nothing connected.

### Store layout moves

Since 0.5.0 (the first version with endbotd), Groups, stored scripts and script files live in the instance:

| Before 0.5.0 | Since 0.5.0 |
| --- | --- |
| `<server>/plugins/endbot/groups.json` | `state/groups.json` |
| `<server>/plugins/endbot/scripts.json` | `state/scripts.json` |
| `<server>/plugins/endbot/scripts/*.luau` | `<instance>/scripts/*.luau` |

`state/migrations.json` (`{"stores": 2}`) marks the new layout as authoritative. Endbot moves the stores for you:

| When | What happens |
| --- | --- |
| Update from a version before 0.5.0 | That version's CLI cannot move them, so the new version does it on its first `endbot start` or `endbot setup` |
| Later updates | Right before `app/current` switches |
| Moving in | Backs up both layouts under `backups/stores-update-<time>/`, copies the data files into `state/` (originals stay), moves the script files with a `README.txt` left in the old folder, and writes the marker last |
| `endbot update --rollback` | Writes the stores back to the plugin data folder, moves the script files back, removes the `README.txt` and sets the marker to `{"stores": 1}` (backups under `backups/stores-rollback-<time>/`), then switches. Changes made since the update are kept. |

Every step is idempotent. An interrupted move reports the cause, the old layout stays authoritative until the marker
is written, and running the same command again finishes it. The `stores` doctor check reports the outcome.

### Version notes

What each update needs beyond `endbot update`. A BDS change means `endbot update` moves BDS with a [backup](#backups);
back up `worlds/` first, because rolling back would put an older BDS in front of a world the newer one has saved.

**0.6.0 to 0.7.0**

- Endstone `0.11.15+endbot.1`, BDS `1.26.60.6`, protocol `2225` stay; no BDS update.
- `endbot.toml` needs no change: `[events]`, `[web]` and the new `[limits]` keys (`max-watches-per-controller`,
  `max-triggers-per-controller`, `max-watched-blocks-per-controller`) are optional.
- The stores are already in `state/` and stay there. 0.7.0 adds `state/triggers.json` with the first trigger and
  `state/web/` once the web is deployed. The web stays off until `endbot web deploy` ([guide](WEB_DEPLOY.md)).
- Before rolling back to 0.6.0, remove any `[events]` and `[web]` sections (including `[web.trusted-site]`) and those
  three `[limits]` keys: 0.6.0 refuses keys it does not know. It does not read `state/triggers.json` or `state/web/`,
  so no trigger runs and nobody can sign in to the web until 0.7.0 is current again.

**0.5.0 to 0.6.0**

- Endstone `0.11.15+endbot.1`, BDS `1.26.60.6`, protocol `2225` stay; no BDS update and no `endbot.toml` change.
- Areas replace `blocks ... radius` and `blocks ... at`: use `within <r>` and `within <r> of <x> <y> <z>`. The old
  spellings are refused with a message naming the new ones ([areas](COMMANDS.md#areas-within-and-except)).
- `look` answers after BDS has the rotation, `destroy` reaches 6 blocks, and `blocks` sees through leaves.
- The 0.4.0 script spellings `share <name>` and `private <name>` are refused in favour of
  `visibility <name> shared|private`. A script that calls `blocks(type, x, y, z)` passes an area value instead.

**0.4.0 to 0.5.0**

- Moves to Endstone `0.11.15+endbot.1`, BDS `1.26.60.6` and protocol `2225`, which Minecraft 26.60 clients need.
  `endbot update` moves BDS with a backup.
- First version with endbotd: the stores move into `state/` ([store layout moves](#store-layout-moves)).
- BDS 1.26.60 refuses players on RakNet. An instance set up with `--transport raknet` keeps 0.4.0 current and the
  update reports the FAIL. Stop it, set `transport=nethernet` in `server.properties` and `[server] transport =
  "nethernet"` in `endbot.toml`, then run `endbot update --force`.

**0.3.1 to 0.4.0**

- Same Endstone, BDS and protocol; no BDS update.
- Adds shared Groups, inventory commands, entity interaction and opt-in Luau scripts ([scripting](SCRIPTING.md)).
- An `endbot.toml` without `[scripting]` or `[limits]` keeps working: scripting stays off, and `[limits]` keeps one
  server slot free for players, so a server already full of Bots refuses new Bot sessions until a slot is free.
- A fresh 0.4.0 setup writes `[scripting]` and `[limits]`, which 0.3.x refuses as unknown. Before rolling back to
  0.3.x, stop the instance and remove both sections, then roll back and start. Stored scripts and script files are
  kept and come back with 0.4.0.

**0.3.0 to 0.3.1**

- Only Endbot code changes. Endstone `0.11.13+endbot.1`, BDS `1.26.52.3` and protocol `2193` stay; no BDS or world
  migration.
- `destroy count <blocks>` is added to continuous mining, and `for` and `count` can be combined.

**0.2.1 to 0.3.0**

- Endstone `0.11.13+endbot.1`, BDS `1.26.52.3` and protocol `2193` stay; no BDS update.
- An `endbot.toml` without `[ui]` keeps working and uses `language = "auto"`.

**0.2.0 to 0.2.1**

- Moves the locked BDS from `1.26.51.1` to `1.26.52.3`; `endbot update` moves BDS with a backup.

**0.1.0 to 0.1.1**

- The 0.1.1 doctor requires `transport=nethernet` in `server.properties` and FAILs a world directory without
  `level.dat`. An instance missing either keeps 0.1.0 current and the update reports the FAIL.
- Add `transport=nethernet` or restore `level.dat`, then run `endbot update --force` (the refused attempt left
  `app/0.1.1` in place).

## Checking health

```text
endbot doctor
endbot doctor --live
```

`endbot doctor` reports the instance's health and never changes anything. It exits with 1 when any check FAILs.
The safety invariants it enforces are `online-mode=true`, `allow-cheats=false`, and a world with no experiments (Beta
APIs and GameTest included).
Before the first `endbot start`, a missing control token or owner key is a WARN. `--live` also asks the running
processes.

Checks, in the order they run:

| Check | What it reports |
| --- | --- |
| `config` | `endbot.toml` loads and is valid |
| `server-dir` | The BDS directory exists |
| `server-properties` | `online-mode=true` and `allow-cheats=false` (safety), and `transport=nethernet` matching `[server] transport` |
| `world` | The world must have no experiments (Beta APIs and GameTest included), no creative history and no cheats. FAIL for achievement-disabling world history in `level.dat` (`hasBeenLoadedInCreative`, `commandsEnabled`, `experiments_ever_used`, `saved_with_toggled_experiments`), an unreadable `level.dat`, or a world directory without one. WARN for `cheatsEnabled`, a non-survival game type or unknown experiment keys. Skipped before the world exists. |
| `capacity` | The Bot slot budget from `[limits]` in `endbot.toml` and `max-players`; WARN when the limits cannot apply ([Bot limits](#bot-limits)) |
| `bds-version` | `<server>/version.txt` matches the locked BDS |
| `local-bot-auth` | `[local-bot-auth]` in `endstone.toml` is enabled with a readable P-384 public key matching `state/secrets/owner-private.pem` |
| `plugin-port` | The plugin config links to endbotd's port with endbotd's token and carries no runtime settings. A config written before endbotd is a WARN. |
| `controllers-legacy` | Legacy `[authorization]` entries, with a pointer to `[controllers] gamertags` |
| `endstone-version` | The installed Endstone package matches the lock |
| `control-token` | The runtime control token is present and readable |
| `endbotd-token` | The endbotd token is present (missing is a WARN; start creates it) |
| `endbotd-config` | `state/generated/endbotd.toml` matches the ports in `endbot.toml` |
| `web` | The web configuration and link key; with `--live`, whether the web link is up |
| `web-trusted-site` | The optional trusted site's configuration |
| `endbotd-link` | The latest `endbotd.log`: a plugin link refused for a protocol or operation-registry mismatch (mixed Endbot versions) is a FAIL |
| `controllers` | Bootstrap bindings and pending GamerTags |
| `controllers-effective` | Each identity's effective state and reason, from saved files, or from the running endbotd with `--live`. An invalid `controller-policy.json` is a FAIL. |
| `grant-revisions`, `controller-roles`, `ownership`, `triggers` | Each file is valid ([policy files](#policy-files)); a corrupt one is a FAIL because endbotd will not start |
| `stores` | PASS when `state/` is authoritative; WARN while the old layout still is (the next start moves it) or script files were left in the old folder; FAIL for an unreadable marker or one written by a newer Endbot |
| `allowlist` | PASS when `allow-list` is off, or when every bootstrap controller is in `allowlist.json` (by name, case-insensitive, or by a bound XUID). Otherwise WARN naming each missing GamerTag and the fix. An unreadable `allowlist.json` is only a WARN. |
| `operators` | WARN when `permissions.json` has an `operator` entry or `default-player-permission-level=operator`: the locked Endstone lets operators run cheat commands without disabling achievements ([SECURITY.md](SECURITY.md#operators-and-achievements)) |
| `runtime` | With `--live` only: the runtime answers on the control port |

## Reference

### Directory layout

Everything Endbot owns lives under one instance directory, chosen at setup. The BDS directory may be inside it (fresh
setup) or anywhere else (adopted server).

```text
<instance>/
├─ endbot.toml               your configuration (the only file you edit)
├─ endbot / endbot.cmd       launcher: runs the endbot CLI from app/<current>/python
├─ app/
│  ├─ current                text file naming the active <version>
│  └─ <version>/             one directory per installed version
│     ├─ python/             private CPython 3.12 with patched Endstone, the plugin and the endbot CLI
│     ├─ node/               private Node.js
│     ├─ runtime/            Endbot runtime with its production node_modules
│     ├─ runner/             endbot-luau-runner[.exe] and its trusted api.luau
│     └─ web/                the prebuilt web that endbot web deploy uploads
├─ licenses/                 licenses and third-party notices for every bundled component
├─ state/                    persistent Endbot state, never replaced by an update
│  ├─ secrets/               owner-private.pem, owner-public.pem, control.token, endbotd.token, web-link.key
│  ├─ profiles/              Bot profiles (<uuid>.json)
│  ├─ controllers.json       bootstrap controller bindings
│  ├─ controller-policy.json runtime grants and revocations
│  ├─ grant-revisions.json   grant revision per revoked identity
│  ├─ controller-roles.json  role per XUID
│  ├─ ownership.json         owner and visibility per Bot and Group
│  ├─ groups.json            Groups
│  ├─ scripts.json           stored scripts
│  ├─ triggers.json          triggers (from the first trigger)
│  ├─ migrations.json        store layout marker
│  ├─ web/                   web accounts, passkeys and sessions (once the web is deployed)
│  ├─ generated/             files generated from endbot.toml on every start
│  └─ run/                   PID files, request files and logs
├─ scripts/                  your <name>.luau script files
├─ backups/                  setup and update backups, store copies
└─ server/                   the BDS directory, after a fresh setup
```

- The `endbot` commands find the instance from `--instance`, then `$ENDBOT_INSTANCE`, then the launcher's directory,
  then the working directory.
- **The instance cannot be moved as it is.** Generated files hold absolute paths; for example the plugin config names
  `state/secrets/endbotd.token` by full path.
- After a copy or move, `endbot update` and `doctor` fail on `plugin-port` until one `endbot start` regenerates the
  file (deleting `<server>/plugins/endbot/config.toml` has the same effect). Restore backups at the original path,
  or start once and re-run `doctor` after moving.

### What is replaced and what is kept

| Part | Created by | On update | Notes |
| --- | --- | --- | --- |
| `app/` | Setup (from the bundle) and update | A new version is added; old ones stay for rollback | No secrets, no state; deletable and reinstallable |
| `state/` | First setup | Kept | Holds the only secrets; never world data |
| Operator-owned BDS files (worlds, `server.properties`, `allowlist.json`, `permissions.json`, packs) | You or BDS | Kept; worlds are never touched | Endbot only reads them, except the previewed, backed-up changes of setup and a BDS change on update, and the generated `[local-bot-auth]` table and plugin config |

### Backups

Setup of an existing server, and an update that changes BDS, copy the operator-owned BDS files that Endstone may
overwrite into `backups/<UTC timestamp>/`, with a `manifest.json` listing every file's SHA-256:

- `server.properties`, `allowlist.json`, `permissions.json`;
- `endstone.toml` and `packetlimitconfig.json`, when present;
- the server executable;
- the vanilla `behavior_packs/`, `resource_packs/` and `definitions/`.

Worlds are not copied unless you pass `--backup-worlds`. Store moves keep their own copies under
`backups/stores-update-<time>/` and `backups/stores-rollback-<time>/`.

### NetherNet signalling

`[server] nethernet-signalling` chooses how Bots find and reach BDS.

| | `direct` (default from 0.5.0) | `lan` |
| --- | --- | --- |
| How | HTTP on the loopback `server-port`, like a game client joining by address | LAN discovery on UDP 7551, as in 0.4.0 and earlier |
| Needs | A valid `server-port` in `server.properties` | UDP 7551 free on the machine |
| Several instances or a game client on one machine | Yes; each instance is selected by its own port | No; only one program can own UDP 7551 |
| Start checks | On Linux, waits for `server-port` to leave TIME-WAIT | Refuses when UDP 7551 is held |

**Direct.** Every join by address (a Bot's or a person's) leaves `server-port` in TCP TIME-WAIT for up to 60 s, because
BDS closes the connection first. On Linux the patched BDS cannot listen on the port again until then, so
`endbot start` waits for it, and refuses after 75 s when another program really holds the port. Windows binds through
TIME-WAIT and skips the wait.

**LAN.** The advertisement carries no port, so every NetherNet host on the machine answers, including a game client
with a world open to LAN or another BDS. The runtime therefore connects only to the host advertising this instance's
`server-name` and world display name; otherwise the Bot stays disconnected with a `lastError` naming the
advertisements it saw.

- The world display name is `LevelName` from `worlds/<level-name>/level.dat`, or `level-name` from `server.properties`
  before the world exists. It is read on every start.
- The display name may differ from the world directory name; you never need to rename a world.

Authentication, the loopback restriction and the Bot token are the same with both. How direct signalling works is in
[ARCHITECTURE.md](ARCHITECTURE.md#operator-cli-and-supervisor-internals).

### Server trust

There is no server identity pin, and `endbot start` keeps no per-start trust state. BDS generates a new NetherNet
identity on every start, so a pin would fail after every restart.

- The runtime refuses any BDS host that is not loopback.
- The local-bot token is owner-signed, audience-bound, short-lived, single-use and bound to the Bot's key, so a
  same-host impostor cannot turn a captured token into a real login.

The rationale is in [SECURITY.md](SECURITY.md#runtime--bds-connection).

### Local Bots and the BDS allow-list

- Humans keep the vanilla allow-list behaviour.
- A Bot accepted by the local-bot trust path needs no `allowlist.json` entry, and nothing is written to
  `allowlist.json` when it joins.
- Logins that the local-bot path does not accept go through the stock BDS validator and allow-list unchanged.
- An accepted Bot has member permissions, not operator.

What was checked on a real server is in [COMPATIBILITY.md](COMPATIBILITY.md#permissions-and-endbotd).

### Release files

Each release carries, built only in CI:

| File | Contents |
| --- | --- |
| `endbot-<version>-windows-x86_64.zip`, `endbot-<version>-linux-x86_64.tar.gz` | The platform bundle: one `endbot-<version>/` directory holding `app/<version>/` (private CPython 3.12 with the patched Endstone, `endstone-endbot` and the `endbot` CLI; private Node.js; the runtime with production `node_modules`; the Luau runner with `api.luau`; the prebuilt web), the launcher, and licenses and third-party notices for every bundled component |
| `endbot-ui-<pack version>.mcpack` | The optional client-side UI pack ([INSTALL.md](INSTALL.md#optional-ui-pack)) |
| `compatibility-manifest.json` | The Endbot, Endstone, BDS and protocol versions this release binds |
| `SHA256SUMS` | Checksums of every file |

- Every file also carries build provenance; [INSTALL.md](INSTALL.md#download-and-verify) shows how to check it.
- The release also carries the separate wheels, the runtime and configuration tarballs and `ENDSTONE_LICENSE`. They
  are for developers, not the operator path
  ([ARCHITECTURE.md](ARCHITECTURE.md#manual-install-from-release-candidate-artifacts)).
