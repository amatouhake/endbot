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
                 │                                       │
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
- Pairing: the Worker admin page shows a one-time code; `endbot web pair <code>` creates a server key pair, keeps the
  private key on the PC (never in the repository) and registers the public key with the Durable Object.
- **endbotd remains the authority.** The Worker relays "web account A, linked to XUID X, requests operation O";
  endbotd evaluates it against its own controller policy, exactly like an in-game request. A compromised Worker can
  at most act as linked users within their grants.
- Controller grants and revocations stay console-only at first.
- Scripts stay stored on the PC. When the PC is offline the web shows it as offline. Mirroring to D1 can be added
  later if offline editing becomes necessary.

## Decision 4: web accounts use passkeys and are linked to an XUID once, in game

The account and the identity proof are separate:

```text
web account (passkey login) ──link──▶ XUID (key of the existing controller policy)
```

Primary link flow (one game join, no Azure registration):

1. On the web, create an account by registering a passkey. The page shows a short code, e.g. `K7Q-4M2`.
2. In game, run `/botweb link K7Q-4M2`.
3. The plugin knows the player's XUID because BDS already validated their Xbox login; endbotd tells the Worker that
   the code belongs to that XUID.
4. Done. Later logins use the passkey only; the game is not needed again.

Bootstrap for the operator: `endbot web invite --admin` on the PC prints a one-time URL for the first account. Its
XUID link still follows the in-game flow.

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
    bot.spawn("Alice")
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
