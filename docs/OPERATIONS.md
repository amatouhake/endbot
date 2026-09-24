# Operator contract (0.1.0 target)

Status: **target contract, being implemented** for 0.1.0 (see `release/FINAL_CHECKLIST.md`). Where this file and the
current code disagree, the code describes rc.1 behaviour and this file describes what 0.1.0 must do. Sections marked
*Decision pending* are not settled yet.

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
│  ├─ generated/             files derived from endbot.toml on every start (runtime JSON, plugin config)
│  └─ run/                   PID files, per-start server-trust state, logs
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

[controllers]
gamertags = ["ExampleTag"] # Xbox GamerTags allowed to control Bots; bound to XUID on first join (§3)

[runtime]
control-port = 19142       # loopback only
```

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

## 3. Controller enrollment

Operators name controllers by GamerTag. GamerTags are a bootstrap identifier, never the security key.

- `endbot.toml [controllers].gamertags` lists wanted controllers. Each entry without a binding is **pending**.
- When a player joins through normal Microsoft/Xbox authentication with a non-empty XUID and a name matching a pending
  GamerTag (case-insensitive), the plugin binds that GamerTag to the player's XUID and UUID in
  `state/controllers.json` and logs the binding to the server console.
- Authorization (`endbot.command.control`) is granted only to bound XUIDs. After binding, the GamerTag no longer
  matters: a GamerTag change keeps control; another account later taking the old GamerTag gets nothing.
- Players with an empty XUID (including every local Bot) can never bind.
- Removing a GamerTag from `endbot.toml` revokes its binding on the next start. `endbot controllers reset <tag>` returns
  a binding to pending for re-enrollment.
- The old `[authorization] allowed-uuids / allowed-xuids` lists remain accepted as pre-bound entries for rc.1 users.

Risk accepted: between setup and the owner's first join, a pending GamerTag can be claimed by whoever currently holds
that GamerTag. Pending entries are therefore shown by `endbot doctor`, and setup tells the operator to join once.

## 4. Local Bots and the BDS allow-list

Humans keep vanilla allow-list behaviour. A Bot accepted by the local-bot trust path needs no `allowlist.json` entry.

Observed on the current baseline (patched Endstone `0.11.12+endbot.1`, BDS 1.26.51.1): with `allow-list=true` and
an empty `allowlist.json`, a Bot accepted by the local-bot path joins, and nothing is written to `allowlist.json`. The
authentication result the existing hook returns (a local identity with `is_local` set and no XUID) is already exempt,
so no additional Endstone core hook is needed. Logins that are not accepted by the local-bot path take the stock BDS validator and allow-list unchanged.

Release gates for this behaviour: a non-allowlisted human is still rejected with `allow-list=true`; an allowlisted
human joins; a Bot with a wrong issuer, wrong owner signature, or with local-bot-auth disabled is rejected; the accepted
Bot has member (not operator) permissions.

## 5. NetherNet server trust

*Decision pending.* BDS generates a new NetherNet DTLS identity on every start, so rc.1's persistent
trust-on-first-use pin (`bds-nethernet.pin`) fails closed after every BDS restart and must be deleted by hand.
Upstream `bedrock-protocol` 3.60.x / `nethernet` 1.1.x exposes no hook to check the server's DTLS identity.

Proposed: remove the persistent pin and rely on

- a loopback-only runtime → BDS connection (`127.0.0.1`, `::1`, `localhost`; anything else is refused), and
- the local-bot token properties in `docs/SECURITY.md`: owner-signed, audience-bound, at most 120 s lifetime,
  single-use `jti`, and bound to the Bot's client key (`cpk`).

A same-host process impersonating BDS could observe a Bot's inputs and feed it a fake world, but cannot turn a
captured token into a login on the real server (no `cpk` private key, and the `jti` is single-use). A process able to
own BDS's loopback port already runs with the operator's privileges and can read `state/secrets` directly.

Alternative: keep a per-start pin managed by `endbot start` (cleared on each supervised BDS start). This needs a small
patch on upstream `nethernet` to expose the answer's DTLS fingerprint, and protects only against an impostor that
starts between BDS start and the first Bot connection.

## 5a. Process lifecycle

`endbot start`:

1. Load `endbot.toml`; regenerate `state/generated/*` and the plugin/Endstone config (§2).
2. Run the doctor checks that can fail fast (§7). Refuse to start on any safety invariant violation.
3. Check `<server>/version.txt` equals the locked BDS version. On mismatch, refuse and point at `endbot update`
   (never let Endstone update BDS implicitly during start).
4. Start the runtime, wait for its `endbot_runtime_ready` line.
5. Start Endstone/BDS (`python -m endstone -s <server>`, non-interactive), record PIDs, stdout/stderr logs, and exit
   codes under `state/run/`.
6. Desired-online Bots reconnect through the runtime's existing lifecycle.

`endbot stop`: send `stop` to the BDS console, wait for a clean exit with a timeout, then stop the runtime. A second
`stop` after the timeout escalates to process termination and is reported as unclean.

## 6. Setup

`endbot setup` has two paths and is always dry-run first: it prints every planned change and applies nothing until the
operator confirms (`--apply` for non-interactive use).

**Fresh BDS.** Creates `<instance>/server`, lets Endstone download the locked BDS through its normal acquisition path
(Endbot never bundles BDS), applies the safety defaults (`online-mode=true`, `allow-cheats=false`, no experiments),
creates `state/`, generates keys and token, and writes `endbot.toml` with the given controller GamerTags.

**Existing BDS.** Points `[server].path` at the existing directory. Before any change:

- Detect the BDS version. Endstone treats a directory without `version.txt` as older than supported and, when
  confirmed, re-downloads the server binary and the vanilla `behavior_packs/`, `resource_packs/`, and `definitions/`
  over the existing ones (worlds, `server.properties` values, `allowlist.json`, `permissions.json` are kept). A BDS
  newer than the locked version is refused.
- Back up `server.properties`, `allowlist.json`, `permissions.json`, `endstone.toml` if present, and the pack
  directories that Endstone may overwrite into `backups/<timestamp>/`. Worlds are not copied by default (size); setup
  requires the operator to confirm they have a world backup, or to pass `--backup-worlds`.
- Verify the safety invariants on the existing `server.properties` and world. A world that already has creative,
  cheat, or experiment history is reported; Endbot does not change world flags.
- List the planned changes (BDS binary update if needed, `endstone.toml` `[local-bot-auth]`, `plugins/endbot/`),
  then apply only on confirmation.

## 6a. Update

`endbot update <artifact>` installs a new `app/<version>` next to the current one, runs its doctor against the
existing `state/` and server, switches `app/current`, and keeps the previous version for rollback. If the new version
locks a different BDS version, it performs the §6 backup and preview before letting Endstone update BDS.

## 7. Doctor

`endbot doctor` reports, without changing anything:

- `endbot.toml` parses; identifiers are strings; paths exist.
- Safety invariants: `online-mode=true`, `allow-cheats=false`, no experiments, no Beta APIs / GameTest, world history
  flags (existing `scripts/preflight.py` checks move here).
- `endstone.toml [local-bot-auth]` enabled with a readable P-384 public key matching `state/secrets/owner-private.pem`.
- Control token present and readable; runtime and plugin agree on port.
- `<server>/version.txt` matches the lock; installed Endstone package version matches the lock.
- Controllers: bound, pending, and legacy pre-bound entries.
- When running: runtime reachable over the control port, BDS process alive, each desired-online Bot's state and last
  error.

## 8. Release artifacts

Per platform (`linux-x86_64`, `windows-x86_64`), built only in CI:

- `endbot-<version>-<platform>.zip|tar.gz`: private CPython 3.12 with the patched Endstone wheel, `endstone-endbot`,
  and the `endbot` CLI preinstalled; private Node.js; `runtime/` with production `node_modules`; launcher; licenses
  and third-party notices for every bundled component.
- `compatibility-manifest.json` and `SHA256SUMS` covering every file above.

The standalone wheels and runtime tarball stay available for developers but are not the operator path.
