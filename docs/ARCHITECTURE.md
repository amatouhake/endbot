# Architecture

## Boundaries

Endbot has three intentionally separate layers:

1. **Patched Endstone** owns only the login-validation seam that a normal plugin cannot reach. The source of truth is
   the exact upstream revision in `endstone.lock` plus the ordered patch files in `patches/endstone/`.
2. **Endbot plugin** owns `/bot`, authenticated human authorization and console-managed persistent controller policy,
   authoritative BDS player observation,
   deferred spawn placement, name collision checks against online players, block observations, and teleport through
   `Actor.teleport`.
3. **Endbot runtime** owns persistent Bot profiles, local identity, NetherNet sessions, desired lifecycle state,
   reconnect, player inputs, and per-Bot chunk-radius preferences. It does not own or duplicate inventory, position,
   dimension, or other world state.

Prepared Endstone trees and build products are disposable. No maintained fork is necessary to reproduce a build.

## Authentication flow

The hook is around `ServerNetworkHandler::_validateLoginPacket`. BDS's original rejection path disconnects immediately,
so the hook recognizes an exact configured local issuer before invoking that destructive path:

```text
exact configured local issuer + all local checks pass -> local bot identity
anything else                                      -> original Microsoft validator
original Microsoft validator passes                -> normal player identity
original validator rejects                         -> reject
```

The local checks require ES384/P-384, owner signature, audience, bounded issue/not-before/expiry times, a single-use
UUID `jti`, UUID subject, bounded name, no Microsoft XUID claim, and a client-data signature/name/UUID bound to `cpk`.
The feature is disabled by default. Invalid local-looking traffic never becomes an authenticated identity.

Block mining follows the same separation: the plugin observes/raycasts an authoritative target without changing it;
the runtime packs one start (and, when needed, an abort) block action into input ticks and observes BDS block updates. BDS owns mining
progress, loot, durability and protection events. Mining is session-local: respawn and disconnect end it rather than
persisting it as desired lifecycle state, while teleport, dimension change and other operations end at most the current
block. Continuous mining uses periodic server-thread
raycasts on a dedicated poll worker, so a slow command (such as a spawn) cannot starve lease renewal; at most one poll
is queued. A target the runtime rejects (reach/dimension recheck) keeps the loop waiting; an unreachable runtime ends it.
Each loop is bound to a runtime session token and to its initiating controller's identity. Unlike queued commands, a
loop survives that controller's logoff and is ended only when the controller loses permission. Conditional stop/
continue messages cannot affect a newer loop, and a runtime lease bounds orphaned mining after plugin/control loss.

## Control boundary

The plugin and runtime use a versioned JSON request/response protocol over a loopback-only TCP socket. Each request
contains an unguessable token loaded from a private file. Both endpoints reject non-loopback configuration, requests
are size/time bounded, errors are returned explicitly, and the token is never logged. Blocking request/response work
runs on one ordered plugin worker rather than the BDS command/tick thread. World operations and command replies are
marshalled back through Endstone's synchronous scheduler before touching server objects. The plugin command service
and runtime lifecycle are transport-independent and use fake adapters in tests.

With shipped settings, session closure is bounded at 5 seconds, replacement delay is 1 second, the runtime control
socket deadline is 8 seconds, and the plugin response deadline is 10 seconds. Thus a normal replacement returns its
result/error before either transport deadline without blocking BDS ticks; it does not report an early timeout and
finish later.

The protocol is intentionally local operator IPC, not a remote API. It conveys desired lifecycle and input operations;
authoritative world operations stay in the plugin. In particular, teleport never dispatches the vanilla `/tp` command.

## Identity and lifecycle

A persisted profile is keyed by an immutable hidden UUID. Its unique Minecraft name is the user-visible command
handle. Profiles may also store a requested chunk-radius override; its absence inherits the runtime's current default.
`spawn` creates a profile implicitly; `despawn` changes desired state to offline but retains it; `forget`
removes only Endbot registration and makes no claim about BDS player records. Rename atomically replaces the name in
the same UUID profile and reconnects a live session so the Bedrock login name changes. There is no selection state.

The runtime distinguishes desired state from connection state. Unexpected loss while desired state is online enters a
bounded exponential reconnect sequence and never creates a second concurrent session. Intentional despawn cancels that
sequence. Intentional replacement waits for the prior Bedrock transport to close before opening the next connection;
if closure cannot be confirmed, the operation fails instead of risking two sessions. Status exposes connecting,
online, reconnecting, offline, and failed states plus the last error.

Lifecycle mutations are serialized per immutable UUID. Every possible session start carries a generation and rechecks
profile existence, desired state, and generation after asynchronous closure/delay/connect boundaries. A newer despawn
or forget therefore invalidates stale starts. A failed close retains the session reference in `failed` state so forget
remains blocked and a later lifecycle command can retry closure before any replacement. Explicit spawn, resume, and
reconnect reset the retry budget; attempts within one automatic reconnect sequence preserve bounded backoff state.

## World-state authority

BDS remains authoritative for dimension, position, rotation, inventory, health, and other player-world state associated
with the stable UUID. Endbot persists only identity and desired lifecycle/control state. `resume` and `reconnect` do not
send a placement; BDS therefore restores its native state. `spawn`, by contrast, explicitly requests placement and the
plugin applies it when the UUID joins. Any explicit non-spawn lifecycle intent clears an unconsumed spawn placement,
so a later resume, reconnect, or rename cannot unexpectedly relocate the Bot. This avoids a second location database.

The protocol-2193 schema ships with upstream `minecraft-data`: the legacy-slot presence byte and packed
`PlayerAuthInput` action-array layout already match the proven wire layouts asserted by the movement and action tests.
Upstream renamed `InputData` entries 34/35 (`item_interact`, `block_action`) at unchanged ordinals, so the wire bytes
are identical. Block interaction then follows a player-like start-action, packed authoritative interaction, and
next-tick stop-action sequence. BDS remains authoritative for target legality and inventory changes. Serialization tests
pin those byte layouts against the installed schema so a future dependency bump cannot silently change them.

## Multi-bot performance considerations

Endbot uses real Bedrock client sessions. Each connected bot therefore participates in normal BDS player processing,
including chunk streaming, entity updates, and server-authoritative input handling. A large number of sessions can
increase both server and runtime work. The dominant cost has not yet been established by a controlled benchmark;
optimization should follow measurement rather than assume that networking or simulation is the bottleneck.

### Current behavior and possible improvements

- **Chunk streaming:** `BedrockSession` does not currently specify a per-bot chunk radius. The pinned
  `bedrock-protocol@3.60.1` sends `request_chunk_radius` using `client.viewDistance || 10`. Because bots do not
  render terrain, a smaller requested radius is worth testing (for example, 4 and 2 against the existing default).
  The field used by the library is `client.viewDistance`; passing a `viewDistance` constructor option alone must
  not be assumed to set it. Confirm the actual outbound request, the server-accepted radius, and the resulting
  chunk traffic before introducing a configurable default. A lower streaming radius is not equivalent to lowering
  the server's simulation/tick distance.
- **Player inputs:** Each spawned session currently runs its input tick every 50 ms and queues
  `player_auth_input` while initialized. An idle-session reduction might reduce packet handling, but changing
  cadence could affect server-authoritative ticks, prediction corrections, gravity, movement, and interaction
  semantics. Test idle-only behavior separately from moving or acting bots; do not skip required protocol inputs
  solely on the assumption that an unmoving bot is safe to throttle.
- **Connection bursts:** Spreading initial connections over time may reduce transient authentication and initial
  chunk-streaming peaks. It will not reduce the steady-state cost of online bots.
- **Entity replication:** Closely grouped bots may generate redundant player/entity updates to other bot sessions.
  Any filtering needs to preserve information required for valid bot interactions. Server-side recipient filtering
  or deeper BDS changes should be considered only after profiling demonstrates a material benefit.

### Suggested performance validation

Record separate BDS and Node.js runtime CPU usage, memory, server MSPT/TPS, per-session received traffic, and
connection time. Compare a no-bot baseline with progressively larger populations (for example, 10, 30, and 50),
separating the connection burst from steady state. Repeat with bots co-located versus widely separated, idle versus
moving, and with the default versus reduced requested chunk radii. A compatible native profiler, such as Spark for
Endstone, may help identify expensive BDS paths; verify its compatibility with the pinned BDS/Endstone pair first.

For a chunk-radius experiment, record outbound `request_chunk_radius`, any radius response, and `level_chunk` /
`sub_chunk` counts and bytes. Measure actual BDS improvement rather than treating the theoretical change in chunk
area as a CPU or TPS improvement. Recheck login, reconnection, teleportation, movement across chunk boundaries,
entity targeting, and block interactions. In particular, test farms and other gameplay mechanisms that depend on
players loading or simulating nearby areas.

All optimizations must retain normal human Microsoft/Xbox authentication, bot identity and world persistence,
`online-mode=true`, `allow-cheats=false`, and the existing achievement-compatible behavior. These are research
candidates, not implemented performance features or benchmark claims.

## Patch lifecycle

`scripts/prepare_endstone.py` validates the tag-to-commit relationship, clones/fetches a bare cache, makes a separate
checkout, and applies `patches/endstone/series`. The cache contains pristine upstream objects only. Patch application
creates commits in the disposable checkout and must finish with a clean working tree.

When updating Endstone, first validate a real Endstone/BDS pair, rebase the smallest necessary patch delta, run all
upstream and Endbot tests, repeat the security negative controls, complete the manual achievement gate, then update the
lock and release manifest together.

## Operator CLI and supervisor internals

How `endbot start` and its children work inside. What an operator does with them is in
[OPERATIONS.md](OPERATIONS.md#start-and-stop); the process split itself is
[ENDBOTD.md P1](ENDBOTD.md#p1-process-split-three-children-under-a-thin-supervisor).

**Children.** The supervisor (`cli/endbot/src/endbot_cli/supervisor.py`) starts each child from the running
`app/<version>/` and waits up to 30 s for its ready line before starting the next:

| Child | Command | Ready line |
| --- | --- | --- |
| Runtime | `node runtime/src/cli.js --config state/generated/endbot-runtime.json` | `{"event": "endbot_runtime_ready"}` |
| endbotd | `python -m endbotd --config state/generated/endbotd.toml` | `{"event": "endbotd_ready"}` |
| Endstone/BDS | `python -m endbot_cli.endstone_entry -s <server> --no-interactive` | none |

`endbot_cli.endstone_entry` is Endstone's own `python -m endstone` entry. On Linux it first repoints the relocated
interpreter's `LIBDIR` at its real library directory, because the bundled CPython still names its build prefix.

**Run-state files** under `state/run/` connect the CLI to the supervisor without cross-process file locking
(`runstate.py`):

| File | Written by | Read by |
| --- | --- | --- |
| `supervisor.pid` | The supervisor | Every CLI command that needs to know whether one runs; a stale PID is diagnosed and removed |
| `stop.request` | `endbot stop` | The supervisor, which then runs the stop sequence |
| `console.request` | `endbot console`, one line per request | The supervisor, which forwards each line to the BDS console and truncates the file |
| `last-exit.json` | The supervisor | `endbot stop` (clean or UNCLEAN) and `endbot doctor` (first-start detection) |

The runtime is stopped through its `shutdown` control method over the control port.

**Direct NetherNet signalling.** A client that joins BDS by address signals over HTTP: it POSTs its SDP offer, with
every ICE candidate in it, to `http://<host>:<server-port>/v1/join/<client network id>` and receives the SDP answer in
the response (a bare number is a NetherNet error code). Upstream `bedrock-protocol` only signals over LAN or the online
services, so the runtime replaces the NetherNet client's signal handler with `runtime/src/direct-signalling.js`. BDS
answers with `Connection: close` and closes first, which is why every join by address leaves `server-port` in
TIME-WAIT (`cli/endbot/src/endbot_cli/netcheck.py`).

**No server identity pin.** rc.1 kept a trust-on-first-use pin (`bds-nethernet.pin`). BDS generates a new NetherNet
DTLS identity on every start, so the pin failed closed after every BDS restart, and upstream `bedrock-protocol` 3.60.x
and `nethernet` 1.1.x expose no hook to verify the identity. The loopback restriction and the local-bot token replace
it; the full rationale is in [SECURITY.md](SECURITY.md).

**Allow-list exemption.** The authentication result the local-bot hook returns (a local identity with `is_local` set
and no XUID) is already exempt from the BDS allow-list, so no additional Endstone core hook is needed.

## Release builds

- Release-candidate jobs build the runner with `node runner/build.mjs --out build/runner-stage`, run `node --test` in
  `runner/`, and pass `--runner-dir build/runner-stage` to `scripts/build_bundle.py`.
- The builder checks the binary, the `api.luau` sidecar, both MIT notices and the Linux executable permission before
  copying them. The notices go to `licenses/LUAU_LICENSE.txt` and `licenses/NLOHMANN_JSON_LICENSE.txt`.
- Bundle self-tests smoke-run a `params {}` script without BDS.
- Local bundle builds may omit `--runner-dir`. Those bundles have no runner and cannot enable scripting without
  installing one; for them only, `scripts/test_bundle.py --allow-missing-runner` skips the absent runner directory.
- The standalone wheels and the runtime tarball stay available for developers
  ([below](#manual-install-from-release-candidate-artifacts)). The release procedure is in
  [release/README.md](../release/README.md).

## Manual install from release-candidate artifacts

The developer path: it rebuilds the stack from the release-candidate wheels and the runtime source bundle instead of
the platform bundle. Operators use [INSTALL.md](INSTALL.md).

### Candidate contents

A release candidate (`endbot-<version>-candidate` workflow artifact, assembled but never published automatically)
contains:

| File | Contents |
| --- | --- |
| `endstone-0.11.16+endbot.1-*-manylinux_*.whl` | Patched Endstone for Linux x86_64 (CPython 3.12), repaired to a `manylinux_*` platform tag. Use it on Linux; it does not install on Windows. |
| `endstone-0.11.16+endbot.1-*-win_amd64.whl` | Patched Endstone for Windows x64 (CPython 3.12), built by the workflow's Windows job from the same `endstone.lock` + `patches/endstone/` inputs as the Linux wheel. Use it on Windows; it does not install on Linux. |
| `endstone_endbot-0.7.2-*.whl` | Endbot plugin. Requires exactly `endstone==0.11.16+endbot.1`, so pip cannot substitute official unpatched `0.11.16`, and exactly the `endbot-core` wheel of the same release. |
| `endbot_core-0.7.2-*.whl` | Shared, Endstone-free Endbot logic the plugin imports. Platform-independent; not on any package index. |
| `endbot-0.7.2-*.whl` | The `endbot` operator CLI. |
| `endbot-0.7.2-windows-x86_64.zip`, `endbot-0.7.2-linux-x86_64.tar.gz` | The operator platform bundles ([INSTALL.md](INSTALL.md)). |
| `endbot-runtime-0.7.2.tar.gz` | Runtime source bundle (`package.json`, `package-lock.json`, `README.md`, `endbot-runtime.example.json`, `scripts/`, `src/`). |
| `endbot-config-0.7.2.tar.gz` | Example configuration (`config/`), `LICENSE`, `THIRD_PARTY_NOTICES.md`. |
| `ENDSTONE_LICENSE` | Upstream Endstone license carried from the pinned checkout. |
| `endbot-ui-<pack version>.mcpack` | Optional client-side UI resource pack ([UI_PACK.md](UI_PACK.md)). Its version is the pack's own. |
| `compatibility-manifest.json` | Machine-readable binding of Endbot revision, Endstone tag/commit/package, BDS version/build/protocol, patch revision, and validation state (schema: `release/compatibility-manifest.schema.json`). |
| `SHA256SUMS` | SHA-256 checksums of every file above. |

Verify before installing:

```bash
sha256sum -c SHA256SUMS
python3 -c "import json; print(json.load(open('compatibility-manifest.json'))['endstone'])"
```

The manifest's `tested_safety_conditions` reports the current artifact's own validation state. A current patched
package never inherits human/Xbox achievement observations from an earlier one; historical M0/M2 evidence stays
attributed to `0.11.11+endbot.1` under `historical_validation`. Do not claim achievement-preserving compatibility for
the candidate until the exact candidate passes the documented human/live gate.

### Linux install and start

Requirements: Python 3.12, Node.js 24+, git, cmake, and a C++ compiler. Docker is needed only to build the candidate,
not to install it. `npm ci` falls back to building `raknet-native` from source when no published prebuild matches the
platform; without a compiler toolchain the install then fails inside the dependency's build check.

1. Create a virtual environment and install the wheels offline from the candidate directory. The repaired Endstone
   wheel carries no LLVM runtime dependency:
   ```bash
   python3 -m venv .venv && . .venv/bin/activate
   python -m pip install ./endstone-0.11.16+endbot.1-*.whl ./endstone_endbot-0.7.2-*.whl ./endbot_core-0.7.2-*.whl
   python -c "import importlib.metadata as m; print(m.version('endstone'))"
   ```
   Expected: `0.11.16+endbot.1`. Never install official `endstone==0.11.16` into this environment.
2. Unpack the runtime bundle outside the server folder and install its pinned dependencies:
   ```bash
   mkdir -p /opt/endbot && tar -xzf endbot-runtime-0.7.2.tar.gz -C /opt/endbot
   npm ci --prefix /opt/endbot
   cp /opt/endbot/endbot-runtime.example.json /opt/endbot/endbot-runtime.json
   node /opt/endbot/src/cli.js --config /opt/endbot/endbot-runtime.json
   ```
   The runtime creates its control token and owner key pair on first start. Keep that directory under the operator's
   own account; POSIX files are mode `0600`.
3. Create the server folder and start once so Endstone bootstraps official BDS and writes its default configuration:
   ```bash
   mkdir -p /srv/endbot-server && endstone -s /srv/endbot-server
   ```
   Stop the server, then configure:
   - `server.properties`: keep `online-mode=true` and `allow-cheats=false`, and set `transport=nethernet` (BDS
     1.26.60 refuses players on RakNet). Leave `server-udp-ports` unset: since upstream `v0.11.12` the server shares
     one UDP port across NetherNet sessions. Check safety with `python3 scripts/preflight.py
     /srv/endbot-server/server.properties --acknowledge-world-state-unverified` from a checkout, or apply the same
     values by hand. `preflight` covers only the safety invariants; `endbot doctor` also checks the transport.
   - `endstone.toml`: set the `[local-bot-auth]` table from the unpacked `config/endstone.local-auth.example.toml`
     with `enabled = true` and `public-key-file` pointing at the runtime's `owner-public.pem`. Only the public key
     is ever configured in Endstone. The `[network]` `stun-servers` default needs no change for loopback use.
   - Create an endbotd token (at least 32 random characters, e.g. `python -c "import secrets;
     print(secrets.token_urlsafe(32))"` into `endbotd.token`) and write an `endbotd.toml` (see
     `core/src/endbotd/config.py`): `[link]` port and `token-file`, `[runtime]` port and the runtime's
     `control.token`, `[stores] directory` (Groups and stored scripts) and `scripts-folder` (script files), and the
     human tester's UUID or decimal XUID in one `[authorization]` allowlist. Do not make the tester an operator.
   - `plugins/endbot/config.toml`: an `[endbotd]` table with `port` (the link port) and `token-file` (the same
     `endbotd.token`).
   - Two TOML traps: Windows paths must use *single-quoted* literal strings
     (`token-file = 'C:\endbot\secrets\endbotd.token'`), because backslashes in double-quoted strings are escapes;
     and XUIDs must be *strings* (`allowed-xuids = ["2535466722952312"]`), not bare numbers.
4. Start the runtime first, then endbotd (`python -m endbotd --config endbotd.toml`), then the server. The runtime
   prints `endbot_runtime_ready` and endbotd `endbotd_ready` when they listen; the server log reports
   `Endbot linked to endbotd`, then `Accepted local bot '...'` on Bot login, and the human joins through the normal
   Microsoft/Xbox path.

### Native Windows install and start

Each platform installs its own Endstone wheel from the candidate; the other artifacts are shared.

1. Install the Windows wheel from the candidate directory into a virtual environment:
   ```powershell
   py -3.12 -m venv .venv; .\.venv\Scripts\Activate.ps1
   python -m pip install .\endstone-0.11.16+endbot.1-*-win_amd64.whl
   python -c "import importlib.metadata as m; print(m.version('endstone'))"
   ```
   Expected: `0.11.16+endbot.1`. Never install official `endstone==0.11.16` into this environment. The workflow
   inspects the wheel for its `win_amd64` tag, locked version and runtime DLL, and install-tests it with the plugin in
   a fresh venv. Building patched Endstone from source is a development path in [WINDOWS_DEV.md](WINDOWS_DEV.md).
2. Install the plugin and core wheels into the same environment (`python -m pip install
   .\endstone_endbot-0.7.2-*.whl .\endbot_core-0.7.2-*.whl`). Both are platform-independent, and the plugin still
   pins the exact patched Endstone version.
3. Unpack `endbot-runtime-0.7.2.tar.gz`, run `npm ci` (npm 12 needs `--allow-remote=root` on the command line for
   the pinned tarball dependency URL; see [WINDOWS_DEV.md](WINDOWS_DEV.md)), copy the example runtime configuration
   outside the unpacked tree, and start the runtime with Node 24+.
4. Create the server folder with `endstone -s` (official Windows BDS `1.26.65.1` build `53624742`, protocol `2225`),
   then apply the same `server.properties`, `endstone.toml` and plugin configuration as on Linux. Linux remains the
   CI and release-artifact baseline; what was checked on Windows is in [COMPATIBILITY.md](COMPATIBILITY.md).

### Updating to a newer candidate

1. Stop the server (`stop` in its console) and the runtime (`Ctrl+C`). Despawn Bots first.
2. Verify the new candidate's `SHA256SUMS` and confirm its `compatibility-manifest.json` names the intended
   Endstone package and BDS pair.
3. Replace the installed wheels (`pip install -U` the new pair; the plugin's exact pin rejects a mismatched
   Endstone) and unpack the new runtime and config tarballs next to the old ones. Re-apply only the operator-owned
   configuration values (token file, owner keys, allowlists); never copy generated secrets, pins, worlds, or
   machine-local paths between machines.
4. Start the runtime, then the server, and re-run the live regression matrix in [WINDOWS_DEV.md](WINDOWS_DEV.md)
   plus `/bot ping` before returning the server to normal use.

### Out of scope

- BDS is never bundled, vendored, or copied into the repository or the candidate.
- No tag or GitHub Release is created from a green build alone. Publication waits for the exact candidate to pass the
  required human/live gate (normal Microsoft/Xbox login, local Bot login and coexistence, the command/input matrix,
  and the actual Xbox achievement observation), with the manifest and evidence updated accordingly.
- World pickup to same-session runtime inventory synchronization remains a known limitation, with reconnect as the
  documented recovery path; same-session pickup inference and sneak ledge simulation stay deferred.
