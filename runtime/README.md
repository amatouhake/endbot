# Endbot runtime

Block mining is available through `/bot <name> destroy [x y z [face]]`, `destroy continuous` and `destroy stop` in the
plugin. Authenticated `mine` IPC (the internal method name)
starts one server-authoritative target using packed player block actions; `status.mining` records active/completed/
canceled/failed state, target and reason. BDS owns speed, drops, durability and protection events. Explicit stops, death
and disconnect clear mining; other inputs keep it (a held-item change restarts the current block, teleport and
dimension changes end only the current block). Jobs are not persisted or retried after reconnect. For continuous mining, the plugin reselects
targets on the server thread and sends authenticated `continue` requests with the runtime's unique loop ID. These
renew a 200-input-tick lease; stale requests cannot resume a stopped/replaced session. `mine start` also accepts the
plugin's `for <ticks>` and `count <blocks>` limits: the tick budget counts input sends (wall clock, including waits
between blocks), while the block budget counts completed targets and is unaffected by waiting; a loop that sets both
ends at whichever comes first. `status.mining` also exposes
`continuous`, `loopId`, `waiting`, `blocksBroken` and `blockCount` state, and a finished loop keeps its final
`blocksBroken` count. See [commands](../docs/COMMANDS.md#mining-blocks)
and [validation](../docs/MINING_VALIDATION.md).

The runtime owns persistent Bot profiles and accountless Bedrock client sessions. A profile contains an immutable UUID,
a unique user-visible Minecraft name, desired lifecycle state, and an optional Bot-specific chunk radius preference.
It deliberately does not duplicate BDS world state.

Install and run from a standalone clone:

```bash
npm ci --prefix runtime
cp runtime/endbot-runtime.example.json endbot-runtime.json
node runtime/src/cli.js --config endbot-runtime.json
```

The exact upstream `bedrock-protocol` release is pinned in `package-lock.json`. Its `minecraft-data` dependency already
ships the Bedrock 1.26.51/protocol-2193 schema with the proven wire layouts (BDS 1.26.52 uses the same protocol 2193), so no schema preparation step runs at
install. Configuration paths are resolved relative to the configuration file. Keep the generated control token, owner
private key, and profile directory outside source control. The owner public key is the only key
configured in patched Endstone.

Standalone runtime JSON accepts `"chunkRadius": 5` (default 5; integer 2..32 chunks) for every Bot's requested loading
radius. The protocol library previously defaulted to 10. Packaged instances configure `[runtime].chunk-radius` in
`endbot.toml`; generated JSON is overwritten on start. `/bot <name> radius <2-32|default>` persists a per-Bot override or
returns to the shared default; live requests apply without reconnecting. BDS may clamp the requested range. Server simulation distance and
chunks loaded by other players are independent. See `docs/OPERATIONS.md` section 2.

The control service refuses non-loopback binds and authenticates every request with the generated token. Unexpected
disconnects retry with bounded exponential backoff. An intentional `despawn` disables reconnect; `resume` reenables it.
Per-profile lifecycle transitions are serialized, and a newer lifecycle intent invalidates delayed connection starts.
Replacement sessions wait for confirmed closure of the prior transport; a failed close is visible and retryable without
opening a second session. Operator-initiated starts reset the retry budget while one automatic reconnect sequence retains
its bounded attempt count. The default 8-second runtime request deadline covers the bounded 5-second close plus 1-second
replacement delay and is shorter than the plugin's 10-second response deadline. Bedrock sessions connect to loopback BDS servers only: the configuration loader and every session refuse any other
`serverHost`. There is no persistent server identity pin (BDS regenerates its NetherNet identity on every start); see
`docs/SECURITY.md` for why the loopback path plus the Bot token properties carry that trust. A `serverIdentityPinPath`
left in an older configuration is ignored, and an old `bds-nethernet.pin` file can be deleted.

The default transport is NetherNet. An explicit `"transport": "raknet"` in runtime JSON enables the optional
direct-loopback path with `raknet-native`, no LAN discovery, no ping, and no advertised-port following. It reuses the
same local-bot authentication and requires a BDS configured with `transport=raknet`. Current `endbot setup/start/doctor`
carry the `[server] transport` selection through both configs and reject mismatches; choose `setup --fresh --transport raknet`
instead of editing generated JSON. The isolated [RakNet feasibility runner](../docs/RAKNET_VALIDATION.md) reproduces
the experiment and negative auth gates independently of the operator lifecycle.

Private keys, tokens, and UUID artifacts use complete-before-publish creation and reject corrupt existing
files and symlinks. POSIX files are mode `0600`; Windows relies on native ACLs. Standard local Windows and Linux
filesystems support the same-volume hard-link publication used for initial artifacts.

The matching plugin reads all 36 BDS inventory slots plus offhand and armor every four server ticks by default and publishes changed observations, plus
unchanged corrections every 20 ticks, over each local Bot's existing
Bedrock connection. The bounded, UUID-scoped `endbot:inventory-v2` envelope uses protocol 2193's `script_custom_event` packet
through Endstone's public `send_packet`; it does not run a script engine or require experiments/Beta APIs. Item IDs come
from the connection's item registry; NBT types and 64-bit integers are preserved. Native inventory updates still work,
and invalid/unknown observations are rejected without replacing good state. This transient cache is not persisted and
never writes BDS inventory; normal transactions perform item actions. Full snapshots use 40 KiB fragments under the
60 KiB packet bound, with a 2 MiB total bound and atomic decoding. `drop` accepts selected one/stack, slot, item/count,
and all selectors through legacy container-to-world transactions without stack IDs. BDS validates every request;
predicted cache changes are corrected by the next authoritative observation. See [validation](../docs/INVENTORY_VALIDATION.md).

`use for <ticks>` schedules a bounded hold followed by one release; existing once/continuous/interval modes remain.
Native selected-slot updates and plugin observations share a deduplicated equipment announcer whose state is separate
from the inventory cache, so a native delta cannot suppress a later empty-hand announcement. See
[timed-use and synchronization validation](../docs/ITEM_USE_SYNC_VALIDATION.md).

```bash
npm run check --prefix runtime
npm test --prefix runtime
```
