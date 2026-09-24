# Endbot runtime

The runtime owns persistent Bot profiles and accountless Bedrock client sessions. A profile contains an immutable UUID,
a unique user-visible Minecraft name, and desired lifecycle state. It deliberately does not duplicate BDS world state.

Install and run from a standalone clone:

```bash
npm ci --prefix runtime
cp runtime/endbot-runtime.example.json endbot-runtime.json
node runtime/src/cli.js --config endbot-runtime.json
```

The exact upstream `bedrock-protocol` release is pinned in `package-lock.json`. Its `minecraft-data` dependency already
ships the Bedrock 1.26.51/protocol-2193 schema with the proven wire layouts, so no schema preparation step runs at
install. Configuration paths are resolved relative to the configuration file. Keep the generated control token, owner
private key, and profile directory outside source control. The owner public key is the only key
configured in patched Endstone.

The control service refuses non-loopback binds and authenticates every request with the generated token. Unexpected
disconnects retry with bounded exponential backoff. An intentional `despawn` disables reconnect; `resume` reenables it.
Per-profile lifecycle transitions are serialized, and a newer lifecycle intent invalidates delayed connection starts.
Replacement sessions wait for confirmed closure of the prior transport; a failed close is visible and retryable without
opening a second session. Operator-initiated starts reset the retry budget while one automatic reconnect sequence retains
its bounded attempt count. The default 8-second runtime request deadline covers the bounded 5-second close plus 1-second
replacement delay and is shorter than the plugin's 10-second response deadline. NetherNet sessions connect to loopback BDS servers only: the configuration loader and every session refuse any other
`serverHost`. There is no persistent server identity pin (BDS regenerates its NetherNet identity on every start); see
`docs/SECURITY.md` for why the loopback path plus the Bot token properties carry that trust. A `serverIdentityPinPath`
left in an older configuration is ignored, and an old `bds-nethernet.pin` file can be deleted.

Private keys, tokens, and UUID artifacts use complete-before-publish creation and reject corrupt existing
files and symlinks. POSIX files are mode `0600`; Windows relies on native ACLs. Standard local Windows and Linux
filesystems support the same-volume hard-link publication used for initial artifacts.

```bash
npm run check --prefix runtime
npm test --prefix runtime
```
