# Endbot commands

What a player with a controller grant can type and tap in game: `/bot`, `/botgroup`, `/botscript` and `/botweb`.
Access is granted by the server's operator ([controller enrollment](OPERATIONS.md#granting-access)); the
roles and what each may do are in [PERMISSIONS.md](PERMISSIONS.md).

How these commands are implemented is in [BOT_CONTROL.md](BOT_CONTROL.md). What was checked on real servers is in
the [validation status](COMPATIBILITY.md#bot-operations).

**Contents**

- [Basics](#basics)
  - [Quick start](#quick-start)
  - [Help, list and status](#help-list-and-status)
  - [Operational states](#operational-states)
  - [Command results](#command-results)
  - [Reply language](#reply-language)
  - [Control forms](#control-forms)
- [Bots: spawn, despawn and move](#bots-spawn-despawn-and-move)
  - [Lifecycle](#lifecycle)
  - [Teleport](#teleport)
  - [Chunk loading range](#chunk-loading-range)
- [Movement and actions](#movement-and-actions)
  - [Moving, sprinting and stopping](#moving-sprinting-and-stopping)
  - [Looking](#looking)
  - [Jump, attack and use](#jump-attack-and-use)
  - [Holding use](#holding-use)
  - [Durations (`for`) and charged presses (`hold`)](#durations-for-and-charged-presses-hold)
- [Inventory](#inventory)
  - [Inventory and hotbar](#inventory-and-hotbar)
  - [Swap and hold](#swap-and-hold)
  - [Dropping items](#dropping-items)
- [Blocks and entities](#blocks-and-entities)
  - [Targets: crosshair, coordinates, block types and `@` entities](#targets-crosshair-coordinates-block-types-and--entities)
  - [Areas: `within` and `except`](#areas-within-and-except)
  - [Interacting with blocks](#interacting-with-blocks)
  - [Mining blocks](#mining-blocks)
  - [Looking around](#looking-around)
  - [Entities and vehicles](#entities-and-vehicles)
- [Other features](#other-features)
  - [Shared Group menus and results](#shared-group-menus-and-results)
  - [Scripts and triggers](#scripts-and-triggers)
  - [Linking the web](#linking-the-web)
- [Appendix](#appendix)
  - [Syntax summary](#inputs-and-actions)
  - [Autocomplete](#autocomplete)

## Basics

How commands, replies, menus and help work. Read this first.

### Quick start

Every command names its Bot; there is no selected target.

```text
/bot Alice spawn
/bot Alice tp me
/bot Alice jump
/bot Alice attack continuous
/bot Alice stop
/bot Alice despawn
```

Typing `/bot` alone opens the Bot list. `/bot Alice` opens Alice's menu, and `/bot #group` opens the group
operation menu (see [control forms](#control-forms)).

### Help, list and status

Show the command summary, the known Bots, or one Bot's state.

```text
/bot help
/bot help <page>
/bot help <operation>
/bot list
/bot <name> status
```

| Command | What it shows |
| --- | --- |
| `/bot help` | Help page 1 |
| `/bot help <page>` | Page 1, 2 or 3 of the command summary, modeled on vanilla Bedrock `/help` |
| `/bot help <operation>` | Every syntax of that operation with a short explanation, for example `/bot help move` |
| `/bot list` | Every known profile and its lifecycle state |
| `/bot <name> status` | Runtime state, plus dimension, position and rotation from BDS while the Bot is in the world |

The help pages cover:

- page 1: lifecycle, status and teleport;
- page 2: movement and actions;
- page 3: inventory, blocks and Bot settings.

Each help line is its own chat message, so it reads well in Bedrock chat.

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

Notes:

- `status` shows `yaw=` in the range [0, 360), the same as `look`'s result and a script's `status` `data.look`.
- At the server console, `/bot` alone shows help page 1 instead of the list.

### Operational states

What `/bot list` and `status` mean by each state:

| State | Meaning |
| --- | --- |
| `connecting` | A requested session has not spawned yet |
| `online` | The session is active |
| `reconnecting` | An explicit reconnect or an automatic retry is in progress |
| `offline` | The Bot is intentionally offline |
| `failed` | Retries ran out; `status` includes the last error |

- Each Bot reconnects and takes input independently of every other Bot.
- After an unexpected disconnect, a Bot keeps its UUID and retries with growing waits, at most 30 seconds apart, until
  the configured number of attempts.
- `reconnect` and a live `rename` wait for the old connection to close first, so two sessions never overlap.

### Command results

Every reply about a Bot starts with `Endbot: <Bot>: ` and says what was applied, including errors and syntax errors.

```text
/bot Alice move forward       -> Endbot: Alice: movement set to forward
/bot Alice jump               -> Endbot: Alice: jump once requested
/bot Alice use continuous     -> Endbot: Alice: use continuous started
/bot Alice stop               -> Endbot: Alice: movement and actions stopped (sprint/sneak off)
/bot Alice hotbar 2           -> Endbot: Alice: hotbar slot 2 selected
/bot Alice move sideways      -> Endbot: Alice: Unknown or invalid Bot action: move
```

- Look replies include yaw and pitch; interaction replies include the block and face.
- Rename and lifecycle replies name the requested change.
- An error names the action, for example `Endbot: Alice: move failed: Bot is offline`.

A reply confirms that the input was applied or the request accepted, not the outcome. BDS still decides collision,
item use, reach and every other world result; check with `status` and by looking at the world.

Connecting is asynchronous: `connecting` does not mean the spawn has finished.

### Reply language

Replies, help text and menus come in English or Japanese.

| `[ui] language` in `endbot.toml` | Language |
| --- | --- |
| `"auto"` (default) | Each player's game client language: Japanese clients see Japanese, all others English |
| `"en"` | English for every player |
| `"ja"` | Japanese for every player |

The operator sets this ([configuration](OPERATIONS.md#settings)).

Only the wording changes. Command words and syntax stay English in every language:

- the help shows `/bot <name> move forward|backward|left|right for <ticks>` with a Japanese description;
- form mode buttons and clause fields keep their command names (`once`, `continuous`, `interval <ticks>`,
  `for <ticks>`), so forms still teach the typed command;
- the gray echo of a form's command is unchanged.

These stay English:

- the server console and the server log;
- the `/bot` description in command autocompletion;
- messages from the Endbot runtime, for example a missing Bot.

A notice from an ongoing job, such as continuous mining ending, uses the language of the player who started it.

Japanese text uses only kana, kanji, ASCII and the Japanese comma and full stop, because the Bedrock chat font lacks
many symbols (arrows show as boxes).

### Control forms

Run the common operations from Bedrock menus instead of typing them.

```text
/bot               -> Bot and Group target list (players); help page 1 (console)
/bot menu          -> the same target list
/bot Alice         -> Alice's menu (same as /bot Alice menu)
/bot Alice menu    -> Alice's actions and compact state/position summary
/bot #miners       -> the group operation menu (same as /bot #miners menu)
/bot #miners menu  -> the same operation menu, applied to #miners
```

#### The target list

The list is ordered by what `/bot` can address:

1. online Bots, by name; connecting, retrying and failed Bots carry a dark `JOIN`, `RETRY` or `FAIL` prefix;
2. one `Offline (n)...` button holding the offline Bots;
3. `#group (count)` entries, which open the group operation screen directly;
4. `+ New Bot here`;
5. **Manage groups...**, which opens `/botgroup menu`;
6. **Scripts...**, which opens `/botscript menu` (only when scripting is enabled).

Group management is not a `/bot` operation, so it appears only as that link.

#### A Bot's screen

Category names follow Minecraft's control settings. An online Bot has:

| Button | What it holds |
| --- | --- |
| Stop everything | `stop` |
| Movement | move, jump, sprint / sneak |
| Look | look |
| Teleport | tp |
| Gameplay | attack, use item, destroy, interact, nearby entities (**Look around...**), interact with entity, dismount |
| Inventory | list inventory, hotbar, hold, swap, drop |
| Bot | resume/spawn, reconnect, confirmed despawn, radius and status |

- An offline Bot offers resume/spawn, radius and the Bot settings category.
- The Bot settings offer rename and a confirmed forget; forget still needs an offline Bot.
- A group screen has the same operations and works with mixed or offline members, reporting failures per
  operation. Rename and forget need an individual Bot.

#### Mode forms

Jump, Attack, Use item and Destroy blocks first offer the command's modes: `once`, `continuous`, `interval`, `stop`,
plus `for` for Use item and `x y z` for Destroy blocks.

- A mode without parameters runs at once.
- A mode with parameters opens a form with only its clauses, named as typed: `interval <ticks>`, `hold <ticks>`,
  `for <ticks>`, and `count <blocks>` on the Destroy blocks continuous form. Ticks are 20 per second.
- A blank optional clause is left out: a blank `for` means until stopped, a blank `count` means no block limit.
- Use item `once` opens a form because it accepts `hold`.
- Move opens one form for `move <direction> [for <ticks>]`; a quick single tap of movement is left to the command.

Example: three charged bow shots are Use item, `interval...`, with interval 30, hold 20, for 80.

#### Using the forms

- Dropdowns, sliders and text fields take the input. Coordinate fields accept the same `~` forms as the command,
  and their labels say what `~` is relative to.
- Closing a form, or cancelling the despawn confirmation, returns to the screen it came from.
- Submitting closes the form and the normal chat reply appears; run `/bot` again to continue.
- Each command a button or form runs is echoed once in gray as its typed form, for example
  `/bot Alice use interval 30 hold 20 for 80`, before the reply. Menu navigation is not echoed.

A form does exactly what the typed command does, with the same permission check and replies. If your permission is
revoked while a form is open, submitting it gets the usual permission reply.

These stay command-only: advanced `tp ... facing ...` arguments and the help detail pages. Coordinate spawn, block
interaction and rename/forget do have form controls.

The server console cannot open forms: `/bot menu` there replies with a pointer to `/bot help`, and a bare target such
as `/bot Alice` keeps the player-only menu reply.

## Bots: spawn, despawn and move

Creating Bots, taking them offline and bringing them back, names, limits, teleporting and the chunk loading range (help page 1).

### Lifecycle

Create, connect, disconnect, rename and remove a Bot.

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
/bot <name> visibility shared|private
/bot <name> owner <GamerTag>
```

| Operation | What it does |
| --- | --- |
| `spawn` | Creates the profile if needed and places the Bot at you; `at` places it at coordinates. An existing offline profile keeps its UUID but goes to the requested place |
| `resume` | Reconnects an existing profile without moving it, so BDS's saved player position wins |
| `reconnect` | Disconnects and reconnects without changing identity or location |
| `despawn` | Takes the Bot offline on purpose and cancels automatic reconnect |
| `forget` | Removes the Endbot registration of an offline Bot; BDS player data is kept |
| `rename` | Renames the Bot; a live Bot keeps its UUID and reconnects so its login name changes |
| `visibility` | Makes the Bot `shared` or `private` |
| `owner` | Console only: assigns the Bot to a player |

Example:

```text
/bot Alice spawn at 100 64 -20 in nether
/bot Alice rename Miner_1
```

#### Names

- 1–16 ASCII letters, digits or underscores.
- Names are case-insensitive: `alice` and `Alice` collide.
- The names of online real players are refused. Endbot cannot see every offline player's GamerTag, so avoid names of
  known real players.
- `help`, `list`, `menu` and `ping` (any case) are reserved, because `/bot help`, `/bot list`, `/bot menu` and
  `/bot ping` use them. Creating or renaming to one is refused from commands, forms and scripts. A Bot that already
  has such a name keeps working; `/bot list` and its menu mark it and suggest `rename`.

#### Spawn notes

- From the server console, `spawn` without coordinates leaves placement to BDS: a new Bot appears at the world spawn
  point, a known Bot where it last was. Use `spawn at <x> <y> <z> in <dimension>` to place it.
- Coordinates and the dimension are checked before anything is created, and a first spawn refuses an active real
  player's name; an existing Bot may reuse its own UUID and name. A refused spawn changes nothing.
- If a spawn never reaches the world, `resume`, `reconnect`, `despawn`, `forget` or `rename` discards its pending
  placement, so it is never used later.

#### Limits

`spawn`, `resume` and `reconnect` respect the operator's `[limits]` ([Bot limits](OPERATIONS.md#bot-limits)).

- A Bot that would take a slot kept for human players, or exceed `max-bots`, is refused with the current counts.
- A Group or script fails only the affected members.
- `despawn` and `forget` are never limited.
- Bots already online stay online when the limit is lowered.

#### Visibility and owner

- `visibility`, `forget` and `rename` are for the Bot's owner or the console.
- An unowned Bot stays shared: `visibility` is refused with a pointer to the console's `owner` command, so a player
  cannot claim an older Bot by making it private.
- `owner` (console only) assigns the Bot to the named online player or controller. It keeps the Bot's visibility, or
  uses the configured default for a Bot that had none.

### Teleport

Move a Bot to a player, coordinates, or another dimension.

```text
/bot <name> tp me
/bot <name> tp <player>
/bot <name> tp <x> <y> <z> [<yaw> <pitch>]
/bot <name> tp <x> <y> <z> facing <player>
/bot <name> tp <x> <y> <z> facing <x> <y> <z>
/bot <name> tp in <dimension> <x> <y> <z>
```

- Dimensions: `overworld`, `nether` and `end` (their Minecraft namespaced IDs), or a raw namespaced ID.
- `~` in the destination and in `facing` are measured from the same moment.
- Teleport does not use vanilla `/tp` and does not need cheat permissions.

Example:

```text
/bot Alice tp me
/bot Alice tp 100 64 -20 facing Steve
/bot Alice tp in nether 100 64 -20
```

A Bot teleported into the air falls and lands normally.

### Chunk loading range

Set how many chunks around a Bot the server sends it.

```text
/bot <name> radius <chunks>
/bot <name> radius
/bot <name> radius default
```

| Form | What it does |
| --- | --- |
| `radius <chunks>` | Sets this Bot's range, 2..32 chunks |
| `radius` | Shows the requested value, where it comes from, and the server's answer |
| `radius default` | Removes the Bot's own value; the shared setting applies again |

Example:

```text
/bot Idle radius 5
/bot Explorer radius 12
/bot Alice radius         -> Endbot: Alice: chunk radius 12 (Bot override); server confirmed 10
```

- Without a Bot value, `[runtime].chunk-radius` in `endbot.toml` applies (default 5).
- A change affects only that Bot and applies at once while online, without reconnecting.
- Offline Bots can be configured too; they show `applies when online`.
- The Bot's value survives reconnect, rename and runtime restart.
- Right after a change the reply says `server confirmation pending`; query again (or use `status`) to see the
  confirmed range.

Use a small range for Bots that stay put and a larger one for exploring Bots. BDS can adjust either request; on the earlier
Endstone/BDS pair a request for 3 became 5 ([validation status](COMPATIBILITY.md#bot-operations)).

The range does not change the server's simulation distance, move the Bot, or guarantee terrain generation. See
[operator configuration](OPERATIONS.md#chunk-radius).

## Movement and actions

Walking, looking, jumping, attacking and using items, and how long an action lasts (help page 2).

### Moving, sprinting and stopping

Movement acts as a held key until stopped.

```text
/bot <name> move forward|backward|left|right|stop
/bot <name> move forward|backward|left|right for <ticks>
/bot <name> sprint on|off
/bot <name> sneak on|off
/bot <name> stop
```

- `stop` clears movement, scheduled actions, sprint and sneak, and ends mining. The Bot stays connected.
- `look` keeps its last rotation after `stop`.

### Looking

Turn the Bot to an angle or toward a point.

```text
/bot <name> look <yaw> <pitch>
/bot <name> look at <x> <y> <z>
```

| Value | Meaning |
| --- | --- |
| yaw | Degrees: 0 faces south (+Z), 90 west (-X), 180 north, 270 east; any value is taken modulo 360 |
| pitch | -90 (straight up) to 90 (straight down) |

- `look` answers once BDS reports the new rotation (within 1 degree), so an `attack`, `use`, `destroy` or `interact`
  sent right after it acts on the new view.
- If BDS does not report it within about 0.5 s (10 ticks at 20 TPS), `look` fails with a timeout; the Bot may still
  turn.
- A typed `look <yaw> <pitch>` reply echoes the angles as you gave them. The Look form's slider runs from -180 to
  180, and any value is applied modulo 360.

### Jump, attack and use

Press an action once, keep it going, or repeat it on an interval.

```text
/bot <name> jump|attack|use [once|continuous|interval <ticks>|stop]
```

| Mode | Meaning |
| --- | --- |
| `once` (default) | One press |
| `continuous` | Every input tick (20 per second) |
| `interval <ticks>` | Every that many ticks, 1–72000 |
| `stop` | Stops this action |

Example: `/bot Alice attack interval 20` attacks once a second.

`use` uses the held item in the air, such as food, a bow or a trident. With an empty hand it does nothing. Endbot
never invents an item or runs a server command.

### Holding use

`use continuous` holds right-click until `use stop` or `stop`, for example to charge and throw a trident or shoot a
bow.

- Eating needs vanilla's eating conditions.
- `use once` and `use interval` are short presses; they do not hold long enough to eat or fully charge an item.
- Switching hotbar slots releases the previous item.

`use for <ticks>` holds use for 1–72000 input ticks, then releases. For example, `/bot Alice use for 40` is one
two-second hold, enough to charge a bow or trident.

- The hold starts with the first use. If a block interaction is still pending, the hold waits without losing time.
- Stopping before that first use cancels it without a release.
- A hand that starts empty stays a no-op; it does not wait for a future pickup.
- The reply confirms the request, not a shot or a meal. It is a duration: food may finish early, repeat during a
  long hold, or fail vanilla's conditions. Server lag affects the outcome.
- `use stop` and `stop` release early, which can fire a charged item.
- Selecting a hotbar slot releases and cancels it; death, a dimension change and disconnect discard it.
- A new timed use replaces the previous one with a fresh hold and deadline.
- When it ends, movement and other inputs continue. Nothing resumes after reconnect.

### Durations (`for`) and charged presses (`hold`)

`for <ticks>` sets how long an input keeps going, for every action, movement and continuous mining. `hold <ticks>`
makes each use press a charged press.

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

Rules:

- All values are 1–72000 input ticks (20 per second).
- Without a mode an action is `once`; with `for` it is `continuous`.
- `once ... for` and `stop ... for` are refused, because one press has no duration.
- `interval`, `hold` and `for` may come in any order, at most once each. `interval` replaces `once`/`continuous`.

How `for` runs:

- It counts from the command (the next input tick), so the input runs for exactly that many ticks.
- Exception: plain `use for <ticks>` / `use continuous for <ticks>` counts from its first use, so a delayed press does
  not lose its charge (see [holding use](#holding-use)).
- When it ends, only that input stops; movement, other actions, sprint/sneak and mining continue.
- A new command for the same input replaces its deadline; a command without `for` removes it.
- `stop`, `<input> stop`, death and disconnect end it early.
- `status` lists inputs that end on their own, with the ticks left, for example
  `timed: move forward 30 ticks left, use interval 40 hold 30`.

How `interval` treats the held item:

- An `interval` action outlives the item it first used. Using up a stack (drinking a potion) or an empty hand ends
  only the press in progress; the next trigger uses whatever is held then.
- A trigger with an empty hand sends nothing and waits for the next interval.
- `once`, `continuous` and plain `use for` still cancel when their item is used up or the hotbar slot changes.

How `hold` works (use only):

- Each press holds use for `hold` ticks, then releases, like `use for`, so a bow or trident can shoot repeatedly.
- `hold` alone charges once.
- `hold ... for` (or `continuous hold`) starts the next charge right after the release.
- `hold ... interval <ticks>` starts a charge every interval ticks. The interval must be longer than the hold, so each
  release gets its own tick.
- A `for` deadline in the middle of a charge releases at once, which may fire a partly charged item. Pick a multiple
  of the press cycle to avoid it.
- Hotbar selection, a changed or empty held item, a dimension change, death and disconnect cancel holds. They never
  resume after reconnect.
- Each release confirms an input, not a shot.

`count <blocks>` is the `destroy`-only partner of `for`; see [mining blocks](#mining-blocks). Item-count conditions
for other inputs are not available.

The Jump, Attack, Use item, Move and Destroy blocks forms offer the same clauses (see [control forms](#control-forms)).

## Inventory

What a Bot carries, what it holds and what it drops (help page 3).

### Inventory and hotbar

See what a Bot carries and which slot it holds.

```text
/bot <name> inventory
/bot <name> hotbar
/bot <name> hotbar <1-9>
```

Slots are numbered as on the game screen, and every inventory command uses these labels:

| Label | Slot |
| --- | --- |
| 1–9 | Hotbar |
| 10–36 | Main inventory |
| `offhand`, `head`, `chest`, `legs`, `feet` | Offhand and armor |

Example: `inventory` lists slots like `3: diamond_pickaxe x1, 10: stone x64`.

- `inventory` lists only non-empty slots, without the `minecraft:` prefix, with counts.
- Tools and armor show remaining/maximum durability, for example `iron_pickaxe x1 (210/250)`.
- An empty inventory reports `inventory is empty`; equipment alone is still listed.
- `inventory` reads BDS directly and changes nothing. In forms, **Inventory... → List inventory** runs it.
- `hotbar` shows the selected slot and the hotbar items; `hotbar <1-9>` selects a slot.
- An offline Bot reports that it is not in the world. Ordinary Xbox players cannot be inventory targets.

Pickups show up by themselves, normally within about 0.2 seconds, so `hotbar`, `drop` and held-item actions can use
them without reconnecting. An empty or unknown selected slot fails with `Selected hotbar slot is empty`. If a picked-up
item seems missing and the server lacks the matching Endbot plugin, reconnecting the Bot recovers it.

### Swap and hold

Rearrange stacks or bring an item into the hand.

```text
/bot <name> swap <a> <b>
/bot <name> hold <item>
```

`swap <a> <b>` exchanges two whole stacks in slots 1–36.

- One empty side makes it a move. Equal slots or two empty slots are errors.
- Offhand and armor are not included; equipping is not available.
- Examples: `/bot Alice swap 10 2` exchanges a main and a hotbar slot; `/bot Alice swap 1 36` moves a hotbar stack
  into an empty main slot.

`hold <item>` takes an exact identifier, `diamond_pickaxe` or `minecraft:diamond_pickaxe`.

- If the item is on the hotbar, it selects the lowest matching hotbar slot, like `hotbar <n>`.
- Otherwise it swaps the lowest matching main slot into the selected hotbar slot, sending that slot's stack back.
- A missing item is reported by the identifier you asked for.

Replies describe requests, not results, for example `hold diamond_pickaxe: selected hotbar 3 requested` or
`hold diamond_pickaxe: moved slot 14 to hotbar 1 requested`.

In forms, **Inventory... / Hold item...** takes an item ID and **Swap slots...** offers two 1-36 dropdowns. They echo
the typed command, including any item prefix. Group targets accept swap and hold too.

### Dropping items

Drop items through normal Bedrock inventory actions.

```text
/bot <name> drop
/bot <name> drop stack
/bot <name> drop <1-36|offhand|head|chest|legs|feet>
/bot <name> drop <item> [count]
/bot <name> drop all
```

| Form | Drops |
| --- | --- |
| `drop` | One of the selected item |
| `drop stack` | The selected stack |
| `drop <slot>` | That slot's whole stack (slot labels as for `inventory`) |
| `drop <item>` | Every stack of that exact item (`stone` or `minecraft:stone`) |
| `drop <item> <count>` | Up to that many (1-10455), in increasing slot order, then offhand/head/chest/legs/feet |
| `drop all` | Every non-empty slot, including offhand and every armor slot (helmet too) |

Example replies: `drop slot 12 stone x5 requested`, `drop all requested for 41 slots`. When fewer items are found than
asked for, the reply gives both, for example `drop stone x3 requested (asked for 5)`.

- Numbers 1-36 and the equipment names are slots; `all` and `stack` are keywords; anything else is an item. Use the
  `minecraft:` prefix for an item named like a keyword or slot.
- Counts apply only to items.
- Replies say requested, not completed; BDS checks each drop. If BDS refuses one, the inventory shown corrects itself,
  which can take about one second.
- In forms, **Inventory... / Drop item...** offers one/stack, a slot, an item with optional count, and drop all. The
  form asks before drop all; typed commands run immediately.
- Group targets use the same grammar.

## Blocks and entities

How to point at a block or an entity, then interacting, mining, looking around and riding (help page 3).

### Targets: crosshair, coordinates, block types and `@` entities

Left- and right-click verbs stay separate (`attack`/`destroy`, `use`/`interact`), and their targets share one syntax:

| Form | Target | Examples |
| --- | --- | --- |
| none | what the Bot is looking at | `interact`, `destroy`, `attack` |
| `<x> <y> <z> [face]` | that block; `~` is relative to the Bot; without a face, the side facing the Bot | `interact ~~-1~`, `destroy 1 64 2 up` |
| `<block type>` | the nearest block of that type the Bot can reach within 6 blocks | `interact chest`, `destroy oak_log` |
| `@<type\|id\|nearest>` | the nearest matching non-player entity (`interact` within 6 blocks, `attack` within 4.5) | `interact @cow`, `attack @zombie`, `interact @9` |

`@` follows vanilla's entity-selector sign and is required for every entity, including `entity @cow` and ids
(`interact @9`). Listings show ids that way (`cow @9 (3.4m)`), and script data carries it as `selector`.

Repeating entity targets:

- `attack @zombie continuous|interval <ticks>|for <ticks>` aims at the nearest match on every trigger and does nothing
  while none is in reach. It never falls back to the crosshair, so a player there is never hit.
- `interact @cow count 5` (or `interval`/`for`) right-clicks a matching entity it has not clicked yet in this run, then
  the one clicked longest ago, so a herd is fed one by one.
- `count` counts clicks sent to an entity; BDS decides the outcome (a cow already in love eats nothing).
- After each click the next waits until BDS reports the changed held item (wheat eaten, a sword worn), or at most 10
  ticks, because a click with an outdated held item is rejected.
- `once` is a single click. `attack @cow stop`, `interact stop` or `stop` ends a run.
- `status` shows the running target and the clicks left, for example `interact @cow continuous 2 left`.

Block types:

- `destroy oak_log count 10` (or `continuous`/`for`) is a continuous mining loop: it turns to the nearest visible
  block of that type for each block and waits while none is in reach. It never walks.
- Unknown block types are reported as such, and `destroy @…` points to `attack`.

In Luau: `bot:interact("@cow")`, `bot:interact("chest")`, `bot:destroy("oak_log")`, `bot:attack("@zombie")`.

### Areas: `within` and `except`

One area value narrows the blocks an operation may pick. `within <area>` keeps only the blocks inside it,
`except <area>` leaves out the blocks inside it, and both take the same forms; the number of values selects the form:

| Area | Shape | Example |
| --- | --- | --- |
| `<x> <y> <z>` | one block | `except ~ ~-1 ~` (the block under the Bot) |
| `<x1> <y1> <z1> <x2> <y2> <z2>` | a box, both corners included, as for `/fill` | `within ~-3 ~ ~-3 ~3 ~10 ~3` |
| `<r>` | a sphere of radius `r` around the Bot | `within 6` |
| `<r> of <x> <y> <z>` | a sphere of radius `r` around a point | `within 4 of 10 70 -3` |

Areas apply to `blocks <type>`, `destroy <block type>`, `destroy continuous` (also through `for`/`count`) and
`interact <block type>`. Coordinates, the single crosshair block and `@` entities take no area.

Rules:

- An area only narrows candidates. It never widens reach or sight: `destroy` and `interact` keep their 6-block reach
  and line of sight, and `blocks` never looks past 16 blocks from the Bot.
- `~` (and the centre of `<r>`) is measured from the Bot when the command is accepted, so a continuous loop keeps its
  area while the Bot moves.
- `within` holds at most a radius-16 sphere (17157 blocks); a larger box is refused. `except` has no cap.
- Without an area: `blocks` scans a sphere of 8 around the Bot, typed `destroy` and `interact` pick from everything in
  reach, and a sight loop mines what the crosshair finds.
- The Bot's footing is not special: `except ~ ~-1 ~` protects it.
- Chat takes `within` and `except` once each, in any position after the operation. Luau scripts may chain several
  exclusions with `area:minus()` (see [areas in scripts](SCRIPTING.md#areas)).
- A continuous loop applies its area on every retarget. A sight loop whose crosshair rests outside the area waits as
  if nothing were in sight.
- Other numbers of values are syntax errors. Later shapes will take a leading shape word (`within cylinder ...`).
- `blocks ... radius` and `blocks ... at` were replaced by `within <r>` and `within <r> of <x> <y> <z>`; the old
  spellings fail with a message naming the new syntax.

`/bot help area` shows the table in game.

### Interacting with blocks

`interact` is a normal right-click on a block face.

```text
/bot <name> interact
/bot <name> interact <x> <y> <z> [face]
/bot <name> interact <block type> [within <area>] [except <area>]
```

BDS decides reach, legality, collision, placement and item use. A placeable held item can therefore place a block in
Survival, without Endbot editing the world. Targets are explained in
[targets](#targets-crosshair-coordinates-block-types-and--entities).

`use` and `interact` differ: `use` uses the held item in the air, `interact` right-clicks a block or an entity.

### Mining blocks

Break blocks with ordinary Survival mining: hold left-click on a block, or keep mining whatever comes into sight.

```text
/bot <name> destroy [once]
/bot <name> destroy <x> <y> <z> [face]
/bot <name> destroy continuous [for <ticks>] [count <blocks>] [within <area>] [except <area>]
/bot <name> destroy for <ticks>
/bot <name> destroy count <blocks>
/bot <name> destroy <block type> [continuous] [for <ticks>] [count <blocks>] [within <area>] [except <area>]
/bot <name> destroy stop
```

| Form | What it mines |
| --- | --- |
| `destroy`, `destroy once` | One block in the Bot's line of sight, within 6 blocks |
| `destroy <x> <y> <z> [face]` | That one block, if reachable and unobstructed; the Bot turns toward it |
| `destroy continuous` | Block after block along the Bot's sight, waiting when nothing is in reach |
| `destroy for <ticks>` | Continuous mining for that many ticks (same as `continuous for`) |
| `destroy count <blocks>` | Continuous mining until that many blocks are gone (same as `continuous count`) |
| `destroy <block type>` | The nearest block of that type the Bot can reach within 6 blocks ([targets](#targets-crosshair-coordinates-block-types-and--entities)) |
| `destroy <block type> continuous` (or `for`/`count`) | Continuous mining, turning to the nearest visible block of that type for each block |
| `destroy stop` | Stops mining, including a waiting continuous loop |

Example:

```text
/bot Alice hotbar 1
/bot Alice destroy continuous for 200 count 64
/bot Alice destroy ~ ~-1 ~ up
/bot Alice destroy acacia_log within 6 except ~ ~-1 ~ count 20
/bot Alice status
```

`destroy` takes the mode words `once` (the default), `continuous` and `stop`, like `jump`, `attack` and `use`. There
is no `interval` mode, because `continuous` already waits for the next block.

#### Before you start

- Select the tool with `hotbar` first; mining does not walk or pick tools.
- Starting clears movement, attack and item-use inputs.
- Relative coordinates are measured from the Bot, also for commands from the server console.
- A face uses the same names as `interact`.

#### What counts as in the way

- Grass, flowers, torches and other blocks without a collision box do not block a named block. The crosshair still
  stops at them, as a player's does.
- A refusal names the block in the way (see [targeting blocks](SCRIPTING.md#targeting-blocks)).
- Targeting works on whole block cells: a slab or fence stops an aimed line like a full block, while `blocks` treats
  it as see-through.

#### Continuous mining

`destroy continuous` holds left-click along the Bot's current sight. After each block it picks the next reachable
block along the same line. It stays in place and does not dig out an area.

When nothing is within 6 blocks, or an unbreakable block such as bedrock is in front, it waits. Placing a reachable
block in sight resumes mining by itself.

#### Ending after a time or a number of blocks

| Clause | Ends mining | Message when it ends |
| --- | --- | --- |
| `for <ticks>` | After that many input ticks from the command; a block still breaking is abandoned | `Endbot: Alice: destroy continuous stopped: duration elapsed` |
| `count <blocks>` | Once that many blocks are broken; a block already breaking is finished | `... stopped: block count reached` |

- Both values are 1–72000.
- `for` and `count` combine in either order; mining ends at whichever comes first.
- `count` counts blocks BDS reports broken, so waiting between blocks costs nothing and server load or TPS does not
  matter. `for` is wall-clock input ticks.
- These messages are info level and go to the player who started mining.
- Single-block `destroy` and coordinates take no `for` or `count`.

#### Status

The first reply names the target, for example
`Endbot: Alice: destroying minecraft:stone at 1 64 2; see status for the result`. No completion message is sent;
`status` shows the mining state:

```text
destroying minecraft:stone at 1 64 2                                (breaking now)
destroying minecraft:stone at 1 64 2 (continuous, 12/64 blocks)    (destroy count)
destroy continuous waiting for a block in sight (last: ..., 3 blocks)  (destroy continuous, nothing in reach)
last destroy: block broken or changed (minecraft:stone at 1 64 2)  (finished)
last destroy: block count reached (64/64 blocks, minecraft:stone at 1 64 2)  (count reached)
last destroy: failed: target out of reach (...)                    (failed)
last destroy: canceled by teleport (...)                           (canceled)
```

- While a `for` run is going, `status` shows the ticks left.
- A finished run keeps its total, for example
  `last destroy: duration elapsed (137 blocks, minecraft:stone at 1 64 2)`.
- A finished job stays visible as `last destroy` until the next one starts.

#### What BDS decides

BDS does the breaking: hardness and tool speed, drops, durability, and plugins that cancel the break. Endbot never
sets a block to air, runs `/setblock` or grants operator permissions.

Single-block mining ends when BDS reports the block changed, so a replacement or another player's change also ends
it. Continuous mining picks again. Endbot waits for the server instead of predicting instant breaking or loot.

A rejected or protected target may stay active until stopped, or until the 12000-input-tick timeout (about ten
minutes at 20 TPS).

#### Other commands while mining

- `move`, `look`, `attack`, `use`, `drop` and `interact` do not cancel mining. A continuous loop follows the new sight
  direction for its next block.
- Changing the hotbar slot or the held item (a pickup or a broken tool) restarts the current block with the new item.
- Attacking or using an item while mining sends both inputs; BDS decides the outcome.

These end the current block: a teleport, a dimension change, the target moving out of reach, or the 10-minute
timeout. Single-block mining ends there; continuous mining picks its next target.

#### When mining ends

Mining ends only on:

- `destroy stop` (mining only) or `stop` (mining and all movement and actions);
- an elapsed `for` duration or a reached `count`;
- death or disconnect;
- revocation of the starting player's permission;
- the plugin being disabled;
- Endbot's server side not responding for 200 input ticks (about ten seconds).

Continuous mining keeps running after its player logs off. Mining is not saved or resumed on reconnect.

The command reply confirms start and stop. When mining ends for any other reason (a failed single block, death,
disconnect, revocation, a lost runtime), the player who started it gets one chat message, for example
`Endbot: Alice: destroy continuous stopped: canceled by death`. Broken blocks are not announced.

For operators: start and stop are logged on the server console at info level, other endings at warning level (also
when the player is offline). The log stays English.

`attack` attacks entities; use `destroy` for blocks. The name follows the game's "Attack/Destroy" control.

### Looking around

Ask what is near the Bot. These read BDS and never change anything.

```text
/bot <name> target
/bot <name> block <x> <y> <z>
/bot <name> blocks <type> [limit <1-64>] [within <area>] [except <area>]
/bot <name> entity [@<type|id|nearest>]
/bot <name> entities
```

| Command | Shows |
| --- | --- |
| `target` | What the crosshair hits within 6 blocks: the nearer of a block (position, face, type) and a non-player entity |
| `block <x> <y> <z>` | One block's type and states, and whether the Bot can target it now (in reach, out of reach, or what is in the way) |
| `blocks <type>` | The nearest visible blocks of that type, as `x y z (distance)`, nearest first; those the Bot can target now are marked `in reach` |
| `entity [@...]` | One entity within 16 blocks in detail (id, distance, position, health, name tag); without an argument, the nearest |
| `entities` | Up to 10 non-player entities within 16 blocks, nearest first, as `<type> @<id> (<distance>m)` |

Example:

```text
/bot Alice blocks oak_log within 5 of ~ ~8 ~
```

Notes on `blocks`:

- It searches a sphere of 8 around the Bot or the given [area](#areas-within-and-except), never past 16 blocks from
  the Bot.
- It lists up to `limit` entries (default 10, at most 64).
- It counts only blocks a player could see: a face touching air, liquid, plants, leaves, glass, ice or a partial
  shape such as a slab or fence. Buried ores stay hidden.
- Search a tall structure with an area around the height that matters, as in the example.

Other notes:

- `~` is relative to the Bot; `block` refuses coordinates beyond 30000000.
- `target`, `block` and `blocks` also return data to scripts.
- A Group target prints every member's result, like `status`, with no history or audit entry.
- The Gameplay form's **Look around...** offers these with the nearby-entity list.

### Entities and vehicles

Right-click an entity, ride, and get off.

```text
/bot <name> interact @<type|id|nearest>
/bot <name> dismount
```

`interact @<type|nearest|id>` picks the nearest matching non-player entity within 6 blocks, turns the Bot toward it
and right-clicks it with the held item on the next input tick.

- `cow` and `minecraft:cow` are the same.
- `nearest` takes any type except dropped items, orbs, projectiles and effects.
- An id comes from `entities`.

It works like a player's right-click; BDS decides every outcome and the reply only confirms the request:

- feeding and breeding use up the held item;
- shears shear and a name tag names;
- an empty hand on a minecart, boat or rideable animal mounts it.

While riding:

- the Bot follows the vehicle; `status` and the Bot screen show `riding <type>`;
- `interact` on another vehicle switches to it, as a player can; `nearest` skips the vehicle being ridden;
- steering horses or boats is not available.

`dismount` leaves the vehicle. The Bot picks a free spot next to it (a solid floor with two passable blocks above), at
the vehicle's level, then one above, then one below:

- minecarts try, relative to travel: right, left, rear right, rear left, front right, front left, rear, front
  ([Minecraft Wiki](https://minecraft.wiki/w/Minecart#Dismounting));
- boats prefer the Bot's look direction ([Boat#Exiting](https://minecraft.wiki/w/Boat#Exiting));
- other rideables use the same ring.

With no free spot, the Bot stays where the vehicle was. A second `dismount` while one is pending is refused. If BDS
does not confirm the exit within 40 ticks, the Bot stops counting as riding.

A Bot whose floor is removed falls, also next to a boat. Standing on slabs, carpets, water, a boat or a shulker is
left alone.

## Other features

Groups, scripts and the web, each with its own page.

### Shared Group menus and results

Operate a Group from the same menus as a single Bot. Group syntax and selectors are in [GROUPS.md](GROUPS.md).

```text
/botgroup                     group browsing and creation (same as /botgroup menu)
/botgroup menu #miners        members and membership management, with Group operations...
/bot #miners menu             the shared Bot operation screen for the group
/botgroup result #miners [history]
/botgroup visibility #group shared|private
/botgroup owner #group <GamerTag>
```

#### Menus

- `/bot #miners menu` opens the Bot operation screen for the whole group, including teleport, lifecycle and chunk
  radius.
- Back from an operation screen returns to group management when you came from there, or to the target list when you
  opened it directly.
- **Group management...** links to the group's management screen; when you came from management, the existing **Back
  to group management** button is used instead.
- Members open their normal Bot screen.
- Deleting a group asks for confirmation and keeps the Bots.
- Forms submit the same `/bot` or `/botgroup` commands with the same permission checks. An outdated form is
  rejected.

#### Results

A whole-group command that changes something replies with at most two lines: counts and grouped failure types. This
confirms acceptance, not finished world actions. An unavailable response after inputs were sent is reported as
unknown acceptance.

`/botgroup result #miners [history]` prints every member's result for one operation:

- no number or `1`: the latest operation;
- `2`: the one before, and so on.

The menu shows the same results with `/botgroup menu #miners result [history]`, with older/newer buttons.

- Up to 20 operations are kept per player and group, and 128 overall; the oldest go first.
- Results are private to you and that group, and are lost on restart.

#### Queries

`/bot #miners status`, `inventory`, `hotbar` and `radius` print every member's result, labeled with `#group[index]`
and the Bot name.

- A failure fills that member's line; the other members are still queried.
- Indexed queries work the same way.
- Queries do not change `result` history, so checking state after an action keeps that action as the latest result.
- `hotbar <slot>` and `radius <value|default>` change things, so they reply with a summary and are kept in history.

#### Ownership

- The owner or the console sets a Group's visibility to `shared` or `private`.
- An unowned Group stays shared and is refused with a pointer to `owner`.
- Only the console reassigns a Group's owner; the GamerTag must be an online player or a controller.
- A private Group is left out of other players' lists and is "not found" for them.

The full Group behavior: [GROUPS.md](GROUPS.md).

### Scripts and triggers

Run Luau scripts and start them on events. Scripts work where the operator has turned them on
([setup](SCRIPTING_SETUP.md)).

```text
/botscript list
/botscript show <name> [page]
/botscript run <name> [positional arguments...]
/botscript runs
/botscript stop <run-id|name>
/botscript delete <name>
/botscript help
/botscript menu
/botscript trigger add <script> <kind> [field=value ...] [name=<name>] [visibility=shared|private] [-- <arg> ...]
/botscript trigger list
/botscript trigger show <name>
/botscript trigger enable <name>
/botscript trigger disable <name>
/botscript trigger remove <name>
/botscript trigger set <name> <policy>=<value> ...
/botscript trigger visibility <name> shared|private
```

Start with the [tutorial](SCRIPTING_TUTORIAL.md); [SCRIPTING.md](SCRIPTING.md) is the reference.

Example:

```text
/botscript run row #miners "~ ~ ~" 2
```

#### Running scripts

- A run uses your authority, checked again for every operation. It survives logout until it is stopped or your grant
  is revoked.
- Runs never resume after restart.
- Positional arguments follow the script's declaration order, then `name=value`. Quote a position, as in the example.
- Typed commands cannot write source. **Scripts...** in `/bot menu` (after **+ New Bot here**) does: shared source,
  line-by-line and book editing, templates, parameter forms, the source on each script's screen, stopping your runs
  and confirmed deletion.
- Turning scripting off keeps the source.

#### Triggers

A trigger binds an event kind and filter to a script and starts it for each occurrence.

- The script declares `param.event("event")`.
- `trigger add` and `trigger enable` approve the script's declaration and parameters, so only someone who may run the
  script can arm it.
- `area=` takes area values in one quoted token as `within` does (`"100 64 200"`, `"0 64 0 3 65 3"`,
  `"5 of 0 64 0"`), optionally with `except <values>`.
- `--` separates the script arguments.
- `trigger set` takes `concurrency`, `cooldown` (ticks), `startsPerMinute` and `queue`.
- The console addresses another owner's trigger as `<owner>/<name>` (display name or owner key).

Limits set by the operator:

- `[limits] max-triggers-per-controller`: default 8; `0` disables triggers.
- `max-watched-blocks-per-controller`: default 1024.

Pausing, disabling and the policy defaults: [SCRIPTING.md](SCRIPTING.md#triggers).

### Linking the web

Link your own account to the Endbot web from the game.

```text
/botweb link     a code for the Endbot web: 6 symbols, 5 minutes, one use
/botweb status   whether this player has a web account, with its passkey and session counts (the default)
/botweb unlink   delete this player's web account, passkeys and sessions
/botweb help     this list
```

- Only a player with a grant and Xbox authentication can link, and only for themselves.
- The web must be enabled on the server.
- A new code replaces an earlier one.
- Unlinking leaves the controller grant as it is.
- On the web, "To my position" in Teleport is `tp me`; it works while you are online in the game.

How the web works: [WEB.md](WEB.md).

## Appendix

Every syntax on one screen, and the limits of command autocomplete.

<a id="inputs-and-actions"></a>
### Syntax summary

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
/bot Alice interact [<x> <y> <z> [face] | <block type> [within <area>] [except <area>] | @<type|id|nearest>]
/bot Alice attack @<type|id|nearest> [once|continuous|interval <ticks>|stop] [for <ticks>] [count <n>]
/bot Alice interact @<type|id|nearest> [once|continuous|interval <ticks>|stop] [for <ticks>] [count <n>]
/bot Alice interact stop
/bot Alice destroy <block type> [continuous] [for <ticks>] [count <blocks>] [within <area>] [except <area>]
/bot Alice destroy continuous [for <ticks>] [count <blocks>] [within <area>] [except <area>]
/bot Alice entities
/bot Alice entity [@<type|id|nearest>]
/bot Alice target
/bot Alice block <x> <y> <z>
/bot Alice blocks <type> [limit <1-64>] [within <area>] [except <area>]
/bot Alice dismount
/bot Alice drop
/bot Alice drop stack
/bot Alice drop <1-36|offhand|head|chest|legs|feet>
/bot Alice drop <item> [count]
/bot Alice drop all
/bot Alice stop
```

Coordinates anywhere in `/bot` accept vanilla's compact relative form: `~~-1~` is `~ ~-1 ~`.

### Autocomplete

Every documented form works when typed, but in-game autocomplete only partly helps. Bot names are not suggested; use
`/bot list` to find them. Why: [command declaration](BOT_CONTROL.md#command-declaration).
