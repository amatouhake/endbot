# Spike report: `bedrock-protocol` fork → upstream 3.60.1

Branch: `spike/upstream-bedrock-protocol` (spike only, will not be merged as-is).
Scope: `runtime/` dependency migration + verdict on deleting the
Endbot-specific `minecraft-data` workaround
(`runtime/scripts/prepare-minecraft-data.js`, formerly run from `postinstall`).

## 1. Node version used

- `node --version` → **v24.17.0** (satisfies upstream `>=24` and the new
  `runtime` `engines.node >= 24`).
- `npm --version` → **12.0.2**. Relevant because npm 12 defaults to
  `allow-git=none` and `allowScripts` gating (see §3).

## 2. Dependency tree changes

`runtime/package.json`:

- `bedrock-protocol`: fork tarball URL (`fb0af8e…`) → exact **`3.60.1`**.
- `engines.node`: `>=22` → **`>=24`** (upstream 3.60.1 and
  `prismarine-xbox-services` require Node `>=24`).
- Removed the `postinstall` minecraft-data step.
- Removed the `allowScripts` entry for `node-datachannel@0.31.0` — it is no
  longer in the tree, so there is nothing to approve.

Resolved versions (`package-lock.json`, lockfileVersion 3):

- `bedrock-protocol` **3.60.1**, `minecraft-data` **3.117.0** (officially
  contains Bedrock **1.26.51 / protocol 2193**), `nethernet` **1.1.2**,
  `werift` **0.24.4** (pure-JS WebRTC, the `nethernet` default backend).

Removed from the tree:

- `node-datachannel` (native WebRTC, previously allow-listed) — gone entirely.
- The `prepare-minecraft-data.js` git-clone + `npm run generate:data` +
  `patchPinnedProtocolSchema` install-time mutation — script deleted.

Native addons still present in the tree but inert on our path:

- `raknet-native@1.2.3` (optional dep of `bedrock-protocol`) ships
  prebuilt `.node` files for darwin/linux/win32, and `raknet-node` ships
  three `.node` files. Neither is loaded when `transport: 'nethernet'`.
- `npm query ':attr(scripts, [install]), :attr(scripts, [postinstall]),
  :attr(scripts, [preinstall])'` returns exactly one package with install
  scripts — `raknet-native` (`install: node buildChecks.js`) — and npm
  reports it **blocked** by the `allowScripts` policy, so it never
  executed. No `preinstall`/`install`/`postinstall` script runs at install.

Git-sourced dependencies (still present upstream):

- `prismarine-xbox-services@github:PrismarineJS/prismarine-xbox-services#052a9676…`.
  The lockfile records it as
  `git+ssh://git@github.com/PrismarineJS/prismarine-xbox-services.git#052a967…`.
- Install-time behaviour verified from clean (`rm -rf runtime/node_modules`):
  - Plain `npm ci --prefix runtime` **fails** with `EALLOWGIT`
    (`Refusing to fetch "prismarine-xbox-services@github:…"`), because npm 12
    defaults to `--allow-git=none`. This refusal itself proves a **git fetch
    is attempted at install time**.
  - `npm ci --prefix runtime --allow-git=all` succeeds from clean and
    reproduces the tree. Repeat installs are served from the npm cache, but
    a cold machine performs a git (SSH) fetch of that commit during install.
  - Operational consequence: any CI/operator environment must either pass
    `--allow-git=all` (or equivalent config) **and** have `github.com` SSH
    access, or vendor/cache the npm cache. This is a regression in
    install hermeticity versus a pure-registry tree.

## 3. API changes made

`runtime/src/protocol-session.js` (`#connect`):

- Removed fork-only options `raknetBackend: 'nethernet'`,
  `nethernetServerKeyPin`, `onNetherNetServerTrust` and all pin-file
  loading (`loadServerIdentityPin`, `loadOrCreatePersistentArtifact` /
  `loadPersistentArtifact` imports, `fs` import).
- New `createClient` call (options verified against upstream
  `bedrock-protocol/src/{client.js,createClient.js,options.js}` and
  `nethernet/src/client.js`):
  `transport: 'nethernet'`, `nethernet: { signalling: 'lan' }`.
  `host` (loopback address) is passed through: upstream uses it as the
  NetherNet discovery destination (`broadcastAddress:7551/udp`), so LAN
  discovery is directed at loopback. `networkId` is intentionally not
  configured — upstream discovery fills `config.nethernet.networkId` from
  the server advertisement before `init()`. `webrtcBackend` is left at the
  default (`werift`, pure JS).
- Kept `offline: false` + the custom `authflow` from `createLocalBotAuth`,
  `skinData: { SelfSignedId }`, `connectTimeout`.
- Added the hard loopback-only check (unit-tested): `connect()` throws
  `Endbot connects to loopback BDS servers only` unless `serverHost` is
  `127.0.0.1`, `localhost` or `::1`, before any protocol module load or
  client creation. No new trust mechanism was invented.
- `runtime/src/config.js`: `serverIdentityPinPath` parsing untouched, marked
  `// TODO(trust): replaced by orchestrator decision`; default
  `gameVersion` `1.26.50` → **`1.26.51`** (upstream `CURRENT_VERSION`;
  `minecraft-data` maps it to protocol 2193). `endbot-runtime.example.json`
  updated to match.
- `item_interact` rename: `protocol-session.js` pushed InputData flag
  `perform_item_interaction`; upstream 1.26.51 renamed entries 34/35 to
  `item_interact` / `block_action` at identical ordinals (see §5), so the
  source string was updated. Wire bytes are unchanged.

## 4. Login/handshake diff findings (fork `fb0af8e` vs upstream 3.60.1)

Diffed `src/client.js`, `src/createClient.js`, `src/options.js`,
`src/transforms/framer.js`, `src/rak.js`, `src/handshake/login.js`
(fork read-only via `git show fb0af8e:…`), plus
`src/auth/loginEnvelope.js` (identical, byte-for-byte).

- **`src/handshake/login.js` — the only content diff relevant to Endbot is
  nil-behavioural for us.** Fork wraps the offline-OIDC `multiplayerToken`
  signing in `if (!client.multiplayerToken)` (preserve a pre-set token);
  upstream always re-signs. Endbot connects with `offline: false`, so this
  branch never executes; the `else` (online) identity-chain signing and the
  entire `createClientUserChain` (skin payload incl. `SelfSignedId` override
  from our `skinData`, `ServerAddress`, `DeviceOS`) are identical.
- **Login path still sends exactly the chain/token Endbot's local-bot auth
  produces.** Upstream `src/client/auth.js#authenticate` calls our
  `authflow.getMinecraftBedrockToken(clientX509)` → `postAuthenticate`
  sets `client.accessToken = chain` (`[profileCertificate,
  profileCertificate]`) and `client.multiplayerToken = token` (local
  identity token). `sendLogin()` (via `network_settings`) builds
  `[clientIdentityChain, ...accessToken]` with `AuthenticationType: 0` and
  `Token: multiplayerToken` (the `newLoginIdentityFields` envelope, active
  for 1.26.51). Server side, upstream `src/auth/loginEnvelope.js` parses
  exactly this `Certificate`/`Token` envelope — same file as the fork.
- **NetherNet identity assertion flows automatically.** Upstream
  `Client._connect` attaches `{ privateKey, token: this.multiplayerToken }`
  as the NetherNet `a=identity` offer attribute for `transport: 'nethernet'`,
  so the patched BDS keeps receiving the local identity token at the
  transport layer with no extra code.
- **`src/client.js` / `src/createClient.js` / `src/options.js`**: the fork's
  `raknetBackend` + `src/nethernet.js` (511 lines: `nethernetServerKeyPin`,
  `onNetherNetServerTrust`, fail-closed pin) design is replaced upstream by
  the `transport: 'raknet' | 'nethernet'` split with the external
  `nethernet` package (`NetherNetClient`, `signalling: 'lan' | 'services'`).
  Upstream exposes **no** pin/trust callback — confirming the brief.
  Transport-equivalence notes: upstream sets `batchHeader = null` and
  `disableEncryption = true` for NetherNet vs the fork's `batchHeader = 0`
  plus skipping `enableEncryption` — same wire effect (no batch header byte,
  no inner Bedrock cipher; DTLS provides encryption). `Framer.decode` is
  otherwise unchanged between fork and upstream.
- **`src/rak.js`**: only the `ping` refactor (abortable, length-prefix
  tolerant) and removal of the fork's `'nethernet'` case (now a separate
  module). RakNet path unused by Endbot.
- **`src/options.js`**: `CURRENT_VERSION` `1.26.50` → `1.26.51`;
  `defaultOptions.transport = 'raknet'`; the fork's
  `defaultClientBackend()` helper is gone (explicit `transport` replaces it).

## 5. Schema comparison result — patch DELETED, verdict: upstream equivalent

`runtime/scripts/prepare-minecraft-data.js` is deleted; nothing regenerates
or mutates `minecraft-data` at install. Comparison of upstream
`minecraft-data@3.117.0` Bedrock 1.26.51 (`protocol.json`, protocol 2193)
against what `patchPinnedProtocolSchema` produced — field-level verdict:

| Layout | Patch output | Upstream 3.117.0 | Verdict |
|---|---|---|---|
| `TransactionLegacy.legacy_transactions` | `['option', <array>]` (explicit presence byte even at `legacy_request_id: 0`) | `['option', ['array', {container…}]]` | **Same wire layout** (option presence byte + inline array; inner element detail comes from newer data) |
| `Transaction.legacy` | `TransactionLegacyStandalone` (option form) | `TransactionLegacy` (already option form) | **Equivalent** — no separate standalone type needed upstream |
| Embedded `packet_player_auth_input.transaction.legacy` | `TransactionLegacyStandalone` | `TransactionLegacy` | **Equivalent** |
| Embedded `…transaction.actions` | `TransactionActions` (direct, not optional) | `TransactionActions` | **Identical** |
| Embedded `…transaction.data` | `TransactionUseItemAuthInput` (clone of `TransactionUseItem`) | `TransactionUseItem` (full 12-field form: `action_type, trigger_type, block_position, face, hotbar_slot, hand, held_item, player_pos, click_pos, block_runtime_id, client_prediction, client_cooldown_state`) | **Identical shape** — matches what `createUseTransaction` / `createBlockInteractionInput` send |
| `InputData` 2 / 53 | `north_jump` / `start_using_item` | same | **Identical** |
| `InputData` 34 / 35 | `perform_item_interaction` / `perform_block_actions` | **`item_interact` / `block_action`** | **Renamed, ordinals unchanged** — wire bytes identical; source/tests updated to new names |
| `Action` 28 | `start_item_use_on` | same | **Identical** |

`runtime/test/schema-patch.test.js` was rewritten to assert the above
directly against the installed upstream `protocol.json` (no mock, no patch
function). `runtime/test/packets.test.js` now serializes against `1.26.51`
with **all byte-level assertions kept verbatim** (`[0x1e, 0, 0, 2, 0]`
use/click_air, `[0x1e, 0, 0, 3, 0, 7, 2]` attack, drop inventory/world
balancing, release `head_pos`, block-interaction `click_pos`) — all pass,
proving the wire layout is preserved. No minimal patch was kept: none is needed.

## 6. Test results

- `npm run check --prefix runtime` (syntax check of `src/` + `scripts/`): **pass**.
- `npm test --prefix runtime`: **67/67 pass** (repeated runs; final three
  consecutive runs fully green).
- Two unrelated single-run flakes were observed (one each, never
  reproduced): `lifecycle.test.js` timing assertion (`reconnecting` vs
  `failed`) and `local-identity.test.js` (`concurrent processes converge on
  one persisted owner key`). Both are in timing/multi-process tests untouched
  by this spike.
- Test changes (no deletions-to-green; fork-only behaviour replaced):
  - `schema-patch.test.js`: rewritten as upstream-layout assertions (above).
  - `packets.test.js`: version `1.26.50` → `1.26.51`,
    `perform_item_interaction` → `item_interact`; test titles updated
    (`M2`/`pinned` wording removed); byte assertions untouched.
  - `protocol-session.test.js`: the two pin tests (symlink/persistent-artifact
    validation, corrupt-pin rejection) encoded fork-only behaviour and were
    **replaced** by `non-loopback servers are refused before client
    creation` (asserts `192.0.2.10`, `example.com`, `0.0.0.0` reject with
    `/loopback/` and zero `createClient` calls); `item_interact` rename;
    `connectedSession` helper drops the now-unused `serverIdentityPinPath`.

## 7. What could not be verified

- **No live BDS run** (per instructions — orchestrator does the real-server
  smoke). In particular, real NetherNet LAN discovery against the patched
  Endstone BDS on loopback, the WebRTC (`werift`) handshake, and the
  `a=identity` token admission path are untested here.
- Git-fetch hermeticity on a cold machine without `github.com` SSH access
  (lockfile uses `git+ssh://…`; only warm-cache/SSH-capable installs
  verified).
- `raknet-native` native build (its install script is policy-blocked and the
  module is unused on the NetherNet path).

## 8. Live-smoke checks the orchestrator must run

Against a real patched BDS on loopback, in order:

1. **Login**: bot spawns with the profile name; BDS log shows the local-bot
   identity accepted (not the Microsoft path); `spawn` event fires and
   `serverbound_loading_screen` completes.
2. **Movement / PlayerAuthInput**: walk forward 20 ticks; BDS position tracks
   predictions without rubber-banding; `correct_player_move_prediction`
   reconciles.
3. **Jump**: single tap launches once; no bunny-hop on landing.
4. **Sneak**: sustained sneak holds crouch; release stands (cooked + raw
   flag sequence).
5. **Interact / block placement**: `start_item_use_on` +
   `player_auth_input(item_interact, click_block)` + `stop_item_use_on`
   sequence places/breaks the targeted block as BDS authorizes.
6. **Drop**: single-item and whole-stack drops update BDS and the session
   cache consistently.
7. **Hotbar**: slot select re-announces equipment; server and session agree.
8. **Inventory**: `inventory_content`/`inventory_slot`/`player_hotbar`
   converge after pickup/drop.
9. **Reconnect**: kill the transport unexpectedly; bounded exponential
   reconnect restores the session without duplicates.
10. **Two concurrent bots**: two profiles online simultaneously, independent
    movement and block interaction, no session cross-talk.
11. **Negative**: `serverHost` outside loopback refuses to connect (already
    unit-tested; confirm end-to-end via CLI config).
