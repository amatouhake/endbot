# Product direction: from one plugin to a small distribution

Status: **Accepted direction, not implemented** (2026-10-28 JST). This records decisions and their reasons; the work
items are BOT-019 to BOT-024 in the [backlog](BACKLOG.md). [Architecture](ARCHITECTURE.md) describes what exists today.

## Goals

1. Control Bots from in game.
2. Control Bots with scripts from in game.
3. Edit scripts in game.
4. Do all three from the web as well.
5. Use the web without joining the game (after a one-time link, see below).
6. Let scripts react to things other than Bots, such as a watched block changing.

## Decision 1: Endbot is a distribution, and its centre moves out of BDS

Endbot cannot be a single plugin: the local bot authentication seam is below what a plugin can reach, so a patched
Endstone, BDS, the Bedrock runtime and the Luau runner must be installed and started as a matched set. The CLI and
supervisor are the consequence of that, not an accident.

What does not scale is the plugin owning everything. Authorization, the script store, runner orchestration, triggers and
the web connection belong outside the BDS process even before the web exists:

- A BDS crash or restart takes run state and armed triggers with it; persistent triggers need a home that outlives BDS.
- Policy decisions, script storage and log writing have no reason to run next to the game's tick thread.
- The plugin can only use what loads in Endstone's Python environment. The web needs WebSocket, WebAuthn and
  cryptography libraries, which endbotd can pin and bundle in its own environment.
- Connecting the web to the plugin first and moving it later would build the relay endpoint and its authorization
  twice. BOT-019 makes the logic process-neutral, but it does not remove that rework.
- No internet-facing code runs inside the game server.

That the web keeps working while BDS is stopped follows from this; it is a benefit, not the reason. The target is:

```text
in-game UI (commands, forms, books)         web UI (Cloudflare Worker, see Decision 3)
                 │                                       │ relayed over the outbound link
                 ▼                                       ▼
  endbotd — grown from today's supervisor, outside BDS
    one Operation API, principals (player XUID, web account, console, trigger),
    authorization, script store and revisions, Luau runner orchestration,
    watches/triggers, operation log, process supervision, outbound web link
                 │ loopback                              │ loopback
                 ▼                                       ▼
  Endbot plugin (thin world adapter)              Node runtime (unchanged role)
    renders in-game UI and forwards it              Bot sessions and inputs
    world observation, teleport, block reads
    event source, filtered by registered watches
                 │
  patched Endstone (local bot auth seam only, unchanged)
```

- endbotd is Python so that the plugin's pure, fake-tested logic (authorization, controller policy, script store,
  groups, operations, i18n) moves rather than being rewritten.
- The internal plugin/runtime protocol stays loopback-only local IPC. The web is a separate trust boundary.
- Only the plugin knows who is online, so it reports presence to endbotd, which applies the in-game online condition
  from those reports. While endbotd is down, in-game Bot commands are refused with a message saying why; the supervisor
  starts both, so this is the exception, not a degraded mode to design for.
- Migration is incremental. The first step (BOT-019) pays off whichever process ends up hosting the logic.
- Only the bundled, managed install (`endbot setup`) is supported for users once endbotd exists; manual plugin
  installation stays documented for developers.
- Parallel, optional: propose a narrow login-verifier hook to upstream Endstone. If accepted, the patch and patched
  builds go away and Endbot becomes stock Endstone + plugin + sidecars. Security-sensitive; rejection is acceptable.

## Decision 2: scripts never write the world directly

Scripts may command Bots, read the world and wait for changes. They get no `setblock`, give, summon-by-command or
similar direct mutation. Every world change happens through a Bot acting like a player, so BDS stays authoritative,
`allow-cheats=false` and achievement compatibility keep their meaning, and Endbot stays a Bot automation tool rather
than a general server scripting platform. Bot lifecycle (spawn, despawn) is allowed: it is Endbot's own state.

## Decision 3: the web is hosted on Cloudflare Workers, deployed by each operator

The web is meant for the internet, so it is not served from the Minecraft PC. Each operator deploys the Worker to
**their own** Cloudflare account. The Worker code lives in this repository; no shared central service is run.

```text
browser ──HTTPS──▶ Worker (static UI, cookie, relay)
                     │
                Durable Object (one per server; relay)
                     ▲
                     │ WebSocket opened outbound by the server PC
                endbotd ──▶ plugin / runtime
```

- The server PC opens no inbound port. Unauthenticated traffic stops at Cloudflare.
- Deployment and pairing: `endbot web deploy` uploads the prebuilt Worker to the operator's account through the
  Cloudflare API, generates a pairing secret and stores it as a Worker secret. endbotd then pairs with its
  own key pair, whose private key stays on the PC (never in the repository). The Worker has no admin page.
- **endbotd is the authority, for authentication as well as authorization.** endbotd keeps the link table (web account
  ↔ XUID), the passkey public keys and the sessions. It issues WebAuthn challenges, verifies passkey assertions and
  issues session tokens; the Worker only carries them, keeping the token in an HttpOnly cookie. endbotd decides every
  relayed request against its own permission model, exactly like an in-game request. Grants and revocations stay
  console-only.
- Why, given that Cloudflare itself is unlikely to be breached: the realistic risks are a bug in the Worker's
  authentication code and a compromised dependency or build. If the Worker verified passkeys, either would let it act
  as every linked account at any time. With endbotd verifying, a compromised Worker can still misuse the browsers that
  visit it while it is compromised, but not the other accounts, and it can never link a new XUID or widen a role. The
  bigger gain is simplicity: authentication and authorization sit in one place, and the Durable Object holds no
  accounts. Signing in needs the PC online, which costs nothing, since an offline PC has nothing to sign in to.
- Cloudflare holds nothing that needs protecting: the static UI and, at most, a display cache (server name, online or
  offline). Script source and operation logs are relayed, never stored there. Scripts stay on the PC; when the PC is
  offline the web shows it as offline. Mirroring to D1 can be added later if offline editing becomes necessary.
- Versions: the Worker and endbotd speak a versioned protocol. endbotd refuses a mismatched Worker and says so;
  `endbot update` redeploys the Worker (or tells the operator to run `endbot web deploy`).
- With BDS stopped, endbotd still runs, so the web can show status and logs and edit scripts. Starting and stopping
  BDS stays console-only, keeping the line that controllers are not operators.

### Web technology

| Part | Choice | Notes |
| --- | --- | --- |
| Language | TypeScript | Types generated from the operation registry; build only in CI and development |
| Worker routing | Hono | Small and Workers-native |
| Relay and state | One Durable Object per server, WebSocket Hibernation API | Holds endbotd's outbound connection and pushes live updates to browsers |
| Storage | endbotd's own store; the Durable Object keeps at most a display cache | Accounts, passkeys and sessions live on the PC; no D1 |
| Passkeys | SimpleWebAuthn browser package; a pinned, bundled Python WebAuthn library in endbotd | endbotd issues challenges and verifies assertions |
| Sessions | HttpOnly, Secure, `SameSite=Strict` cookie plus Origin checks | The cookie carries an endbotd-issued token; session state lives in endbotd |
| UI | Svelte 5 + Vite single-page app, served as Workers static assets | No SvelteKit |
| Script editor | CodeMirror 6 | Light and usable on phones; Luau highlighting defined in-house; completion from the registry and `api.luau` |
| Luau diagnostics | Compile results from endbotd at first | In-browser Luau compiled to WASM may come later; no luau-lsp |
| Messages | The shared message catalog from BOT-019 | Same wording as chat |
| endbotd side | Python `websockets`, pinned and bundled | |
| Protocol | JSON over WebSocket with a protocol version and request IDs | endbotd proves itself with its paired key on connect |
| Tests | Vitest with `@cloudflare/vitest-pool-workers`; Playwright for end-to-end | |
| Build and deploy | CI builds the Worker; release bundles carry it prebuilt | `endbot web deploy` calls the Cloudflare API directly (module upload, Durable Object migrations, secrets, static assets). Confirm the static-asset upload works through the API alone; fall back to a pinned wrangler if it does not |

Dependencies are pinned with a lockfile, like the other parts. Operators need no Node, npm or build step.

### Other hosts

Because endbotd owns authentication, the browser ↔ endbotd protocol, and the Worker is a thin static host and relay,
Cloudflare is one way to put the web on the internet rather than where the web lives.

| Option | Status | Notes |
| --- | --- | --- |
| Cloudflare Worker | The supported internet path | As above |
| Localhost mode | Planned after the web (BOT-021) | endbotd serves the same UI on `127.0.0.1` and speaks the same protocol directly, with no relay. Little new code; useful to operators who do not want Cloudflare and for development and end-to-end tests without a Cloudflare account. `localhost` is a secure context, so passkeys work |
| LAN access | Documented, not supported | Passkeys need a secure context, so `http://192.168.x.x` does not work. An operator may put localhost mode behind their own TLS reverse proxy |
| Other deployment targets (a VPS, another cloud, a self-hosted relay) | Not planned | The relay is small, but each target needs its own deployment, `endbot update` integration, tests and docs. The protocol is documented so one can be added; revisit on a concrete request |
| Cloudflare Tunnel to localhost mode | Not adopted | Removes the Worker code, but unauthenticated internet traffic would reach the PC, losing the property that it stops at Cloudflare |

## Decision 4: web accounts use passkeys and are linked to an XUID once, in game

One XUID has at most one web account; the account holds any number of passkeys and sessions:

```text
XUID (GamerTag) ── web account ──┬─ passkey (PC)
                                 ├─ passkey (phone)
                                 └─ sessions, one per signed-in device
```

Link flow (one game join, no Azure registration). Accounts can only be created from a code issued in game:

1. In game, run `/botweb link`. The plugin knows the player's XUID because BDS already validated their Xbox login.
   endbotd issues a one-time code (e.g. `K7Q-4M2`, valid for 5 minutes) and the chat says where to enter it and not
   to share it.
2. On the web, enter the code and register a passkey. The account is created already linked to that XUID.
3. Later logins use the passkey only; the game is not needed again.

There is no open sign-up and no unlinked account, so strangers cannot fill endbotd's account store, and nobody can
link their own account to a controller by asking the controller to type a code. Someone who is given a controller's
code can still use it; the short lifetime, single use, the in-game warning and new-device notices limit that. The
operator links the same way, so no separate invite or admin bootstrap is needed. Only XUIDs that hold a controller
grant get a web account: `/botweb link` is refused for anyone else, and so is every other way of proving the link.

More devices:

- Synced passkeys (iCloud Keychain, Google Password Manager and similar) need nothing extra.
- Standard cross-device sign-in (scanning the login page's QR code with a phone) works across ecosystems.
- "Add this device" on a signed-in device shows a short one-time code to enter on the new device, which then
  registers its own passkey. No game join is needed.
- `/botweb link` for an XUID that already has an account adds a passkey to it instead of creating another account.
  This is also the recovery path after losing every device.
- Adding a passkey notifies the account's other devices and, when online, the player in game.

Removing access:

| Who | Where | What |
| --- | --- | --- |
| The player | Web device list | Remove one passkey or session |
| The player | `/botweb unlink` in game | Delete the account and end every session |
| The player | `/botweb status` in game | List passkeys and last sign-ins |
| The operator | `endbot web revoke <GamerTag>` at the console | Delete the account and end every session |
| Automatic | Controller revocation | Ends every web session at once |

Optional: a trusted external site (off by default). An operator who already runs a site where players sign in, for
example with their own Microsoft OAuth, can let that site prove the link instead of the in-game code:

```text
operator's site (knows the user's XUID)
   │ signed, short-lived assertion:
   │ { sub: XUID, gtg: GamerTag, aud: this server, exp: about 60 s, jti: single use }
   ▼
Worker ──relays──▶ endbotd: verifies with the site's public key from endbot.toml, rejects reused jti
                            → creates or links the account, or signs the user in
```

- The operator generates a key pair, registers the public key in `endbot.toml` and adds a few lines to their site that
  sign the assertion and hand it to the Worker with an auto-submitted form POST, never in a query string, so it does
  not land in browser history or access logs. The ES384 signed-token form mirrors the existing local Bot
  authentication.
- endbotd refuses an assertion for an XUID without a controller grant, as it refuses `/botweb link`.
- The operator chooses whether the site only links (later logins use passkeys) or is also the sign-in (SSO, no
  passkey).
- Risk: whoever controls that site can claim any XUID. The operator opts into that trust explicitly; controller
  grants stay console-only, so no grant can be widened.
- With the site enabled, removing the account does not keep anyone out: the site can vouch again at the next sign-in.
  That is fine for the player's own `/botweb unlink`. For the operator, `endbot web revoke` only ends the current
  account and sessions; to keep someone out, revoke their controller grant, or have the site stop vouching for them.
- Operators who want Microsoft sign-in can plug in their own this way, which further reduces the need for Endbot to
  carry it.
- Site-to-site API access (for example showing Bot status inside the operator's site) is a larger, later step that
  the Operation API (BOT-019) would make possible; it is not planned now.

Microsoft sign-in (OAuth → Xbox user token → XSTS, yielding XUID and GamerTag) is deferred, not rejected; it can be
added later as another way to prove the link. It is not the primary method because:

- The Worker, not endbotd, would learn the XUID, so endbotd would have to trust the Worker's claim and a compromised
  Worker could claim any XUID. Avoiding that needs endbotd to re-verify the token against Xbox Live itself.
- Each operator deploys their own Worker, so each would have to register an Entra application and manage a secret,
  probably with an Azure tenant (to be verified).
- The Xbox token exchange is used by launchers but is not a documented third-party API; changes and terms are risks.
- Its clear advantages, never joining the game and no device management, are not required: one join is accepted and
  the device flows above cover several devices.

Revisit this if a single shared service replaces per-operator Workers (one registration would then suffice), if never
joining the game becomes a requirement, or if passkeys turn out to block many users in practice.

## Decision 5: events are a registry-defined framework; scripts wait, triggers start runs

Events will keep being requested (a player did something, the server started, a chat message arrived, ...). Instead
of designing each one, Endbot defines how any event is added and consumed.

**Event kinds live in the same JSON registry as operations.** Names are `category.action`: `player.join`, `player.chat`,
`block.change`, `bot.died`, `server.started`. Each kind declares its filter fields (name, position or area, block type,
...), its payload shape, its source, its delivery guarantee and its visibility (see who may observe what, below). Adding
a kind means one source adapter and one registry entry; Luau completion, the web trigger editor and the docs follow from
the registry, as the [interface guide](INTERFACE_GUIDE.md) requires.

| Source | Examples |
| --- | --- |
| Plugin (in game) | Block changed, broken or placed; player joined, left, chatted, died; in-game time (day and night) |
| Runtime and Bots | Bot online or offline, Bot died, job finished |
| endbotd | Server started or stopped (it supervises BDS), wall-clock schedules, script run finished |

**Scripts wait on a watch.** One generic form, not one function per kind:

```lua
params {}
local w = watch("block.change", {x = 100, y = 64, z = 200})
while true do
  local r = w:wait{timeout = 1200} -- yields until the block changes or the timeout; common result shape
  if r.ok and r.data.to == "minecraft:air" then
    bot("Alice"):spawn()
  end
end
```

- The blocking-wait shape fits the runner's yield model and reads top to bottom.
- Sources filter at the origin. For `block.change` the plugin uses Endstone events where they exist (player breaking and
  placing) and, for changes without an event (redstone, fluids, pistons — coverage to be measured), checks only the
  watched positions in one batched server-thread pass every N ticks. That is far cheaper than scripts polling over IPC,
  does not consume the operation budget, and misses fewer short changes. Its guarantee is "a change that persists for at
  least N ticks is delivered", with N stated; every kind states its own guarantee.
- Manual polling with `block()` and `wait()` keeps working as an escape hatch.
- Watches are owned by the run's principal, bounded per controller and server, and end when that grant is revoked.

**Events are observation only.** Many Endstone events can be cancelled or modified by plugins; scripts get neither.
Cancelling a block break would be a world write and would bypass Decision 2. Events are delivered after the fact, so
slow handling never holds a game tick.

**Triggers are separate bindings, not declarations inside scripts:**

```text
trigger = { event kind + filter → script (+ arguments), owner, enabled, policy }
```

- The script declares the event as a parameter in `params{}` (for example `param.event("event")`) and reads it from
  the returned arguments (`a.event`), so the same script can run by hand or from a trigger.
- Triggers can be created without code from the web and the in-game forms, and managed with
  `/botscript trigger add|list|remove`.
- `watch():wait()` stays for waiting inside a run; a trigger is what starts a run.
- Policy, against runaway chains (a Bot breaks a block, which fires the trigger that made it break blocks):
  concurrency defaults to "skip while a run from this trigger is active" (alternatives: queue one, run in parallel);
  a cooldown; a maximum number of starts per minute, beyond which the trigger disables itself and notifies its owner;
  a bounded event queue that drops the oldest and reports a `missed` count.
- Triggers persist in endbotd and survive restarts (after BOT-020).

**First kinds**, then more on request: `block.change`; `bot.online`, `bot.offline`, `bot.died`, `job.finished`;
`player.join`, `player.leave`, `player.chat`; `server.started`; `schedule` (wall-clock) and `world.time` (in-game day
and night). What Endstone events actually cover is measured on the locked pair before a kind is listed.

**Who may observe what** follows the [permission model](PERMISSIONS.md#observation): what an ordinary player in game
can see (joins, leaves, public chat, death messages) is available and declared by scripts; other players' positions
and inventories are not provided; direct messages never are. Triggers are approved per script revision and bounded by
limits ([triggers](PERMISSIONS.md#triggers)).

## Delivery

No release numbers are assigned in advance. The order is fixed by dependency, and main stays releasable at each
checkpoint; whether a checkpoint ships on its own or together with later ones is decided when it is reached.

1. BOT-019 (Operation API, principals), which changes nothing players see, then the agreed waits (BOT-016, interface
   guide D2) as a separate step in the same checkpoint; the waits are the new Luau API. Reserved Bot names (BOT-025)
   land here too.
2. BOT-020 (endbotd). It moves the policy and script stores, so `endbot update` and `--rollback` must carry the
   migration in both directions.
3. BOT-024 (permission model: roles, ownership, script declarations), before anything reaches the web or runs
   unattended.
4. BOT-021 (web), then BOT-022 (watches first; persistent triggers need step 2). Localhost mode follows the web.

Decided 2026-10-29 JST: these four steps (BOT-020, BOT-024, BOT-021, BOT-022) ship together as one release once all
of them are done, with one validation pass; no checkpoint in between is released on its own.

However checkpoints are bundled, a release includes their manual validation gates; bundling more makes one larger
validation pass, not a shorter one.
