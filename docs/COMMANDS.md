# Endbot commands and operation

## Quick start

Every command names its Bot; there is no selected-target state.

```text
/bot Alice spawn
/bot Alice tp me
/bot Alice jump
/bot Alice attack continuous
/bot Alice stop
/bot Alice despawn
```

For players, `/bot` opens the Bot list, just like `/bot menu`. At the server console, `/bot` still shows help page 1.
`/bot help` always shows help page 1. `/bot help <page>` (1-3) shows a paginated command summary modeled on
vanilla Bedrock `/help`, and `/bot help <operation>` (for example `/bot help move`) shows every supported
syntax for that operation with a concise explanation. `/bot list` summarizes every known profile and
its lifecycle state. `/bot Alice status` combines runtime state with authoritative dimension/position/rotation when the
BDS player entity is present.

```text
--- Endbot help page 1 of 3: lifecycle, status and teleport ---
/bot ping | /bot list | /bot help - connectivity, profiles, this help
/bot menu - open the Bot control forms (players only)
/bot <name> spawn - create or connect a Bot
/bot <name> resume - reconnect a profile without moving it
...
Next page: /bot help 2 - detail: /bot help <command>
```

```text
Endbot help: move
/bot <name> move forward|backward|left|right|stop
Movement acts as held input; stop halts it.
```

Page 1 covers lifecycle, status, and teleport; page 2 covers movement and actions; page 3 covers
inventory, world interaction, and advanced lifecycle. Each page is one chat message per line so it
reads in Bedrock chat without dense pipe-separated one-liners.

## Command results

Each operation reports its Bot and the applied input/change, rather than only its connection state. For example:

```text
/bot Alice move forward       -> Endbot: Alice: movement set to forward
/bot Alice jump               -> Endbot: Alice: jump once requested
/bot Alice use continuous     -> Endbot: Alice: use continuous started
/bot Alice stop               -> Endbot: Alice: movement and actions stopped (sprint/sneak off)
/bot Alice hotbar 2           -> Endbot: Alice: hotbar slot 2 selected
/bot Alice move sideways      -> Endbot: Alice: Unknown or invalid Bot action: move
```

Every reply about a Bot starts with `Endbot: <Bot>: `, including errors and syntax errors. Look replies include
yaw/pitch, interaction replies include block/face, and rename/lifecycle replies identify the requested change. An
operation error names the action, for example `Endbot: Alice: move failed: Bot is offline`. These replies confirm input application or request
acceptance; BDS still decides collision, item consumption, reach and other world outcomes. Use `status` and actual world
observations to check the outcome. Connection lifecycle remains asynchronous (`connecting` is not a completed spawn).

## Control forms

Players can run the common operations from native Bedrock forms instead of typing them:

```text
/bot               -> Bot list (players); help page 1 (console)
/bot menu          -> Bot list (name and connection state), plus "Spawn a new Bot here"
/bot Alice menu    -> Alice's actions and compact state/position summary
```

The Bot list shows online Bots by name; connecting, retrying and failed Bots carry a dark-colored `JOIN`, `RETRY` or
`FAIL` prefix, and offline Bots are behind one `Offline (n)...` button. The online Bot screen has Stop everything,
Movement (forward, backward, left, right, jump, sprint / sneak), Look, Teleport, Gameplay (attack, use item, destroy),
Inventory (hotbar, drop) and Bot (reconnect, despawn with a confirmation, radius and the full status text). Group names
follow Minecraft's control settings. An offline Bot offers resume/spawn and radius.
Input forms use dropdowns, sliders and text fields; coordinate fields accept the same `~` forms as the command, and their
labels say what `~` is relative to. Submitting closes the form and the normal chat reply appears; run `/bot` again to
continue.

Forms are only another way to type commands: every button or form submission becomes the equivalent `/bot` arguments
(for example `/bot Alice attack interval 40`) and goes through the same permission check, command queue, parser and
replies, so a typed command and a form submission always behave identically. A controller whose permission is revoked
while a form is open gets the usual permission reply on submit. The server console cannot open forms (`/bot menu` replies
with a pointer to `/bot help`). `spawn at ...`, `interact`, `rename`, `forget`, `tp ... facing ...` and help
detail pages stay command-only.

Unit tests cover screen contents, every form response mapping to parser-accepted arguments, malformed responses, and
view delivery/revocation. Rendering and input on a real client are a manual check: on a normal Microsoft/Xbox client,
open `/bot`, `/bot menu` and `/bot <name> menu`, submit each input form once, cancel the despawn confirmation, close forms with
Escape, and confirm a revoked controller cannot act from an already open form.

## Mining blocks

```text
/bot Alice hotbar 1
/bot Alice destroy
/bot Alice destroy once
/bot Alice destroy continuous
/bot Alice destroy 100 64 -20
/bot Alice destroy ~ ~-1 ~ up
/bot Alice destroy stop
/bot Alice status
```

`destroy` takes the same mode words as `jump`, `attack` and `use`: `once` (the default), `continuous` and `stop`.
There is no `interval` mode, because `continuous` already waits for the next block.

`destroy` (or `destroy once`) starts holding left-click on one block in the Bot's current line of sight, within 5 blocks. Explicit coordinates
target one reachable, unobstructed block and turn the Bot toward it; an optional face uses the same names as `interact`.
Relative coordinates are measured from the Bot, including commands from the server console. Movement, attack and item
use inputs are cleared on start. Select the desired tool with `hotbar` first; mining does not walk or select tools.

`destroy continuous` holds left-click along the Bot's current sight direction. After each BDS target update it selects the
next reachable block along that same ray. When there is no target within 5 blocks (or bedrock is in front), it waits;
placing a reachable block in sight resumes mining automatically. It stays in place and does not excavate a chosen area.
Use `destroy stop` to disable this waiting/resume behavior as well as an active break attempt.

BDS performs ordinary Survival breaking, including hardness/tool speed, harvest drops, durability and cancellable
`BlockBreakEvent` handling. The plugin never sets the mined block to air, dispatches `/setblock`, or grants operator
permissions. The pinned baseline uses server-authoritative block breaking; other modes are rejected explicitly.

The initial reply names the target, for example `Endbot: Alice: destroying minecraft:stone at 1 64 2; see status for the
result`. No completion message is sent; `status` shows the mining state in player-facing words:

```text
destroying minecraft:stone at 1 64 2                                (breaking now)
destroy continuous waiting for a block in sight (last: ...)        (destroy continuous, nothing in reach)
last destroy: block broken or changed (minecraft:stone at 1 64 2)  (finished)
last destroy: failed: target out of reach (...)                    (failed)
last destroy: canceled by teleport (...)                           (canceled)
```

A finished job stays visible as `last destroy` until the next one starts. Single-block mining ends when BDS sends a changed
target block, so a replacement or another player's change also ends that task. Continuous mining selects again. The runtime
waits for server updates instead of predicting instant destruction or loot. A rejected/protected target may remain active
until stopped or until the 12000-input-tick timeout (about ten minutes at 20 TPS).

Mining continues through other operations. `move`, `look`, `attack`, `use`, `drop` and `interact` do not cancel it;
a continuous loop follows the new sight direction for its next block. Changing the hotbar slot or the held item (a
pickup or a broken tool) restarts the current block with the new item. A teleport, a dimension change, a target that
moves out of reach, or the 10-minute timeout ends the current block; single-block mining ends there, while continuous
mining selects its next target. Attacking or using an item while mining sends both inputs; BDS decides the outcome.

Mining ends only on `destroy stop` (mining only), `stop` (mining and all movement/actions), death, disconnect, revocation of
the starting controller's permission, or plugin disable. An expiring runtime lease also ends it if the plugin/control
worker stops responding (200 input ticks, about ten seconds). Continuous mining keeps running after its controller logs
off. Active mining is not saved or resumed on reconnect.

Start and stop are confirmed by the command reply and logged on the server console at info level. When mining ends for
any other reason (a failed single block, death, disconnect, revocation, a lost runtime), the starting controller gets
one chat message, for example `Endbot: Alice: destroy continuous stopped: canceled by death`. The console log records
the same message at warning level, also for controllers who are offline. Successfully broken blocks are not announced.
Sight selection currently uses occupied block cells (ignoring air/liquid); complex partial-block shapes are conservative.

See [mining validation](MINING_VALIDATION.md) for the exact real-server coverage. `attack` remains entity attack; use
`destroy` for blocks. The name follows the game's "Attack/Destroy" control.

## Managing access

`endbot controllers add|remove|list` waits for and prints the plugin result. The list includes effective permission and
reason; `endbot doctor` inspects saved authorization and `endbot doctor --live` queries the running plugin.

Bot access can be changed without restarting BDS from the host CLI with `endbot controllers add <GamerTag>`,
`endbot controllers remove <GamerTag>`, and `endbot controllers list`. These submit the console-only
`botcontrollers` commands and persist grants/revocations across restart. They do not grant operator permissions.
See [runtime authorization](OPERATIONS.md#runtime-grants-and-revocations) for offline enrollment, precedence and queued
command behavior.

## Lifecycle

```text
/bot <name> spawn
/bot <name> spawn at <x> <y> <z>
/bot <name> spawn at <x> <y> <z> facing <yaw> <pitch>
/bot <name> spawn at <x> <y> <z> in <dimension>
/bot <name> resume
/bot <name> reconnect
/bot <name> despawn
/bot <name> forget
/bot <name> rename <new-name>
```

`spawn` creates a profile if necessary and places the arriving UUID at the caller by default. From the server console,
`spawn` without coordinates leaves placement to BDS: a new Bot appears at the world spawn point and a known Bot where it
last was; `spawn at <x> <y> <z> in <dimension>` places it explicitly. Spawning an existing offline profile reuses its UUID but applies the requested
placement. `resume` reconnects an existing profile without relocation, allowing BDS native player persistence to win.
`reconnect` tears down and reconnects without changing identity or location. `despawn` is intentional offline state and
cancels automatic reconnect. `forget` requires an offline profile and removes only Endbot registration; it does not
purge BDS player data. A live rename preserves UUID and reconnects automatically so the login name changes.

If a requested spawn never reaches the world, its pending placement is discarded by resume, reconnect, despawn,
forget, or rename. Those operations therefore never consume an abandoned spawn placement later.

Spawn resolves coordinates and verifies the destination dimension before creating or starting a profile. A first spawn
also rejects an active real-player name; an existing Bot may reuse its own UUID/name. Invalid placement therefore has no
lifecycle side effect.

Names use 1–16 ASCII letters, digits, or underscores. Collisions are case-insensitive. The plugin rejects names of
online real players. Endstone does not expose a reliable complete offline GamerTag-history index, so an operator must
avoid known offline real-player names.

## Teleport

```text
/bot Alice tp me
/bot Alice tp Steve
/bot Alice tp 100 64 -20
/bot Alice tp 100 64 -20 90 0
/bot Alice tp 100 64 -20 facing Steve
/bot Alice tp 100 64 -20 facing 100 70 -10
/bot Alice tp in nether 100 64 -20
```

`overworld`, `nether`, and `end` map to their Minecraft namespaced IDs; a raw namespaced ID is also accepted. `~`
relative destination and `facing` coordinates are both resolved from the same command-source snapshot. Teleport uses
`Actor.teleport`; it never dispatches vanilla `/tp` and does not require cheat-command permission.

After a same-dimension server teleport, the runtime acknowledges the correction and resumes normal client-predicted
vertical movement. A Bot teleported into unsupported air therefore falls and lands instead of continually resending
the teleport height.

Dimension changes now reset old movement prediction and entity targets, acknowledge the dimension/loading transition,
and request the configured chunk radius. Server motion is incorporated into movement prediction for knockback.
The reported cross-dimension symptoms remain subject to the real-server and multi-player checks in
[BOT-002](BACKLOG.md#bot-002-restore-physics-movement-and-position-synchronization-after-dimension-changes).

## Chunk loading range

```text
/bot Idle radius 5
/bot Explorer radius 12
/bot Explorer radius
/bot Explorer radius default
```

`radius` accepts 2..32 chunks. Without a Bot override, `[runtime].chunk-radius` in `endbot.toml` is used (default 5).
Setting a radius affects only the named Bot and sends a new request immediately while online, without reconnecting.
Registered offline Bots can also be configured. The override survives reconnect, rename, and runtime restart;
`default` removes it and resumes inheritance from the shared setting. Query with `radius` or `status` to see the
requested value, its source and the server's answer, for example
`Endbot: Alice: chunk radius 12 (Bot override); server confirmed 10`. Right after a change the server has not answered
yet, so the reply says `server confirmation pending`; query again to see the confirmed range. An offline Bot shows
`applies when online`.

Use a smaller range for stationary Bots and a larger request for exploring Bots when appropriate. BDS can clamp either
request: the current baseline raised a request for 3 to 5. This setting does not change server simulation distance and
does not itself move a Bot or guarantee terrain generation across an area. See [operator configuration](OPERATIONS.md).

## Inputs and actions

```text
/bot Alice move forward|backward|left|right|stop
/bot Alice look <yaw> <pitch>
/bot Alice look at <x> <y> <z>
/bot Alice jump|attack|use [once|continuous|interval <ticks>|stop]
/bot Alice sprint on|off
/bot Alice sneak on|off
/bot Alice hotbar
/bot Alice hotbar <1-9>
/bot Alice interact <x> <y> <z> <down|up|north|south|west|east>
/bot Alice drop
/bot Alice drop stack
/bot Alice stop
```

Omitting an action mode means `once`. Continuous actions run each 20 Hz input tick; interval actions accept 1–72000
ticks. Movement behaves as held input. Global `stop` clears movement, scheduled actions, sprint, and sneak while keeping
the session connected. `look` remains at its last rotation after stop. `use` sends the held item's normal Bedrock
interaction; with an empty hand it is a safe no-op. Endbot does not invent an item or issue a server command.
Sprint and sneak emit Bedrock's one-tick start/stop transitions as well as steady held-state flags; reconnect input reset
does not replay transitions from the previous transport.

`hotbar` reports or changes the selected slot using human-facing 1–9 numbering. Selection is sent through the normal
Bedrock equipment path and inventory updates continue to come from BDS; Endbot does not keep a second inventory.
BDS does not push every pickup's inventory delta to the picker. The Endbot plugin therefore reads the nine actual BDS
hotbar slots every four server ticks and sends a read-only observation over that Bot's existing connection. The runtime
updates its transient cache, including counts and typed NBT, and re-announces a changed held item through normal Bedrock
equipment packets. This makes same-session pickups usable by `hotbar`, `drop` and held-item actions without reconnecting,
normally within four server ticks (about 0.2 seconds at 20 TPS). No item is added, removed or reassigned by the observer.
`drop` still uses a normal BDS-validated transaction; the reply says requested, not completed. Fresh observations also
correct an optimistic drop if BDS rejected it. Unknown/invalid observations are ignored as a whole; an unknown or empty
selected slot still fails with `Selected hotbar slot is empty`. Without the matching plugin, reconnect remains the
recovery for missing native deltas. See [pickup validation](INVENTORY_VALIDATION.md).

`use continuous` holds right-click: its first input tick sends the initial air-use interaction, and later ticks send
simulation-tick interactions in the same authoritative input stream. `use stop` or global `stop` releases it, for example
to throw a charged trident or shoot a bow. Food use requires vanilla eating conditions. `use once` and `use interval`
remain short press/release actions; they do not hold long enough to eat or fully charge an item. Switching hotbar slots
also releases the previous item. Real item outcomes and dimension-transition behavior are tracked in
[the backlog](BACKLOG.md); historical generic use/release smoke does not cover every item.

`use` means using or holding the selected item in the air, such as food, a bow, or a trident. `interact` is a normal
right-click on the named block face. It sends the protocol-2193 press, server-authoritative item-interaction tick, and
release sequence; BDS decides reach, legality, collision, placement, and inventory decrement. A placeable selected
item can therefore place a block in Survival without the plugin editing the world. `drop` drops one selected item and
`drop stack` drops the selected stack through normal inventory transactions, operating on the runtime-known selected
slot (see the `hotbar` pickup-sync note above).

The command declaration separates parseability from completion because live BDS overload matching proved three
rules (verified with a probe plugin dispatching 42 documented forms through the real `compileCommand` path):
trailing optional parameters never consume input; `string` rejects numeric tokens; and parameters positioned
after an enum never match. A tail consumer must therefore be a required `message` with no enum before it, while
suggestions need enums at the same positions in separate overloads:

```text
/bot [command: string] [topic: string]
/bot (ping|list|help)<command: EndbotRoot>
/bot <command: string> <page: int>
/bot <name: string> <operation: string>
/bot <name: string> <operation: string> <arguments: message>
```

The `<name>` string overloads are the parseability layer: they accept every 2-token and every longer named
form (numeric help pages use the required-int overload instead, since strings reject numbers). The typed enum
overloads below them — operations, movement direction, action modes, on/off, dimension aliases, block faces, and
`stack` — exist so completion has something to advertise, but real Minecraft-client smoke showed they do not
cleanly narrow the UI while a compatible generic string branch remains: generic overloads stay visible and
dominate filtering, for both the name-first `/bot` surface and the selection-model `/botprobe` (an ad-hoc
diagnostic probe used during the command-parser investigation, not a shipped Endbot command). Numeric tokens
narrow better only because they eliminate the generic `string` branch. A broad `<operation: message>` fallback
was already rejected because it absorbs all named-operation suggestions. On this pinned Endstone 0.11 baseline,
execution is therefore prioritized over completion: every documented form parses, while autocomplete stays
degraded. Deeper command declaration redesign is deferred until upstream Endstone command API work
(Brigadier-style typed command tree, upstream PR #509) matures; this layout is intentionally a compatibility
stopgap, not the final command API. The pinned Endstone public API does not expose supported dynamic completion for
Bot profile names, so names remain strings; use `/bot list` to discover them. Endbot does not use private
registry or raw soft-enum hooks.

## Live parser smoke (real BDS required)

Repository unit tests prove the Python parser and the declaration coverage model, but they cannot execute the
closed-source BDS overload matcher. After any change to `COMMAND_USAGES`, run each of these on the pinned
native BDS build and confirm it reaches the Python layer (a Bot response or an `Endbot:` usage message, never a
Bedrock syntax error):

```text
/bot
/bot ping
/bot menu
/bot help
/bot help 2
/bot help 3
/bot help move
/bot help teleport
/bot help dance
/bot Alice spawn
/bot Alice status
/bot Alice menu
/bot Alice move forward
/bot Alice tp me
/bot Alice tp 100 64 -20 facing Steve
/bot Alice look at 1 64 2
/bot Alice attack interval 20
/bot Alice interact 1 64 2 up
/bot Alice hotbar 9
/bot Alice radius
/bot Alice radius 12
/bot Alice radius default
/bot Alice drop stack
/bot Alice stop
/bot Alice despawn
```

Native autocomplete on this baseline stays degraded by design: generic string/message overloads remain visible
and dominate filtering, so operation and direction suggestions do not narrow cleanly. Confirm execution for
every line above; record any completion change as evidence for the deferred redesign, not as a reason to remove
a parseability overload.

Block breaking/mining is not part of this remediation. A future implementation must use the server-authoritative
`PlayerAuthInput` block-action/prediction sequence and BDS inventory/tool rules, not direct plugin block mutation.

## Operational states

- `connecting`: a requested session has not spawned yet;
- `online`: the protocol session is active;
- `reconnecting`: an explicit reconnect or bounded automatic retry is in progress;
- `offline`: desired state is intentionally offline;
- `failed`: retries were exhausted; status includes the last error.

One Bot's generation/reconnect timer and inputs are independent of every other Bot. An unexpected disconnect retains
the profile UUID and retries with exponential backoff capped at 30 seconds, stopping after the configured attempt limit.
Explicit reconnect and live rename wait for the old transport to close before replacement, so a new session cannot race
the old one. A changed NetherNet server identity fails closed and is reported in status; an operator must deliberately
remove the saved pin after verifying an intentional BDS identity rotation.
