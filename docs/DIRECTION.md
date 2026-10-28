# Product direction: from one plugin to a small distribution

Status: **Accepted direction, not implemented** (2026-10-28 JST). This records decisions and their reasons; the work
items are BOT-019 to BOT-023 in the [backlog](BACKLOG.md). [Architecture](ARCHITECTURE.md) describes what exists today.

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

What does not scale is the plugin owning everything. Goals 4–6 need authorization, the script store, runner
orchestration, triggers and the web connection to keep working without depending on the BDS process (crash and tick
isolation, no internet-facing code inside the game server). The target is:

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
- Migration is incremental. The first step (BOT-019) pays off whichever process ends up hosting the logic.
- Only the bundled, managed install (`endbot setup`) is supported for users once endbotd exists; manual plugin
  installation stays documented for developers.
- Parallel, optional: propose a narrow login-verifier hook to upstream Endstone. If accepted, the patch and patched
  builds go away and Endbot becomes stock Endstone + plugin + sidecars. Security-sensitive; rejection is acceptable.

## Decision 2: scripts never write the world directly

Scripts may command Bots, read the world and wait for changes. They get no `setblock`, give, summon-by-command or
similar direct mutation. Every world change happens through a Bot acting like a player, so BDS stays authoritative,
`allow-cheats=false` and achievement compatibility keep their meaning, and Endbot stays a Bot automation tool rather than
a general server scripting platform. Bot lifecycle (spawn, despawn) is allowed: it is Endbot's own state.

## Decision 3: the web is hosted on Cloudflare Workers, deployed by each operator

The web is meant for the internet, so it is not served from the Minecraft PC. Each operator deploys the Worker to
**their own** Cloudflare account. The Worker code lives in this repository; no shared central service is run.

```text
browser ──HTTPS──▶ Worker (static UI, login, sessions)
                     │
                Durable Object (one per server; relay)
                     ▲
                     │ WebSocket opened outbound by the server PC
                endbotd ──▶ plugin / runtime
```

- The server PC opens no inbound port. Unauthenticated traffic stops at Cloudflare.
- Deployment and pairing: `endbot web deploy` runs a pinned wrangler with the bundle's Node, deploys the Worker to
  the operator's account, generates a pairing secret and stores it as a Worker secret. endbotd then pairs with its
  own key pair, whose private key stays on the PC (never in the repository). The Worker has no admin page.
- **endbotd remains the authority.** The Worker stores only passkey public keys and sessions. The link table (web
  account ↔ XUID) lives in endbotd, which decides every relayed request against its own controller policy, exactly
  like an in-game request. A compromised Worker can therefore misuse the sessions it relays, within those users'
  grants, but cannot claim other XUIDs or widen grants: controller grants and revocations stay console-only.
- Cloudflare holds the minimum: XUID, GamerTag, passkey public keys and sessions. Script source and operation logs
  are relayed, never stored there. Scripts stay on the PC; when the PC is offline the web shows it as offline.
  Mirroring to D1 can be added later if offline editing becomes necessary.
- Versions: the Worker and endbotd speak a versioned protocol. endbotd refuses a mismatched Worker and says so;
  `endbot update` redeploys the Worker (or tells the operator to run `endbot web deploy`).
- With BDS stopped, endbotd still runs, so the web can show status and logs and edit scripts. Starting and stopping
  BDS stays console-only, keeping the line that controllers are not operators.

## Decision 4: web accounts use passkeys and are linked to an XUID once, in game

The account and the identity proof are separate:

```text
web account (passkey login) ──link──▶ XUID (key of the existing controller policy)
```

Link flow (one game join, no Azure registration). Accounts can only be created from a code issued in game:

1. In game, run `/botweb link`. The plugin knows the player's XUID because BDS already validated their Xbox login.
   endbotd issues a one-time code (e.g. `K7Q-4M2`, valid for 5 minutes) and the chat says where to enter it and not
   to share it.
2. On the web, enter the code and register a passkey. The account is created already linked to that XUID.
3. Later logins use the passkey only; the game is not needed again.

There is no open sign-up and no unlinked account, so strangers cannot fill the Worker with accounts, and nobody can
link their own account to a controller by asking the controller to type a code. Someone who is given a controller's
code can still use it; the short lifetime, single use and the in-game warning limit that. The operator links the
same way, so no separate invite or admin bootstrap is needed.

Microsoft sign-in (OAuth → Xbox user token → XSTS, yielding XUID and GamerTag) is deferred, not rejected. Because each
operator deploys their own Worker, it would require every operator to register an Azure application and manage a
secret, and the claim set and terms must be verified first. It can be added later as another way to prove the link.

## Decision 5: scripts wait for changes; the host chooses events or polling

Scripts do not write "read the block, wait a few seconds, repeat" loops. They register a watch and wait on it:

```lua
local w = world.watchBlock(100, 64, 200)
while true do
  local change = w:wait()          -- yields until the block changes
  if change.to == "minecraft:air" then
    bot("Alice"):spawn()
  end
end
```

The name is illustrative; the final API follows the existing `api.luau` style.

- The blocking-wait shape fits the runner's yield model and reads top to bottom.
- Inside, the plugin uses Endstone events where they exist (player breaking and placing) and, for changes without an
  event (redstone, fluids, pistons — coverage to be measured), checks only the registered watched positions in one batched
  server-thread pass every N ticks. That is far cheaper than many scripts each polling over IPC, it does not consume the
  script's operation budget, and it does not miss short changes as easily.
- The documented guarantee is "a change that persists for at least N ticks is delivered", with N stated.
- Manual polling with `block()` and `wait()` keeps working as an escape hatch.
- First a running script loops on `wait()`. Persistent triggers that survive restarts come later (BOT-022).
- Watches are owned by the script's principal, bounded per controller and server, and end when that grant is revoked.

## Delivery

No release numbers are assigned in advance. The order is fixed by dependency, and main stays releasable at each
checkpoint; whether a checkpoint ships on its own or together with later ones is decided when it is reached.

1. BOT-019 (Operation API, principals) with the agreed waits (BOT-016, interface guide D2).
2. BOT-020 (endbotd). It moves the policy and script stores, so `endbot update` and `--rollback` must carry the
   migration in both directions.
3. BOT-021 (web), then BOT-022 (watches, then triggers).

However checkpoints are bundled, a release includes their manual validation gates; bundling more makes one larger
validation pass, not a shorter one.
