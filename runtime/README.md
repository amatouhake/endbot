# Endbot runtime

The runtime owns persistent Bot profiles and accountless Bedrock client sessions. A profile contains an immutable UUID,
a unique user-visible Minecraft name, and desired lifecycle state. It deliberately does not duplicate BDS world state.

Install and run from a standalone clone:

```bash
npm ci --prefix runtime
cp runtime/endbot-runtime.example.json endbot-runtime.json
node runtime/src/cli.js --config endbot-runtime.json
```

The exact NetherNet-capable `bedrock-protocol` revision is pinned in `package-lock.json`. Its npm dependency currently
lags the pinned BDS protocol, so `npm ci` runs `scripts/prepare-minecraft-data.js` to fetch an exact public
`minecraft-data` revision and generate the 1.26.50/protocol-2193 schema in the installed package. No sibling checkout is
used. Preparation applies a tested Endbot schema correction only to standalone inventory transactions while preserving
the distinct `PlayerAuthInput` layout. Configuration paths are resolved relative to the configuration file. Keep the generated control token, owner
private key, server identity pin, and profile directory outside source control. The owner public key is the only key
configured in patched Endstone.

The control service refuses non-loopback binds and authenticates every request with the generated token. Unexpected
disconnects retry with bounded exponential backoff. An intentional `despawn` disables reconnect; `resume` reenables it.
Per-profile lifecycle transitions are serialized, and a newer lifecycle intent invalidates delayed connection starts.
Replacement sessions wait for confirmed closure of the prior transport; a failed close is visible and retryable without
opening a second session. Operator-initiated starts reset the retry budget while one automatic reconnect sequence retains
its bounded attempt count. The default 8-second runtime request deadline covers the bounded 5-second close plus 1-second
replacement delay and is shorter than the plugin's 10-second response deadline. The NetherNet server identity pin fails
closed if BDS presents a different identity; rotate the pin only after independently verifying an intentional restart.

Private keys, tokens, server pins, and UUID artifacts use complete-before-publish creation and reject corrupt existing
files and symlinks. POSIX files are mode `0600`; Windows relies on native ACLs. Standard local Windows and Linux
filesystems support the same-volume hard-link publication used for initial artifacts.

```bash
npm run check --prefix runtime
npm test --prefix runtime
```
