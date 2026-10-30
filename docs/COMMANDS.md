# Endbot commands and operation

## Optional Luau scripts

Scripting is off by default. Enable `[scripting] enabled = true` and install the pinned native runner;
see [SCRIPTING.md](SCRIPTING.md) for installation, configurable limits, generated API and validation gates, and
the [tutorial](SCRIPTING_TUTORIAL.md) to learn writing scripts.
The plugin hosts scripts; the Node Bedrock runtime is unchanged. Scripts use the invoking controller's
`endbot.command.control` authority, rechecked for every operation, and survive logout until stopped/revoked.

```text
/botscript list
/botscript show <name> [page]
/botscript run <name> [positional arguments...]
/botscript runs
/botscript stop <run-id|name>
/botscript delete <name>
/botscript help
/botscript menu
```

Enabled `/bot menu` adds **Scripts...** (after **+ New Bot here**) for shared source, line-by-line and book editing,
templates, parameter forms, the source on each script's screen, per-controller stopping and confirmed deletion. Typed `save` cannot
author source. Positional run arguments follow the script's declaration order, then `name=value`; quote positions, for example
`/botscript run row #miners "~ ~ ~" 2`. Runs never resume after restart. Disabling scripting retains source.

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

For players, `/bot` opens the Bot list, just like `/bot menu`. `/bot <name>` opens that Bot's menu, just like
`/bot <name> menu`, and `/bot #group` does the same for group operations. At the server console, `/bot` still shows
help page 1, and a bare target keeps the player-only menu reply.
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
/bot <name> move forward|backward|left|right for <ticks>
Movement acts as held input; stop halts it. for stops it after that many ticks.
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
/bot               -> Bot and Group target list (players); help page 1 (console)
/bot menu          -> the same target list
/bot Alice         -> Alice's menu (same as /bot Alice menu)
/bot Alice menu    -> Alice's actions and compact state/position summary
/bot #miners       -> the group operation menu (same as /bot #miners menu)
/bot #miners menu  -> the same operation menu, applied to #miners
```

The target list shows online Bots by name; connecting, retrying and failed Bots carry a dark-colored `JOIN`, `RETRY` or
`FAIL` prefix, and offline Bots are behind one `Offline (n)...` button. The online Bot screen has Stop everything,
Movement (move, jump, sprint / sneak), Look, Teleport, Gameplay (attack, use item, destroy, interact, nearby
entities, interact with entity, dismount),
Inventory (list inventory, hotbar, hold, swap, drop) and Bot (resume/spawn, reconnect, confirmed despawn, radius and status).
Category names follow Minecraft's control settings. An offline Bot offers resume/spawn, radius and the Bot settings
category. Individual Bot profile controls offer rename and confirmed forget; forget still requires an offline profile.
The target list also shows `#group (count)` entries that open this same operation screen directly. Group-wide controls
remain available with mixed/offline membership and report failures per operation; rename/forget require an individual.
The list is ordered by what `/bot` itself can address: online Bots, the `Offline (n)...` list, `#group (count)` targets,
then `+ New Bot here`. Two links follow creation: **Manage groups...** opens `/botgroup menu` and, when scripting is
enabled, **Scripts...** opens `/botscript menu`. Group management is not a `/bot` operation, so it appears only as that
link rather than among the targets.
Input forms use dropdowns, sliders and text fields; closing one (or cancelling the despawn confirmation) returns to the screen it was opened from; coordinate fields accept the same `~` forms as the command, and their
labels say what `~` is relative to. Submitting closes the form and the normal chat reply appears; run `/bot` again to
continue.

Jump, Attack, Use item and Destroy blocks mirror their command grammar instead of adding form-only meanings. Their
first screen offers the command's modes (`once`, `continuous`, `interval`, `stop`, plus `for` for use and `x y z` for
destroy). A mode without parameters runs at once; a mode with parameters opens a form holding only its clauses, named
as typed and measured in ticks (`interval <ticks>`, `hold <ticks>`, `for <ticks>`; the Destroy blocks continuous form
also offers `count <blocks>`; 20 = 1 second). A blank optional
clause is omitted, so a blank `for` means until stopped and a blank `count` means no block limit. Use item `once` opens
a form because it accepts `hold`.
For example, three charged bow shots are `interval...` with interval 30, hold 20, for 80. Move opens one form for
`move <direction> [for <ticks>]`; quick single-tap movement is left to the typed command.
Every command a form or button runs is echoed once in chat as its typed form, for example
`/bot Alice use interval 30 hold 20 for 80` in gray, before the normal reply. Menu navigation is not echoed.

Forms are only another way to type commands: every button or form submission becomes the equivalent `/bot` arguments
(for example `/bot Alice attack interval 40`) and goes through the same permission check, command queue, parser and
replies, so a typed command and a form submission always behave identically. A controller whose permission is revoked
while a form is open gets the usual permission reply on submit. The server console cannot open forms (`/bot menu` replies
with a pointer to `/bot help`). Advanced `tp ... facing ...` arguments and help detail pages stay command-only;
coordinate spawn, block interaction and individual profile rename/forget have form controls.

Unit tests cover screen contents, every form response mapping to parser-accepted arguments, malformed responses, and
view delivery/revocation. Rendering and input on a real client are a manual check: on a normal Microsoft/Xbox client,
open `/bot`, `/bot menu`, `/bot <name>` and `/bot <name> menu`, submit each input form once, cancel the despawn
confirmation, close forms with
Escape, and confirm a revoked controller cannot act from an already open form.

## Reply language

Replies, help text and form screens are available in English and Japanese. By default (`[ui] language = "auto"` in
`endbot.toml`, see [OPERATIONS.md](OPERATIONS.md) section 2) each player gets the language of their game client:
Japanese clients see Japanese, every other client sees English. `language = "en"` or `"ja"` fixes one language for
every player.

Only the wording changes. Command words and syntax stay English in every language: the help shows
`/bot <name> move forward|backward|left|right for <ticks>` with a Japanese description, and form mode buttons and
clause fields keep their command names (`once`, `continuous`, `interval <ticks>`, `for <ticks>`), so forms still teach
the typed command, and the gray echo of a form's command is unchanged. The server console, the server log, and the
`/bot` description in command autocompletion stay English. Messages that come from the Endbot runtime (for example a
missing Bot) are not translated. A notice from an ongoing job, such as continuous mining ending, uses the language of the
player who started it; its log entry stays English.

Japanese text uses only kana, kanji, ASCII and the Japanese comma and full stop, because the Bedrock chat font lacks many
symbols (arrows show as boxes). How it looks on a real client is a manual check: with a Japanese client, read
`/bot help` pages 1-3, a `status` reply, an error, the Bot list and Bot screens, and one mode form.

## Mining blocks

```text
/bot Alice hotbar 1
/bot Alice destroy
/bot Alice destroy once
/bot Alice destroy continuous
/bot Alice destroy continuous for 200
/bot Alice destroy count 64
/bot Alice destroy continuous for 200 count 64
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
`destroy for <ticks>` (or `destroy continuous for <ticks>`) runs continuous mining for that many input ticks from the
command, then ends the loop and aborts a block still in progress. Single-block `destroy` and coordinates take no `for`
or `count`; they keep the 10-minute safety timeout. When the duration ends, the starting controller gets one info-level
message, `Endbot: Alice: destroy continuous stopped: duration elapsed`, and `status` shows the ticks left while it
runs.

`destroy count <blocks>` (or `destroy continuous count <blocks>`) runs continuous mining until that many blocks are
gone, then ends the loop. The budget counts BDS-completed targets, so a block already breaking is still finished, and
waiting for the next one costs nothing: unlike the wall-clock `for` budget, the count does not depend on server load,
TPS or the gaps between blocks. `for` and `count` combine in either order and mining ends at whichever comes first,
with the matching info-level message (`... stopped: block count reached`). `status` shows the blocks so far after the
mode, for example `destroying minecraft:stone at 1 64 2 (continuous, 12/64 blocks)`, and a finished run keeps its
total, for example `last destroy: duration elapsed (137 blocks, minecraft:stone at 1 64 2)`.

BDS performs ordinary Survival breaking, including hardness/tool speed, harvest drops, durability and cancellable
`BlockBreakEvent` handling. The plugin never sets the mined block to air, dispatches `/setblock`, or grants operator
permissions. The pinned baseline uses server-authoritative block breaking; other modes are rejected explicitly.

The initial reply names the target, for example `Endbot: Alice: destroying minecraft:stone at 1 64 2; see status for the
result`. No completion message is sent; `status` shows the mining state in player-facing words:

```text
destroying minecraft:stone at 1 64 2                                (breaking now)
destroying minecraft:stone at 1 64 2 (continuous, 12/64 blocks)    (destroy count)
destroy continuous waiting for a block in sight (last: ..., 3 blocks)  (destroy continuous, nothing in reach)
last destroy: block broken or changed (minecraft:stone at 1 64 2)  (finished)
last destroy: block count reached (64/64 blocks, minecraft:stone at 1 64 2)  (count reached)
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

Mining ends only on `destroy stop` (mining only), `stop` (mining and all movement/actions), an elapsed `for` duration,
a reached `count` budget, death, disconnect, revocation of
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

`endbot controllers add|remove|list` asks endbotd directly over its loopback endpoint and prints the result. The list
includes effective permission and reason; `endbot doctor` inspects saved authorization and `endbot doctor --live` queries
the running endbotd.

Bot access can be changed without restarting BDS from the host CLI with `endbot controllers add <GamerTag>`,
`endbot controllers remove <GamerTag>`, and `endbot controllers list`. These run as the server
console, like the console-only `botcontrollers` commands, and persist grants/revocations across restart. They do not grant operator permissions.
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
/bot <name> share
/bot <name> private
/bot <name> owner <GamerTag>
```

`spawn` creates a profile if necessary and places the arriving UUID at the caller by default. From the server console,
`spawn` without coordinates leaves placement to BDS: a new Bot appears at the world spawn point and a known Bot where it
last was; `spawn at <x> <y> <z> in <dimension>` places it explicitly. Spawning an existing offline profile reuses its UUID but applies the requested
placement. `resume` reconnects an existing profile without relocation, allowing BDS native player persistence to win.
`reconnect` tears down and reconnects without changing identity or location. `despawn` is intentional offline state and
cancels automatic reconnect. `forget` requires an offline profile and removes only Endbot registration; it does not
purge BDS player data. A live rename preserves UUID and reconnects automatically so the login name changes.

`share` and `private` switch a Bot's visibility and, like `forget` and `rename`, stay with its owner or the console
(S12). An unowned Bot stays shared and is refused with a pointer to the console's `owner` command, so a player cannot
claim a pre-ownership Bot by making it private. Only the console runs `owner`, which assigns the Bot to the named
online player or controller while keeping its visibility (or the configured default for a Bot that had no entry).

`help`, `list`, `menu` and `ping` are reserved Bot names (any case), because the root commands `/bot help`, `/bot list`,
`/bot menu` and `/bot ping` take them. Creating a Bot with one of them, or renaming a Bot to one, is refused from
commands, forms and scripts alike. A profile that already has such a name keeps working; `/bot list` and its menu mark it
and suggest `rename`. The registry (`operations.json`, `reservedBotNames`) is the one list every surface reads.

`spawn`, `resume` and `reconnect` respect `[limits]` (see [configuration](OPERATIONS.md#2-configuration)): when the Bot
would take a slot kept for human players, or exceed `max-bots`, the command is refused with the current counts. A Group
or script fails only the affected members; `despawn` and `forget` are never limited. Existing Bots stay online when the
limit is lowered.

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
/bot Alice move forward|backward|left|right for <ticks>
/bot Alice jump|attack|use [once|continuous|interval <ticks>|stop]
/bot Alice jump|attack|use [continuous|interval <ticks>] for <ticks>
/bot Alice use for <ticks>
/bot Alice use hold <ticks> [interval <ticks>] [for <ticks>]
/bot Alice sprint on|off
/bot Alice sneak on|off
/bot Alice inventory
/bot Alice hotbar
/bot Alice hotbar <1-9>
/bot Alice swap <a> <b>
/bot Alice hold <item>
/bot Alice interact [<x> <y> <z> [face] | <block type> | @<type|id|nearest>]
/bot Alice attack @<type|id|nearest> [once|continuous|interval <ticks>|stop] [for <ticks>] [count <n>]
/bot Alice interact @<type|id|nearest> [once|continuous|interval <ticks>|stop] [for <ticks>] [count <n>]
/bot Alice interact stop
/bot Alice destroy <block type> [continuous] [for <ticks>] [count <blocks>]
/bot Alice entities
/bot Alice entity [@<type|id|nearest>]
/bot Alice target
/bot Alice block <x> <y> <z>
/bot Alice blocks <type> [radius <1-16>] [limit <1-64>] [at <x> <y> <z>]
/bot Alice dismount
/bot Alice drop
/bot Alice drop stack
/bot Alice drop <1-36|offhand|head|chest|legs|feet>
/bot Alice drop <item> [count]
/bot Alice drop all
/bot Alice stop
```

Omitting an action mode means `once`. Continuous actions run each 20 Hz input tick; interval actions accept 1–72000
ticks. Movement behaves as held input. Global `stop` clears movement, scheduled actions, sprint, and sneak while keeping
the session connected. `look` remains at its last rotation after stop. `use` sends the held item's normal Bedrock
interaction; with an empty hand it is a safe no-op. Endbot does not invent an item or issue a server command.
Sprint and sneak emit Bedrock's one-tick start/stop transitions as well as steady held-state flags; reconnect input reset
does not replay transitions from the previous transport.

`inventory` reads the authoritative inventory through Endstone on the server thread. It shows only non-empty slots,
with identifiers stripped of `minecraft:` and counts, for example `3: diamond_pickaxe x1, 10: stone x64`.
Slots follow the game screen: hotbar 1–9, main inventory 10–36, then `offhand`, `head`, `chest`, `legs`, `feet`.
An empty inventory reports `inventory is empty`; equipment-only inventories still list their equipment.
This query changes no items and needs no runtime inventory cache or request for a named online Bot. Offline Bots
report that they are not present in the world. In forms, **Inventory... → List inventory** runs the same command.
The server rejects ordinary Xbox players as Bot inventory targets.

`hotbar` reports or changes the selected slot using human-facing 1–9 numbering. Selection is sent through the normal
Bedrock equipment path and inventory updates continue to come from BDS; Endbot does not keep a second inventory.
BDS does not push every pickup's inventory delta to the picker. The Endbot plugin therefore reads all 36 actual BDS
inventory slots plus offhand and armor at a configurable interval (four server ticks by default) and sends changed read-only observations over
that Bot's existing connection, plus periodic unchanged corrections (20 server ticks by default). The runtime
updates its transient cache, including counts and typed NBT, and re-announces a changed held item through normal Bedrock
equipment packets, including offhand changes. This makes same-session pickups usable by `hotbar`, `drop` and held-item actions without reconnecting,
normally within four server ticks (about 0.2 seconds at 20 TPS). No item is added, removed or reassigned by the observer.
`drop` still uses a normal BDS-validated transaction; the reply says requested, not completed. Fresh observations also
correct an optimistic drop if BDS rejected it; an unchanged correction can take about one second with default settings.
Native inventory updates also announce changed held equipment, independent of periodic observations.
Unknown/invalid observations are ignored as a whole; an unknown or empty
selected slot still fails with `Selected hotbar slot is empty`. Without the matching plugin, reconnect remains the
recovery for missing native deltas. See [pickup validation](INVENTORY_VALIDATION.md).

`swap <a> <b>` requests an exchange of the two whole stacks, with the same hotbar 1-9 and main 10-36 labels as
`inventory`. One empty side makes it a move. Equal slots and two empty slots are errors. Offhand and armor are
excluded; equip is deferred. For example `/bot Alice swap 10 2` requests a main/hotbar exchange and
`/bot Alice swap 1 36` can move a hotbar stack into an empty main slot.

`hold <item>` matches an exact identifier (`diamond_pickaxe` or `minecraft:diamond_pickaxe`). A hotbar match wins:
it selects the lowest matching hotbar slot just like `hotbar <n>`. Otherwise it swaps the lowest matching main slot
into the currently selected hotbar slot, sending that slot's previous stack back to the main slot. A missing item
reports its requested identifier. Replies describe requests, such as `hold diamond_pickaxe: selected hotbar 3 requested`
or `hold diamond_pickaxe: moved slot 14 to hotbar 1 requested`; they do not establish BDS completion.

Both mutations use a legacy normal transaction with two window-0 container actions, carrying exact item data without
stack IDs. The runtime predicts both slots until the next authoritative observation corrects them, and announces
selected equipment after a changed selection or held stack. **Inventory... / Hold item...** accepts an item ID and
**Swap slots...** offers two 1-36 dropdowns. These forms echo the equivalent typed command, including any item prefix.
Group swap/hold use the existing UUID-pinned batch input path with bounded request replies, audit and saved history.
Live acceptance of swap/hold passed on the locked pair over Windows RakNet (2026-10-22); human-client forms, native
hints and other-client equipment rendering remain manual gates. See [the validation record](INVENTORY_VALIDATION.md).
The native `EndbotSwap`/`EndbotHold` typed overloads provide hints; enum-free required message tails continue to carry
execution under the pinned BDS declaration rules. Native client hints and form rendering still require live checks.

`drop` requests one selected item; `drop stack` requests the selected stack. `drop <slot>` requests that slot's whole
stack using the same labels as `inventory`: hotbar 1-9, main 10-36, then offhand/head/chest/legs/feet.
`drop <item> [count]` matches an exact item identifier, accepting either `stone` or `minecraft:stone`. Without a count,
it requests every matching stack. With a count (integer 1-10455), it takes up to that number, in increasing slot order,
then offhand/head/chest/legs/feet. A shortfall reports the number requested and the number asked for.
Numbers 1-36 and the equipment names select slots; `all` and `stack` are keywords. Other tokens are item identifiers;
use the `minecraft:` prefix to disambiguate an identifier equal to a keyword or slot name. Counts apply only to items.

`drop all` immediately requests one transaction per non-empty slot, including offhand and every armor slot (helmet too).
Replies describe requests, for example `drop slot 12 stone x5 requested` or `drop all requested for 41 slots`; BDS
validates each transaction. **Inventory... / Drop item...** offers selected one/stack, a slot, an item and optional count,
and drop all. The form asks for confirmation before drop all and echoes the command it submits. Typed commands run
immediately. Group selectors use the same grammar; drops remain mutations with bounded summaries, audit and history.

The versioned `endbot:inventory-v2` observation carries exact typed item/NBT data in bounded fragments, applied only
after a full valid snapshot arrives. Existing `hotbar-sync-interval-ticks` and `hotbar-sync-resend-ticks` settings now
cover the entire inventory. Size bounds and the remaining live gates are described in the validation record.

`use continuous` holds right-click: its first input tick sends the initial air-use interaction, and later ticks send
simulation-tick interactions in the same authoritative input stream. `use stop` or global `stop` releases it, for example
to throw a charged trident or shoot a bow. Food use requires vanilla eating conditions. `use once` and `use interval`
remain short press/release actions; they do not hold long enough to eat or fully charge an item. Switching hotbar slots
also releases the previous item. Real item outcomes and dimension-transition behavior are tracked in
[the backlog](BACKLOG.md); historical generic use/release smoke does not cover every item.

`use for <ticks>` holds use for 1–72000 runtime input ticks and then releases automatically. For example,
`/bot Alice use for 40` requests one two-second hold/release sequence, suitable for charging a bow or trident.
These are the runtime's 20 Hz input ticks; server lag and vanilla item conditions still affect the outcome.
The hold duration starts with the first air-use transaction. If a pending block interaction occupies the input tick,
use waits without consuming its hold duration. Stopping before that first use cancels it without sending a release;
an initially empty hand remains a no-op rather than waiting to use a future pickup.
The reply confirms the request, not a successful shot or consumption. This is a duration, not an item-count or
completion condition: food may finish early, repeat during a long hold, or fail vanilla prerequisites.
`use stop` and global `stop` release early, which can fire a charged item. Explicit hotbar selection also releases and
cancels timed use; death, dimension change and disconnect discard it. A new timed-use command replaces the previous
one with a fresh hold/deadline. Movement and other inputs continue when its duration expires. Nothing resumes after
reconnect. Existing `use`/`once`, `continuous` and `interval` behavior remains available.

### Durations (`for`) and charged presses (`hold`)

`for <ticks>` says how long an input keeps going, for every action, movement and continuous mining:

```text
/bot Alice move forward for 40                 walk forward for 2 seconds, then stop moving
/bot Alice jump for 100                        jump every tick for 100 ticks (same as jump continuous for 100)
/bot Alice attack interval 20 for 200          attack every 20 ticks for 200 ticks
/bot Alice use for 40                          hold use for 40 ticks, then release (unchanged)
/bot Alice use hold 30                         charge for 30 ticks, release once
/bot Alice use hold 30 for 200                 charge 30 / release, repeated back to back for 200 ticks
/bot Alice use hold 30 interval 40 for 200     start a 30-tick charge every 40 ticks for 200 ticks
/bot Alice destroy for 200                     continuous mining for 200 ticks
/bot Alice destroy count 64                    continuous mining until 64 blocks are broken
```

Without a mode, an action is `once`; with `for` it is `continuous`. `once ... for` and `stop ... for` are rejected
because one press has no duration. `interval`, `hold` and `for` clauses may appear in any order, at most once each;
`interval` replaces `once`/`continuous`. All values are 1–72000 runtime input ticks (20 per second).

`for` counts from the command (the next input tick), so the input runs for exactly that many ticks. The one exception
is the existing plain `use for <ticks>` / `use continuous for <ticks>`: it still counts from its first air use, so a
deferred press cannot eat the charge (see above). When a duration ends, only that input stops; movement, other
actions, sprint/sneak and mining continue. A new command for the same input replaces its deadline, and a command
without `for` removes it. Global `stop`, `<input> stop`, death and disconnect end it early. `status` lists inputs that
end on their own with the ticks left, for example `timed: move forward 30 ticks left, use interval 40 hold 30`.

An `interval` action is a repeating command, so it outlives the item it first fired with: consuming a stack (drinking
a potion) or ending up with an empty hand only ends the press in progress, and the next trigger uses whatever is held
then. A trigger with an empty hand sends nothing and keeps waiting for the following interval. `once`, `continuous`
and a plain `use for` still cancel themselves when their item is consumed or the hotbar slot changes.

`count <blocks>` is the destroy-only completion counterpart to `for`: it stops continuous mining once that many
blocks are gone (1–72000), waiting between blocks costs nothing, and `for ... count ...` in either order ends at
whichever comes first. Item-count conditions for other inputs are not implemented.

`hold <ticks>` (use only) holds use for that many use transactions per press and then releases, the same press/release
as `use for`, so a bow or trident can be shot repeatedly. `hold` without `for` or a mode charges once; `hold ... for`
(or `continuous hold`) starts the next charge right after the release tick; `hold ... interval <ticks>` starts a charge
every interval ticks, which must be longer than the hold so each release gets its own tick. A `for` deadline that
arrives mid-charge releases at once, which may fire a partly charged item; choose a multiple of the press cycle to
avoid it. Like timed use, holds are canceled by hotbar selection, a changed or empty held item, dimension change,
death and disconnect, and they never resume after reconnect. Each release confirms an input only, not a shot.

The Jump, Attack, Use item, Move and Destroy blocks forms offer the same `interval`, `hold` and `for` clauses (the
Destroy blocks form adds `count` for its continuous mode) (see
[control forms](#control-forms)). See [item-use/sync validation](ITEM_USE_SYNC_VALIDATION.md) and
[the backlog](BACKLOG.md).

`use` means using or holding the selected item in the air, such as food, a bow, or a trident. `interact` is a normal
right-click on the named block face. It sends the protocol-2193 press, server-authoritative item-interaction tick, and
release sequence; BDS decides reach, legality, collision, placement, and inventory decrement. A placeable selected
item can therefore place a block in Survival without the plugin editing the world. `drop` drops one selected item and
`drop stack` requests the selected stack through normal inventory transactions; wider selectors use the same legacy
container-to-world path with exact observed item data (see the inventory/drop grammar above).

### Targets: crosshair, coordinates, block types and `@` entities

Left- and right-click verbs stay separate (`attack`/`destroy`, `use`/`interact`), and their targets share one syntax
(decided 2026-10-24):

| Form | Target | Examples |
| --- | --- | --- |
| none | what the Bot is looking at | `interact`, `destroy`, `attack` |
| `<x> <y> <z> [face]` | that block; `~` is relative to the Bot; without a face, the side facing the Bot | `interact ~~-1~`, `destroy 1 64 2 up` |
| `<block type>` | the nearest block of that type the Bot can see (`interact` within 6 blocks, `destroy` within 5) | `interact chest`, `destroy oak_log` |
| `@<type\|id\|nearest>` | the nearest matching non-player entity (`interact` within 6 blocks, `attack` within 4.5) | `interact @cow`, `attack @zombie`, `interact @9` |

`@` follows vanilla's entity-selector sign and is required for every entity, including `entity @cow` and ids
(`interact @9`); listings show ids in that form (`cow @9 (3.4m)`), and script data carries it as `selector`.

Targets combine with repetition. `attack @zombie continuous|interval <ticks>|for <ticks>` re-aims at the nearest match
on every trigger and does nothing while none is in reach; it never falls back to the crosshair, so a player there is
never hit. `interact @cow count 5` (or `interval`/`for`) right-clicks a matching entity it has not clicked yet in this
run while one is in reach, then the one clicked longest ago, so a herd is fed one by one. `count` counts right- or
left-clicks sent to an entity; BDS still decides the outcome (a cow already in love eats nothing). After each click the
next waits until BDS reports the held item it changed (wheat eaten, a sword worn), or at most 10 ticks: a click with a
stale held item is rejected. `once` is a single click, and `attack @cow stop`, `interact stop` or `stop` ends it;
`status` shows the running target and the clicks left, for example `interact @cow continuous 2 left`. `destroy oak_log count 10` (or
`continuous`/`for`) is a continuous mining loop that turns to the nearest visible block of that type for each block and
waits while none is in reach; it never walks. Unknown block types are reported as such, and
`destroy @…` points to `attack`. In Luau: `bot:interact("@cow")`, `bot:interact("chest")`, `bot:destroy("oak_log")`,
`bot:attack("@zombie")`. Live check (2026-10-24, Windows RakNet, console): `interact @cow` fed the cow, the crosshair
`interact` reached it, `attack @cow` damaged it and `destroy dirt` broke the nearest dirt block. Live fix check
(2026-10-24): `interact @cow count 3` fed three different cows (three wheat) and `destroy oak_log count 5` broke a
five-log trunk; before the held-item wait, rapid clicks after the first were rejected by BDS.

### Looking around

Coordinates anywhere in `/bot` accept vanilla's compact relative form: `~~-1~` is `~ ~-1 ~`. `inventory` and
`hotbar` list items with remaining/maximum durability for tools and armor, for example `iron_pickaxe x1 (210/250)`;
`hotbar` also lists the hotbar items, not only the selected slot. `entity [@<type|id|nearest>]` shows one entity within 16
blocks in detail (id, distance, position, health, name tag); without an argument it is the nearest.

`target` shows what the Bot's crosshair hits within 6 blocks: the nearer of a block (position, face, type) and a
non-player entity. `block <x> <y> <z>` shows one block's type and states; `~` is relative to the Bot, and coordinates beyond
30000000 are refused. `blocks <type>`
lists the nearest blocks of that type, as `x y z (distance)`, nearest cells first (it stops reading once `limit`
matches are certain), within a radius (default 8, at most 16), up to `limit`
(default 10, at most 64),
counting only blocks with a face open to air, water or plants, so it does not reveal buried ores. `at <x> <y> <z>`
moves the scan centre (and the distances) to that point instead of the Bot, so a tall structure can be searched from
the height that matters; `~` is relative to the Bot. All three read BDS
through the Endstone SDK, never change anything, and return data to scripts; Group targets print every member's
result like `status`. The Gameplay form's **Look around...** offers them with the nearby-entity list.

### Entities and vehicles

`entities` lists up to 10 non-player entities within 16 blocks of the Bot, nearest first, as
`<type> @<id> (<distance>m)`. A Group target is a query like `status`: every member's list is printed, with no
history or audit entry. `interact @<type|nearest|id>` picks the nearest matching non-player entity within 6 blocks
(`cow` and `minecraft:cow` are the same; `nearest` takes any type except dropped items, orbs, projectiles and
effects; an id comes from `entities`), turns the Bot
toward it and right-clicks it with the held item on the next input tick. This is the vanilla client path
(`item_use_on_entity` with action `interact`): feeding and breeding consume the held item, shears shear, a name tag
names, and an empty hand on a minecart, boat or rideable animal mounts it. BDS decides every outcome; the reply
only confirms the request.

While riding, the Bot follows the vehicle position BDS sends and ignores its own movement prediction; `status` and
the Bot screen show `riding <type>`. A riding Bot may `interact` another vehicle to switch to it, as a player
can; `nearest` skips the vehicle it is riding. `dismount` sends the vanilla leave-vehicle action. As in the vanilla
client, the rider chooses its exit spot: the plugin checks the neighbouring blocks for a solid floor and two passable
blocks above it, at the vehicle's level, then one above, then one below. Minecarts use the
[Minecraft Wiki](https://minecraft.wiki/w/Minecart#Dismounting) order relative to travel (right, left, rear right,
rear left, front right, front left, rear, front); boats prefer the Bot's look direction
([Boat#Exiting](https://minecraft.wiki/w/Boat#Exiting)); other rideables use the same ring, matching Bedrock's default
"valid ground position around the entity". Block passability is a conservative name list (unknown blocks are solid).
With no free spot the Bot stays where the vehicle was. The exit point stays inside its block, 0.05 clear of the
vehicle's box and the block edges, so the Bot never rests on a boat's edge or a neighbouring block.

The runtime has no world collision: it starts falling when BDS corrects its position. BDS skips that correction while
a boat (or another standable entity) is nearby, so a Bot whose floor was removed next to a boat stood in the air. The
plugin now checks every hotbar-sync interval whether a Bot standing on a block top has a solid block under its feet
box and, if not, tells the runtime to start falling; BDS then corrects the landing. Fractional heights (slabs, carpets),
water, flight and standing on a boat or shulker are never reported. BDS ignores a rider's reported position but corrects a sideways
jump right after the link ends, so the Bot reports the exit spot for two ticks while still riding and then sends the
leave action; its first free input then does not move. A second `dismount` while one is pending is refused, and if
BDS never confirms the leave within 40 ticks the Bot stops counting as riding. Live check (2026-10-24): switching from a minecart to a boat
without dismounting, a boat exit in the look direction and a moving-minecart side exit, each with no correction. Steering horses or
boats is not implemented. Live check (2026-10-24, Windows RakNet, locked pair): feeding a cow consumed wheat,
the Bot boarded a minecart by id, rode a powered minecart about 12 blocks with BDS keeping its position on the
cart, refused interaction while riding (before vehicle switching), and walked normally after leaving both a stationary and a moving cart
(`scripts/raknet_spike.py --entity`).

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

## Shared Group menus and results

The Group feature adds direct group operation targets to `/bot menu`. `/botgroup` alone or `/botgroup menu` opens
group browsing/creation; `/botgroup menu #miners` opens member browsing and membership management, with a nested
**Group operations...** entry. `/bot #miners menu` opens the shared Bot operation screen directly, including teleport,
lifecycle and chunk radius. Returning from a nested operation screen goes back to management when entered there, or
to the Bot/target list when entered directly. **Group management...** links back to the selected group's management
screen; an existing **Back to group management** button is used instead when entered from management.
Individual members open their normal Bot screen. Group deletion is confirmed
and keeps the Bots. Forms echo and submit the same `/bot`
or `/botgroup` commands and authorization checks, with UUID/membership checks to reject stale forms.

Group ownership uses `/botgroup`:

```text
/botgroup share #group
/botgroup private #group
/botgroup owner #group <GamerTag>
```

The owner or the console may `share` a Group or make it `private`; an unowned Group stays shared and is refused with
a pointer to `owner`. Only the console reassigns a Group's owner, and the GamerTag must be an online player or a
controller. A private Group is left out of other players' lists and resolves as not found for them (S4).

Whole-group mutating-command acknowledgements and their normal audit entries are capped at two lines: counts plus grouped
failure categories, independent of group/failure size. They confirm acceptance, not completed world actions. Details
are available without menus via `/botgroup result #miners [history]`. This prints every member's result for one
operation: omit the number or use `1` for the latest, `2` for the previous operation, and so on. The menu reads the
same saved results through `/botgroup menu #miners result [history]`, with older/newer buttons. Retention is up to
20 operations per caller/group and 128 globally, evicting oldest first. Results are private per caller/group, in memory, and reset on
restart. An unavailable response after sending inputs is reported as unknown acceptance. See
[Group scope, evidence and client checks](GROUPS.md) for the full behavior and remaining gates.

Queries return data directly: `/bot #miners status`, `/bot #miners inventory`, `/bot #miners hotbar` and `/bot #miners radius` print every
member's query result, labeled with `#group[index]` and Bot name. Failures occupy that member's result line; the
remaining members are still queried. Indexed queries work the same way. Queries create no audit entry and do not
change `result` history, so a state check after an action still leaves that action as the latest operation result.
`hotbar <slot>` and `radius <value|default>` remain modifying operations with bounded summaries and saved history.

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
/bot Alice use for 40
/bot Alice use hold 30 interval 40 for 200
/bot Alice jump interval 20 for 100
/bot Alice move forward for 40
/bot Alice destroy for 200
/bot Alice destroy count 64
/bot Alice interact 1 64 2 up
/bot Alice hotbar 9
/bot Alice inventory
/bot #miners inventory
/bot #miners[1] inventory
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
