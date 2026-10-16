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
│  └─ runtime/               Endbot runtime with production node_modules, prebuilt in CI
├─ app/current               text file naming the active <version> (no symlink privileges needed on Windows)
├─ state/                    persistent Endbot state, never replaced by update
│  ├─ secrets/               owner-private.pem, owner-public.pem, control.token
│  ├─ profiles/              Bot profiles (<uuid>.json)
│  ├─ controllers.json       controller enrollment state (generated, see §3)
│  ├─ controller-policy.json console-managed grants/revocations (generated, see §3)
│  ├─ generated/             files derived from endbot.toml on every start (runtime JSON, plugin config)
│  └─ run/                   PID files and logs
├─ backups/                  setup/update backups of operator-owned BDS files
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

[ui]
language = "auto"          # auto (each player's game language), en, or ja
```

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
Both settings are shared across Bots, default to 4/20 when omitted, and apply after a server restart. Managed instances
generate them into the plugin config; manual plugin installs use the same keys under `[runtime]` there.
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
- `<server>/plugins/endbot/config.toml` — plugin config pointing at `state/secrets/control.token` and
  `state/controllers.json`.
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
endbot controllers remove "Player With Spaces"
endbot controllers list
```

The CLI waits for the plugin's execution result and prints it in that terminal. Success exits with 0; a rejected change
exits with 1. Results also appear in the active BDS console/log. A timeout or interrupted connection reports an unknown
outcome; inspect `controllers list` before retrying a mutation. Requests are not automatically retried. The same
`botcontrollers add|remove|list` commands work directly on the BDS console. Only a real server console sender may
administer access; controllers and operator players cannot grant or revoke access. Bot operation applies to all Bots,
with no per-Bot ACL. Restart the supervisor/BDS after deploying this version to enable the result channel.

`controllers list` shows effective states (`allowed`, `revoked`, `pending`, `denied`, or `conditional`) and their reasons,
including runtime grants, bootstrap bindings and explicit identity revocations. Online authenticated players use the
same decision as the command handler. Unresolved legacy identity entries are conditional: their full identity/name
checks happen on an authenticated join. `endbot doctor` combines saved config, bindings, runtime policy and legacy
allowlists for the next start; `endbot doctor --live` queries the running plugin when a supervisor is present. It labels
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

Changes update online players' control permission and command visibility immediately and recheck pending commands on
the server thread. A revoke/regrant cycle also invalidates commands queued under the earlier grant. An operation
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
5. Start Endstone/BDS (Endstone's own `python -m endstone -s <server>` entry, non-interactive, through
   `endbot_cli.endstone_entry`, which only repoints the relocated interpreter's `LIBDIR` on Linux), record PIDs, stdout/stderr logs, and exit
   codes under `state/run/`.
6. Desired-online Bots reconnect through the runtime's existing lifecycle.

`endbot start` stays in the foreground and supervises both children. Lines typed on its stdin and lines queued in
`state/run/console.request` (appended by `endbot console`, one line per request, forwarded to the BDS console and
truncated by the supervisor) reach the BDS console. `endbot stop` writes `state/run/stop.request` and waits (default
120 s) for the supervisor recorded in `state/run/supervisor.pid` to exit; Ctrl+C triggers the same stop sequence
locally. `endbot stop`, `endbot console`, and `endbot controllers add|remove|list` require a live supervisor;
`endbot controllers reset` requires it to be stopped. Stale supervisor PIDs are diagnosed/cleaned as appropriate.

Stop sequence: `stop` on the BDS console (up to 60 s), then a graceful runtime `shutdown` over the control port
(up to 15 s). A timeout escalates to killing that process tree and is reported as an UNCLEAN stop with exit code 1.
Bots keep their desired-online state and resume on the next start. Per-start logs live in `state/run/logs/<start>/`
(`runtime.log`, `server.log`; the last 10 starts are kept) and the latest exit codes in `state/run/last-exit.json`.

Supervision policy: when BDS exits on its own, the runtime is stopped gracefully and `endbot start` exits with BDS's
exit code; BDS is never auto-restarted. When the runtime exits unexpectedly while BDS runs, the supervisor restarts
it with exponential backoff (1 s, 2 s, 4 s ... capped at 30 s); after 5 failures within a 5-minute window it stops
BDS and exits non-zero.

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

Updating from 0.1.0: 0.1.1 doctor also requires `transport=nethernet` in `server.properties` (#36) and FAILs a world
directory that exists without `level.dat` (#37). An instance missing either keeps 0.1.0 current and the update
reports the FAIL; add `transport=nethernet` (BDS only accepts NetherNet in this release, so such an instance could not
take Bot or human connections anyway) or restore `level.dat`, then rerun `endbot update --force` (the refused attempt left `app/0.1.1` in place).

Updating from 0.2.0 to 0.2.1 also moves the locked BDS from `1.26.51.1` to `1.26.52.3`. The new version's doctor reports
the `bds-version` mismatch, so `endbot update` performs the §6 backup and preview and lets Endstone update BDS; the world
is opened by the newer BDS afterwards. Back up `worlds/` first: rolling back to 0.2.0 would put an older BDS in front of
a world the newer one has saved.

## 7. Doctor

`endbot doctor` reports, without changing anything:

- `endbot.toml` parses; identifiers are strings; paths exist.
- Safety invariants: `online-mode=true`, `allow-cheats=false`, no experiments, no Beta APIs / GameTest, world history
  flags (existing `scripts/preflight.py` checks move here); plus the runtime transport requirement
  `transport` matching `[server].transport` (separate from safety; RakNet also requires a valid `server-port`).
- `endstone.toml [local-bot-auth]` enabled with a readable P-384 public key matching `state/secrets/owner-private.pem`.
- Control token present and readable; runtime and plugin agree on port.
- `<server>/version.txt` matches the lock; installed Endstone package version matches the lock.
- Controllers: bootstrap bindings and pending entries; `controllers-effective` combines saved config/policy/legacy
  entries with decision reasons, or queries the running plugin with `--live` and a live supervisor. Legacy entries
  lacking a resolved authenticated identity are conditional. Invalid policy is FAIL.
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
  and the `endbot` CLI preinstalled; private Node.js; `runtime/` with production `node_modules`; launcher; licenses
  and third-party notices for every bundled component.
- `compatibility-manifest.json` and `SHA256SUMS` covering every file above.

The standalone wheels and runtime tarball stay available for developers but are not the operator path.
