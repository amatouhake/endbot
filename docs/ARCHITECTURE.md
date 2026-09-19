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

The pinned protocol data is also prepared reproducibly. Endbot corrects the protocol-2193 legacy-slot presence and
packed `PlayerAuthInput` action-array layout while preserving the pinned enum ordinals already proven by movement and
action tests. Block interaction then follows a player-like start-action, packed authoritative interaction, and
next-tick stop-action sequence. BDS remains authoritative for target legality and inventory changes. These narrow,
serialization-tested corrections do not change the pinned protocol baseline.

## Patch lifecycle

`scripts/prepare_endstone.py` validates the tag-to-commit relationship, clones/fetches a bare cache, makes a separate
checkout, and applies `patches/endstone/series`. The cache contains pristine upstream objects only. Patch application
creates commits in the disposable checkout and must finish with a clean working tree.

When updating Endstone, first validate a real Endstone/BDS pair, rebase the smallest necessary patch delta, run all
upstream and Endbot tests, repeat the security negative controls, complete the manual achievement gate, then update the
lock and release manifest together.
