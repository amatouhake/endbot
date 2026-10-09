# Architecture

## Boundaries

Endbot has three intentionally separate layers:

1. **Patched Endstone** owns only the login-validation seam that a normal plugin cannot reach. The source of truth is
   the exact upstream revision in `endstone.lock` plus the ordered patch files in `patches/endstone/`.
2. **Endbot plugin** owns `/bot`, explicit human UUID/XUID authorization, authoritative BDS player observation,
   deferred spawn placement, name collision checks against online players, block observations, and teleport through
   `Actor.teleport`.
3. **Endbot runtime** owns persistent Bot profiles, local identity, NetherNet sessions, desired lifecycle state,
   reconnect, and player inputs. It does not own or duplicate inventory, position, dimension, or other world state.

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
handle. `spawn` creates a profile implicitly; `despawn` changes desired state to offline but retains it; `forget`
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
