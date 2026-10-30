# Operator contract

This describes Endbot 0.2.0. Published `v0.1.x` bundles predate the transport selection (opt-in, experimental RakNet);
see `RAKNET_VALIDATION.md` for its validation scope. The first-release record is in `release/FINAL_CHECKLIST.md`.

The goal: a plain BDS operator runs Endbot as one product. Endbot internally uses Python (patched Endstone + plugin)
and Node.js (headless Bedrock runtime), but the operator never installs Python or Node.js, never creates a venv, and
never runs pip or npm.

## 1. Directory layout

One instance directory, chosen at setup (`<instance>` below). Everything Endbot owns lives under it; the BDS server
directory may live inside it (fresh setup) or anywhere else (existing server).

```text
<instance>/
├─ endbot.toml               operator config (the only file operators edit)
├─ app/<version>/            replaceable application, one directory per installed version
│  ├─ python/                private CPython 3.12 with patched Endstone + endstone-endbot + the endbot CLI installed
│  ├─ node/                  private Node.js runtime
│  ├─ runtime/               Endbot runtime with production node_modules, prebuilt in CI
│  └─ runner/                native endbot-luau-runner[.exe] and trusted api.luau, prebuilt in CI
├─ app/current               text file naming the active <version> (no symlink privileges needed on Windows)
├─ state/                    persistent Endbot state, never replaced by update
│  ├─ secrets/               owner-private.pem, owner-public.pem, control.token, endbotd.token
│  ├─ profiles/              Bot profiles (<uuid>.json)
│  ├─ controllers.json       controller enrollment state (generated, see §3)
│  ├─ controller-policy.json console-managed grants/revocations (generated, see §3)
│  ├─ grant-revisions.json   grant revision per revoked controller identity (generated, see §3)
│  ├─ controller-roles.json  controller role per XUID (generated, see §3)
│  ├─ groups.json            Groups (written by endbotd)
│  ├─ scripts.json           stored scripts (written by endbotd)
│  ├─ migrations.json        store layout marker ({"stores": 2}, see §6a)
│  ├─ generated/             files derived from endbot.toml on every start (runtime JSON, endbotd.toml)
│  └─ run/                   PID files and logs
├─ scripts/                  the operator's <name>.luau script files (read by endbotd)
├─ backups/                  setup/update backups of operator-owned BDS files; stores-<direction>-<time>/ store copies
└─ endbot / endbot.cmd       launcher: execs app/<current>/python with the endbot CLI
```

Ownership rules:

- **App** (`app/`): created by setup/update, deletable and reinstallable. Holds no secrets and no state.
- **State** (`state/`): created on first setup, carried across updates. Holds the only secrets. Never world data.
- **Operator-owned BDS** (worlds, `server.properties`, `allowlist.json`, `permissions.json`, packs): Endbot only
  reads these, except for the explicit, backed-up, previewed changes in §6. Updates never touch worlds.

## 2. Configuration

`endbot.toml` is the single operator-edited file. Everything else Endbot needs is generated from it plus `state/`.

```toml
[server]
path = 'server'            # BDS directory, relative to <instance> or absolute (TOML literal string)
transport = 'nethernet'    # nethernet or raknet; omitted in old configs means nethernet

[controllers]
gamertags = ["ExampleTag"] # Xbox GamerTags allowed to control Bots; bound to XUID on first join (§3)

[runtime]
control-port = 19142       # loopback only
chunk-radius = 5           # requested Bot chunk radius, integer 2..32
hotbar-sync-interval-ticks = 4  # server-side hotbar reads, integer 1..1200
hotbar-sync-resend-ticks = 20   # unchanged corrections, integer 1..72000

[endbotd]
link-port = 19143          # loopback port where endbotd accepts the plugin link; must differ from control-port

[limits]
max-bots = 0               # Bots online or connecting; 0 = no separate Bot cap, N = cap
reserved-player-slots = 1  # server slots kept free for human players

[ui]
language = "auto"          # auto (each player's game language), en, or ja

[scripting]
enabled = false            # opt in to controller Luau scripts; restart to apply
```

`[scripting].enabled` is a boolean and defaults to false, including when the section is absent.
Bundles ship the runner, so enabling it requires no build or toolchain. On each start the CLI writes this setting
and the absolute path of the running app version's runner into the generated plugin config. An optional
`[scripting] runner = 'path'` overrides that path (relative paths resolve from the plugin data folder).
Manual plugin installations set `[scripting]` directly in their plugin config. See [SCRIPTING.md](SCRIPTING.md).
`[scripting] book-export` (default true) lets controllers write a script into the book and quill they hold
(`/botscript export`) to edit it there. No item is created; a book that already has writing is replaced only with
`export <name> overwrite`, which the menu confirms first. Set it to false if the plugin must never change a held
item. Importing from a book is always available while scripting is enabled.

`[limits]` keeps a controller who spawns many Bots from filling every server slot. `reserved-player-slots`
(default 1) is subtracted from the BDS `max-players`; `max-bots = 0` adds no separate Bot cap, and a positive value
caps online-or-connecting Bots. `spawn`, `resume` and `reconnect` are refused — per member for a Group or script — when
the Bot would take a reserved slot or exceed `max-bots`. Existing Bots are never despawned by the limit;
`max-bots = 0` with `reserved-player-slots = 0` restores no limit. `endbot doctor` prints the resulting budget and
warns when it cannot apply. Managed instances write this section into the plugin config on start; manual plugin
installs edit the same keys there.

`[ui].language` sets the language of player-facing replies, help and forms. `auto` (the default, also when omitted)
gives Japanese clients Japanese and every other client English; `en` or `ja` fixes one language for everyone. Command
words, the server console and logs stay English (see [COMMANDS.md](COMMANDS.md#reply-language)). Managed instances
generate it into the plugin config; manual plugin installs use the same key under `[ui]` there. It applies after a
server restart.

`[runtime].chunk-radius` defaults to 5 chunks when omitted (previously the protocol library requested 10).
It is the shared default for Bots without an override; restart the runtime after editing this file.
`/bot <name> radius <2-32>` overrides one Bot, including while online. `/bot <name> radius default` removes that override.
The preference is stored with the Bot's immutable profile and survives reconnect, rename, and runtime restart. It can
also be set while the registered Bot is offline. `/bot <name> radius` reports the requested value, its source, and the
last server-confirmed value (unconfirmed until BDS replies, or while offline).
Endbot requests that Bot's chosen radius at spawn, reconnect, and dimension changes. BDS can clamp the request.
This controls the Bot client's requested chunk range; it does not change BDS `view-distance` or `tick-distance` and does
not guarantee that chunks loaded by other players or server systems will be unloaded.
On the tested BDS baseline, a request for 3 chunks was raised to 5; the default follows that observed effective range.

`hotbar-sync-interval-ticks` controls how often the plugin reads each online Bot's nine hotbar slots on the server
thread. `hotbar-sync-resend-ticks` controls periodic unchanged-state corrections; changes are sent at the next read.
Both settings are shared across Bots, default to 4/20 when omitted, and apply after a restart. Managed instances
generate them into `state/generated/endbotd.toml` (`[plugin]`), and endbotd hands them to the plugin when it links.
Resends happen only at reads, so their spacing rounds up to a multiple of the read interval (and is never shorter than
one read interval). At 20 TPS, 4 ticks is about 0.2 seconds and 20 ticks about one second; low TPS increases wall time.

Longer read intervals reduce SDK/NBT/JSON work but delay recognizing pickups. Longer resend intervals reduce unchanged
packets but can delay correcting a predicted drop rejected by BDS, whose inventory then stayed unchanged. Neither
setting disables native inventory/equipment updates. Unchanged observations are suppressed between resends and block
palette lookups are cached; all nine slots and item NBT are still read on every scheduled scan. There is no automatic
load adjustment or per-Bot override. See [measured scope and limits](ITEM_USE_SYNC_VALIDATION.md).

Generated on every `endbot start` (overwritten, never hand-edited):

- `state/generated/endbot-runtime.json` — runtime config pointing at `state/secrets`, `state/profiles`, and the BDS
  port read from `server.properties`.
- `state/generated/endbotd.toml` — endbotd's config ([ENDBOTD.md](ENDBOTD.md)): authorization (pointing at
  `state/controllers.json` and the policy files), limits, UI language, scripting, the runtime it drives
  (`state/secrets/control.token`), its plugin link and `[stores]`: Groups and stored scripts in `state/`, script
  files in `<instance>/scripts/`.
- `<server>/plugins/endbot/config.toml` — plugin config with only the `[endbotd]` link settings (loopback host,
  `link-port`, `state/secrets/endbotd.token`). The plugin no longer receives the runtime token.
- `state/secrets/endbotd.token` — created once, like the runtime's control token; never printed or logged.
- The `[local-bot-auth]` table in `<server>/endstone.toml` — `enabled = true`, current issuer/audience defaults, and
  `public-key-file` pointing at `state/secrets/owner-public.pem`. Other `endstone.toml` tables are left as the
  operator wrote them.

Identifiers are always strings. Paths are written as TOML literal strings so Windows backslashes need no escaping.
`endbot doctor` rejects configs that violate either rule with a message naming the key.

`[server].transport` selects the runtime connection path and must match `server.properties` `transport`.
Fresh setup accepts `--transport raknet|nethernet` (default NetherNet) and writes both values. Existing-server
setup reads and records the BDS value when the option is omitted; an explicit mismatch refuses the plan without
editing BDS. To change an existing instance, stop it, change both transport values, then run `doctor` and `start`.
Generated JSON is never the place to select a transport. RakNet dials the configured IPv4 `server-port` on
loopback directly and requires a valid port; it omits advertisement-name fields and the UDP 7551 check.
NetherNet keeps the discovery and server-selection checks below. The bundled native RakNet addon is load-tested.

## 3. Controller enrollment

Operators name controllers by GamerTag. GamerTags are a bootstrap identifier, never the security key.

- `endbot.toml [controllers].gamertags` lists wanted controllers. Each entry without a binding is **pending**.
- When a player joins through normal Microsoft/Xbox authentication with a non-empty XUID and a name matching a pending
  GamerTag (case-insensitive), the plugin binds that GamerTag to the player's XUID and UUID in
  `state/controllers.json` and logs the binding to the server console.
- Authorization (`endbot.command.control`) is granted only to bound XUIDs. After binding, the GamerTag no longer
  matters: a GamerTag change keeps control; another account later taking the old GamerTag gets nothing.
- Players with an empty XUID (including every local Bot) can never bind.
- Removing a GamerTag from `endbot.toml` removes its bootstrap grant on the next start. Runtime grants have their own
  persistence described below. `endbot controllers reset <tag>` resets only the bootstrap binding while stopped;
  it does not change runtime grants or revocations.
- Joining at all requires passing the BDS allow-list (§4). `endbot setup --fresh` adds every `--controller` GamerTag to
  `<server>/allowlist.json`; on an adopted server with `allow-list=true` the operator must add each controller first
  (`endbot console allowlist add <GamerTag>` after `endbot start`, or by editing `allowlist.json` while the server is
  stopped). The `allowlist` doctor check (§7) reports any controller missing from it.
- The old `[authorization] allowed-uuids / allowed-xuids` lists remain accepted for normally authenticated players;
  runtime identity revocations override them. An empty/invalid XUID never grants control, including via a UUID entry.

Risk accepted: between setup and the owner's first join, a pending GamerTag can be claimed by whoever currently holds
that GamerTag. Pending entries are therefore shown by `endbot doctor`, and setup tells the operator to join once.

### Runtime grants and revocations

While `endbot start` is running, use another terminal:

```text
endbot controllers add "Player With Spaces"
endbot controllers add "Player With Spaces" --role scripter
endbot controllers remove "Player With Spaces"
endbot controllers list
```

`--role scripter` or `--role controller` sets the grant's role. A new grant without the option is a **controller**, and
an existing grant keeps its role unless the option is given. The console command takes the same option, for example
`botcontrollers add "Player With Spaces" --role controller`.

The CLI connects to endbotd's loopback endpoint (the `[endbotd] link-port`, authenticated with
`state/secrets/endbotd.token`), runs the change in endbotd as the server console and prints the result in that terminal.
Success exits with 0; a rejected change exits with 1. Changes are also recorded in `endbotd.log`. While endbotd is down
(the supervisor restarts it) the CLI says so at once instead of waiting. A timeout or a connection lost after the
request was sent reports an unknown outcome; inspect `controllers list` before retrying a mutation. Requests are not
automatically retried, and endbotd refuses a request id that was already used on the connection. The same
`botcontrollers add|remove|list` commands work directly on the BDS console. Only a real server console sender may
administer access; controllers and operator players cannot grant or revoke access. Bot operation applies to all Bots,
with no per-Bot ACL.

`controllers list` shows effective states (`allowed`, `revoked`, `pending`, `denied`, or `conditional`) and their reasons,
including runtime grants, bootstrap bindings and explicit identity revocations. Online authenticated players use the
same decision as the command handler. Unresolved legacy identity entries are conditional: their full identity/name
checks happen on an authenticated join. `endbot doctor` combines saved config, bindings, runtime policy and legacy
allowlists for the next start; `endbot doctor --live` queries the running endbotd when a supervisor is present. It labels
the source so an un-reloaded TOML edit is not confused with current server access. Corrupt saved policy fails doctor.

An online authenticated target is bound by XUID immediately. An unknown offline GamerTag is pending until its first
normal Microsoft/Xbox-authenticated join. An offline already-bound GamerTag keeps its existing XUID when regranted.
Removing an offline unknown name fails; use a known bound name or `remove xuid:<id>` / `remove uuid:<id>` for legacy
identity entries. Name matching is case-insensitive; spaces may be quoted. Joining still requires the normal BDS
allow-list, which controller commands do not edit. Doctor's allow-list check covers bootstrap configuration;
inspect runtime entries with `controllers list` and allow-list those human accounts separately when required.

The plugin atomically saves overrides in `state/controller-policy.json` (`controller-policy-file` for manual installs).
It is the sole writer; the CLI does not rewrite configuration or live player state. Keep this file with the private
instance state and include it in backups. Missing policy means no overrides; corrupt policy prevents plugin loading.
Explicit identity revocations override bootstrap bindings and legacy UUID/XUID allowlists, survive name changes and
restart, and prevent a rejoin from restoring access. A pending GamerTag never enrolls a revoked XUID/UUID, even if that
account renames itself to the pending name; run `add` while that account is online to clear its revocation. Runtime grants
survive edits to the bootstrap list; use runtime `remove` to revoke them. Do not edit policy files while BDS runs.

Each revoke also bumps that identity's grant revision in `state/grant-revisions.json`, beside the policy file, written
atomically by the plugin. Work captured under an earlier revision stays refused after a regrant, including across
restarts. The plugin reads the file only at start-up; a missing file counts every revision as 0, which is safe because
no earlier work is running then. A corrupt file prevents plugin loading; with Endbot stopped, deleting it is a safe
reset. Versions before this file ignore it.

Changes update online players' control permission and command visibility immediately and recheck pending commands on
the server thread. A revoke/regrant cycle also invalidates commands queued under the earlier grant and ends
continuous `destroy` loops and script runs started under it. An operation
already started by the command worker may finish; revocation does not undo dispatched operations or stop existing Bot
movement/actions. Use `/bot <name> stop` from the console if those actions must stop too. No operator or vanilla
command permission is granted.

## 4. Local Bots and the BDS allow-list

Humans keep vanilla allow-list behaviour. A Bot accepted by the local-bot trust path needs no `allowlist.json` entry.

Observed on the 0.1.x baseline (patched Endstone `0.11.12+endbot.1`, BDS 1.26.51.1): with `allow-list=true` and
an empty `allowlist.json`, a Bot accepted by the local-bot path joins, and nothing is written to `allowlist.json`. The
authentication result the existing hook returns (a local identity with `is_local` set and no XUID) is already exempt,
so no additional Endstone core hook is needed. Logins that are not accepted by the local-bot path take the stock BDS validator and allow-list unchanged.

Release gates for this behaviour: a non-allowlisted human is still rejected with `allow-list=true`; an allowlisted
human joins; a Bot with a wrong issuer, wrong owner signature, or with local-bot-auth disabled is rejected; the accepted
Bot has member (not operator) permissions.

## 5. Server trust and transport selection

Decided: no server identity pin. BDS generates a new NetherNet DTLS identity on every start, so rc.1's persistent
trust-on-first-use pin (`bds-nethernet.pin`) failed closed after every BDS restart, and upstream `bedrock-protocol`
3.60.x / `nethernet` 1.1.x expose no hook to verify the identity. The runtime instead refuses any non-loopback BDS host,
and the local-bot token (owner-signed, audience-bound, short-lived, single-use `jti`, bound to the Bot's `cpk`) keeps a
same-host impostor from turning a captured token into a real login. The full rationale lives in `docs/SECURITY.md`
("Runtime → BDS connection"). `endbot start` therefore needs no per-start trust state.

NetherNet server selection. Bots find BDS through LAN discovery on UDP 7551, and the advertisement carries no port, so
other NetherNet hosts on the machine (a Minecraft client with a world open to LAN, another BDS) answer as well. The
runtime connects only to the host advertising this instance's `server.properties` `server-name` and world display
name (`worlds/<level-name>/level.dat` `LevelName`, falling back to `server.properties` `level-name` before the
world exists; written into the generated runtime config on every start); otherwise the Bot stays disconnected with
a `lastError` naming the advertisements it saw. The advertised display name can differ from the world directory
name, so renaming a world to match is never required. Only one program can own UDP 7551: when another one holds
it, BDS cannot answer discovery at all, so `endbot start` checks the port before starting anything and refuses with
that explanation. Use distinct `server-name` values (or world display names) when several Endbot instances share
a machine. RakNet skips this discovery and connects only to the configured loopback address/port, with ping and
advertised-port following disabled. Both paths use the same local-bot authentication and host-isolation boundary.

## 5a. Process lifecycle

`endbot start`:

1. Load `endbot.toml`; regenerate `state/generated/*` and the plugin/Endstone config (§2).
2. Run the doctor checks that can fail fast (§7). Refuse to start on any safety invariant violation.
3. Check `<server>/version.txt` equals the locked BDS version. On mismatch, refuse and point at `endbot update`
   (never let Endstone update BDS implicitly during start).
4. Start the runtime, wait for its `endbot_runtime_ready` line.
5. Start endbotd (`python -m endbotd --config state/generated/endbotd.toml` from the same `app/<version>/python`),
   wait for its `endbotd_ready` line. endbotd owns authorization, Groups, scripting and Bot command execution; the
   plugin inside BDS links to it and refuses every Endbot command ("Endbot service is restarting") while it is down.
6. Start Endstone/BDS (Endstone's own `python -m endstone -s <server>` entry, non-interactive, through
   `endbot_cli.endstone_entry`, which only repoints the relocated interpreter's `LIBDIR` on Linux), record PIDs, stdout/stderr logs, and exit
   codes under `state/run/`.
7. Desired-online Bots reconnect through the runtime's existing lifecycle.

`endbot start` stays in the foreground and supervises all three children. Lines typed on its stdin and lines queued in
`state/run/console.request` (appended by `endbot console`, one line per request, forwarded to the BDS console and
truncated by the supervisor) reach the BDS console. `endbot stop` writes `state/run/stop.request` and waits (default
120 s) for the supervisor recorded in `state/run/supervisor.pid` to exit; Ctrl+C triggers the same stop sequence
locally. `endbot stop` and `endbot console` require a live supervisor, and `endbot controllers add|remove|list` a
live supervisor and endbotd;
`endbot controllers reset` requires it to be stopped. Stale supervisor PIDs are diagnosed/cleaned as appropriate.

Stop sequence: `stop` on the BDS console (up to 60 s), then endbotd (its stdin is closed; it ends its script runs,
Group work and mining loops, up to 20 s), then a graceful runtime `shutdown` over the control port (up to 15 s). A
timeout escalates to killing that process tree and is reported as an UNCLEAN stop with exit code 1. Bots keep their
desired-online state and resume on the next start. Per-start logs live in `state/run/logs/<start>/` (`runtime.log`,
`endbotd.log`, `server.log`; the last 10 starts are kept) and the latest exit codes in `state/run/last-exit.json`.
The supervisor echoes runtime and endbotd lines with `[runtime]` and `[endbotd]` prefixes. Audit lines, script run
logs and run ends are written by endbotd, so they appear in `endbotd.log`, not in the BDS console or `server.log`.

Supervision policy: when BDS exits on its own, endbotd and the runtime are stopped gracefully and `endbot start`
exits with BDS's exit code; BDS is never auto-restarted. When the runtime or endbotd exits unexpectedly while BDS
runs, the supervisor restarts it with exponential backoff (1 s, 2 s, 4 s ... capped at 30 s); a replacement that
does not become ready counts as another failure, and after 5 failures of one child within a 5-minute window it stops
BDS and exits non-zero. While endbotd restarts, the plugin keeps the control permissions it last applied and
reconnects on its own; endbotd's script runs and mining loops end with it (mining stops at the runtime lease).

## 6. Setup

`endbot setup` has two paths and is always dry-run first: it prints every planned change (files to create or modify
with a short reason, backups to take, and whether BDS will be downloaded or overwritten) and applies nothing until the
operator confirms (`--apply` is the confirmation; the CLI never prompts). Setup refuses to run when `endbot.toml`
already exists or when a supervisor is running.

**Fresh BDS.** Creates `<instance>/server`, lets Endstone download the locked BDS through its normal acquisition path
(Endbot never bundles BDS), applies the safety defaults (`online-mode=true`, `allow-cheats=false`, no experiments)
plus the selected transport (`--transport raknet|nethernet`; omitted means NetherNet for fresh setup),
creates `state/`, and writes `endbot.toml` with the given controller GamerTags. It also adds every `--controller`
GamerTag to `<server>/allowlist.json` right after the BDS acquisition — a fresh BDS enables `allow-list=true` with an
empty list and would otherwise reject the controllers on join. Each entry uses the format `allowlist add <name>` writes
on the console, `{"ignoresPlayerLimit": false, "name": "<GamerTag>"}` with no `xuid` (BDS fills it on the first join);
every existing entry and its order is preserved, the write is atomic, and a name already listed (case-insensitively) is
not added again. A `allowlist.json` that is not a JSON list of entry objects FAILs the plan and is never overwritten.
The world is not created here — BDS
creates it on the first `endbot start`, which is also what generates the owner keys and control token (setup's doctor
report marks those two checks as expected-not-yet-present).

**Existing BDS.** Points `[server].path` at the existing directory. Before any change:

- Detect the BDS version. Endstone treats a directory without `version.txt` as older than supported and, when
  confirmed, re-downloads the server binary and the vanilla `behavior_packs/`, `resource_packs/`, and `definitions/`
  over the existing ones (worlds, `server.properties` values, `allowlist.json`, `permissions.json` are kept). A BDS
  newer than the locked version is refused.
- Back up `server.properties`, `allowlist.json`, `permissions.json`, `endstone.toml` if present,
  `packetlimitconfig.json` if present, the server executable, and the pack directories that Endstone may overwrite
  into `backups/<UTC timestamp>/`, with a `manifest.json` listing every backed-up file's SHA-256. Worlds are not
  copied by default (size); setup requires the operator to confirm they have a world backup
  (`--i-have-a-world-backup`), or to pass `--backup-worlds`.
- Verify the safety invariants on the existing `server.properties` and world, plus the runtime transport
  requirement. An existing server's `server.properties` is checked, never silently flipped
  (`online-mode=false` / `allow-cheats=true` / missing or unsupported `transport` FAIL the plan). The existing BDS
  transport is kept by default; an explicit `--transport` must match it,
  and a world that already has creative, cheat, or experiment history FAILs the plan; Endbot does not change world
  flags. Unknown experiment keys are reported as warnings.
- Never edit `allowlist.json`. While `allow-list=true`, the plan prints for each controller GamerTag missing from the
  allow-list how to fix it: `endbot console allowlist add <GamerTag>` after `endbot start` (or add it to
  `allowlist.json` while the server is stopped).
- List the planned changes (BDS binary update if needed, `endstone.toml` `[local-bot-auth]`, `plugins/endbot/` — the
  latter two are generated on the first start), then apply only on confirmation.

After `--apply`, both paths run `endbot doctor` (non-live) and print the report plus the next steps (`endbot doctor`,
`endbot start`, and joining once with each controller GamerTag to bind it).

## 6a. Update

`endbot update <artifact>` installs a new `app/<version>` next to the current one, runs its doctor against the
existing `state/` and server, switches `app/current`, and keeps the previous version for rollback. The artifact is a
platform bundle archive (`.zip` on Windows, `.tar.gz` on Linux) holding `app/<newversion>/` and the `endbot` /
`endbot.cmd` launcher at its root; only those are extracted — `state/`, `endbot.toml`, the server directory, and the
current application are never touched, and the launcher is replaced from the archive when the switch succeeds. The
archive is verified against `--sums PATH` (or `SHA256SUMS` beside the archive when present); a mismatch refuses the
update. If the new version locks a different BDS version (its doctor reports a `bds-version` FAIL), it performs the §6
backup and preview before letting Endstone update BDS; any other doctor FAIL keeps the old version current and is
reported. `endbot update --rollback` switches `app/current` back to the most recent previous version directory present
in `app/`. Updates refuse to run while a supervisor is running.

Store layout ([ENDBOTD.md](ENDBOTD.md) P7). From the first endbotd version, Groups and stored scripts live in
`state/groups.json` and `state/scripts.json`, and script files in `<instance>/scripts/`; `state/migrations.json`
(`{"stores": 2}`) marks that layout as authoritative. Before it, they lived in `<server>/plugins/endbot/`
(`groups.json`, `scripts.json`, `scripts/*.luau`). Moving between the layouts:

- An update from a version before endbotd is driven by that version's CLI, so the new version moves the stores on
  its first `endbot start` (or `endbot setup`): it backs up both layouts under `backups/stores-update-<time>/`,
  copies the data files into `state/` and leaves the originals in place, moves the script files to
  `<instance>/scripts/` with a `README.txt` left in the old folder, and writes the marker last. Later updates do the
  same right before `app/current` switches.
- `endbot update --rollback` first writes `state/groups.json` and `state/scripts.json` back to the plugin data
  folder, moves the script files back to `<server>/plugins/endbot/scripts/`, removes the `README.txt` and sets the
  marker to `{"stores": 1}` (backups under `backups/stores-rollback-<time>/`), then switches `app/current`. Group
  and script changes made since the update are therefore kept.
- Every step is idempotent: an interrupted move reports the cause, the old layout stays authoritative until the
  marker is written, and running the same command again finishes it. `endbot doctor` reports the outcome (`stores`).

Updating from 0.1.0: 0.1.1 doctor also requires `transport=nethernet` in `server.properties` (#36) and FAILs a world
directory that exists without `level.dat` (#37). An instance missing either keeps 0.1.0 current and the update
reports the FAIL; add `transport=nethernet` (BDS only accepts NetherNet in this release, so such an instance could not
take Bot or human connections anyway) or restore `level.dat`, then rerun `endbot update --force` (the refused attempt left `app/0.1.1` in place).

Updating from 0.2.0 to 0.2.1 also moves the locked BDS from `1.26.51.1` to `1.26.52.3`. The new version's doctor reports
the `bds-version` mismatch, so `endbot update` performs the §6 backup and preview and lets Endstone update BDS; the world
is opened by the newer BDS afterwards. Back up `worlds/` first: rolling back to 0.2.0 would put an older BDS in front of
a world the newer one has saved.

Updating from 0.2.1 to 0.3.0 keeps Endstone `0.11.13+endbot.1`, BDS `1.26.52.3` and protocol `2193`, so no BDS update
runs. An existing `endbot.toml` without `[ui]` keeps working and uses `language = "auto"`.

Updating from 0.3.0 to 0.3.1 changes only Endbot plugin/runtime/CLI code and versions: `destroy count <blocks>` is
added to continuous mining, and `for` and `count` can be combined. Endstone `0.11.13+endbot.1`, BDS `1.26.52.3`
and protocol `2193` remain pinned; no BDS/world format migration is needed.

Updating from 0.3.1 to 0.4.0 keeps the same pinned Endstone, BDS and protocol, so no BDS update runs. It adds shared
Groups, inventory commands, entity interaction and opt-in Luau scripts. An existing `endbot.toml` without
`[scripting]` or `[limits]` keeps working: scripting stays off, and `[limits]` defaults keep one server slot free for
players (`reserved-player-slots = 1`), so a server already full of Bots refuses new Bot sessions until a slot is
free. See [scripting](SCRIPTING.md) to enable scripts, and [installation](INSTALL.md) before rolling back to 0.3.x
(a fresh 0.4.0 setup writes sections that 0.3.x rejects).

## 7. Doctor

`endbot doctor` reports, without changing anything:

- `endbot.toml` parses; identifiers are strings; paths exist.
- Safety invariants: `online-mode=true`, `allow-cheats=false`, no experiments, no Beta APIs / GameTest, world history
  flags (existing `scripts/preflight.py` checks move here); plus the runtime transport requirement
  `transport` matching `[server].transport` (separate from safety; RakNet also requires a valid `server-port`).
- `endstone.toml [local-bot-auth]` enabled with a readable P-384 public key matching `state/secrets/owner-private.pem`.
- Control token present and readable. endbotd token present (`endbotd-token`); `state/generated/endbotd.toml` matches
  the ports in `endbot.toml` (`endbotd-config`); the plugin config links to endbotd's port with endbotd's token and
  no longer carries runtime settings (`plugin-port`). `endbotd-link` reads the latest `endbotd.log`: a plugin link
  refused for a protocol or operation-registry mismatch is a FAIL (mixed Endbot versions). A missing endbotd token
  or a plugin config written before endbotd is a WARN, since `endbot start` regenerates both.
- Stores (`stores`): PASS when `state/migrations.json` makes `state/` authoritative; WARN while the plugin data
  folder still is (the next `start` migrates) or when script files were left in the old folder; FAIL for an
  unreadable marker or one written by a newer Endbot. Both layouts' contents are listed.
- `<server>/version.txt` matches the lock; installed Endstone package version matches the lock.
- Controllers: bootstrap bindings and pending entries; `controllers-effective` combines saved config/policy/legacy
  entries with decision reasons, or queries the running endbotd with `--live` and a live supervisor. Legacy entries
  lacking a resolved authenticated identity are conditional. Invalid policy is FAIL, and so is an invalid
  `grant-revisions.json` (`grant-revisions`).
- Allow-list: when `allow-list` is not `true`, PASS (controllers are not filtered); when it is, PASS while every
  configured controller GamerTag is in `allowlist.json` (case-insensitive name match, or a bound controller's XUID in
  an entry's `xuid`), otherwise WARN naming each missing GamerTag and the fix (`endbot console allowlist add
  <GamerTag>` while the server runs, or an `allowlist.json` edit while it is stopped). An unreadable or malformed
  `allowlist.json` WARNs only — BDS treats it as its own file. The check never writes.
- Operators: WARN when `permissions.json` lists an `operator` entry or `default-player-permission-level=operator`,
  because the locked Endstone lets operators run cheat commands without disabling achievements (`SECURITY.md`);
  PASS otherwise. An unreadable `permissions.json` WARNs only. The check never writes.
- When running: runtime reachable over the control port, BDS process alive, each desired-online Bot's state and last
  error.

## 8. Release artifacts

Per platform (`linux-x86_64`, `windows-x86_64`), built only in CI:

- `endbot-<version>-<platform>.zip|tar.gz`: private CPython 3.12 with the patched Endstone wheel, `endstone-endbot`,
  and the `endbot` CLI preinstalled; private Node.js; `runtime/` with production `node_modules`;
  `runner/endbot-luau-runner[.exe]` with `api.luau`; launcher; licenses
  and third-party notices for every bundled component.
- `compatibility-manifest.json` and `SHA256SUMS` covering every file above.

The standalone wheels and runtime tarball stay available for developers but are not the operator path.

Release-candidate jobs build the runner with `node runner/build.mjs --out build/runner-stage`, run
`node --test` in `runner/`, and require `--runner-dir build/runner-stage` on `scripts/build_bundle.py`.
The builder checks the binary, sidecar, both MIT notices and Linux executable permission before copying them.
The notices go to `licenses/LUAU_LICENSE.txt` and `licenses/NLOHMANN_JSON_LICENSE.txt`.
Bundle self-tests smoke-run `params{}` without BDS. Local bundle builds may omit `--runner-dir`; those bundles
have no runner and cannot enable scripting without installing one. For such local bundles only,
`scripts/test_bundle.py --allow-missing-runner` skips an absent runner directory.
