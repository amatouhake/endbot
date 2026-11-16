# Luau scripting reference

Scripts are Luau programs that drive Bots. Each script belongs to the controller who created it (see
[ownership](#ownership-visibility-and-revisions)), and each run uses the authority of whoever started it. This page
lists everything a script author needs to look up.

- New to scripts? Start with the [tutorial](SCRIPTING_TUTORIAL.md).
- Scripting is **off by default**. Turning it on, its limits and how runs are confined are the operator's side:
  [SCRIPTING_SETUP.md](SCRIPTING_SETUP.md).
- How it works inside: [SCRIPTING_INTERNALS.md](SCRIPTING_INTERNALS.md). What was checked on real servers:
  [validation status](COMPATIBILITY.md#scripting).

## Commands

`/botscript` manages and runs scripts. It requires `endbot.command.control`; creating and changing scripts also
depends on your role ([roles](PERMISSIONS.md#roles)).

```text
/botscript list
/botscript show <name> [page]
/botscript run <name> [positional arguments...] [name=value...]
/botscript runs
/botscript stop <run-id|name|all>
/botscript delete <name>
/botscript copy <name> <new-name>
/botscript visibility <name> shared|private
/botscript owner <name> <GamerTag>
/botscript history <name>
/botscript restore <name> <n>
/botscript rename <name> <new-name>
/botscript import <name>
/botscript export <name> [overwrite]
/botscript reload
/botscript help
/botscript menu
```

| Subcommand | What it does |
| --- | --- |
| `list` | Lists the scripts you can see as `name (owner, visibility)`, with `yours` for your own |
| `show <name> [page]` | Shows the source in numbered pages; long lines wrap, no source is dropped |
| `run <name> ...` | Starts a run; arguments are described under [passing arguments](#passing-arguments) |
| `runs` | Lists your runs |
| `stop <run-id\|name\|all>` | Stops your runs by id, by script name, or all of them |
| `delete <name>` | Deletes a script you own; running copies keep running |
| `copy`, `visibility`, `owner`, `history`, `restore`, `rename` | See [ownership, visibility and revisions](#ownership-visibility-and-revisions) |
| `import`, `export` | Read from or write into a held book; see [books](#books) |
| `reload` | Re-reads the operator's [script files](#script-files) |
| `help` | Shows `/botscript` help |
| `menu` | Opens the [script menu](#the-script-menu) |

Notes:

- Start, stop, save and delete are logged with your name, and so is the end of each run.
- Source is never loaded from a filesystem path you type. The only files read are the operator's
  [script files](#script-files).
- Command completion hints work as for `/botgroup`, with the same gaps the pinned BDS version has.

Console only:

- The console's `runs` lists every controller's runs with their owner.
- The console's `stop <id|name|all>` stops any of them, so a run left behind by a controller who logged out can be
  ended without revoking that controller.

### Ownership, visibility and revisions

Each script records the controller who created it as its owner. Only the owner, or the console, may edit, delete,
restore or change its visibility; anyone else gets "only the owner may change" and is pointed to **Copy and edit**.

| Visibility | Who sees and runs it |
| --- | --- |
| `private` | Only the owner; to everyone else it does not exist |
| `shared` | Every controller may list, show, run and copy it |

- New scripts and copies start `private`. `visibility <name> shared|private` switches it.
- The old `share <name>` and `private <name>` spellings (0.4.0) were removed in 0.6.0; they are refused with a
  pointer to `visibility`.
- `copy <name> <new-name>` makes a private copy owned by you.
- `rename <name> <new-name>` (owner only) keeps the owner, visibility and history. Running copies keep the old name.
- Every save keeps the previous source. `history <name>` lists the last 10 revisions, newest first, with time and line
  count.
- `restore <name> <n>` makes revision `n` current as a new save; the replaced source stays in the history.
- A run always uses the authority of whoever started it, whoever owns the script.

Notes:

- Scripts saved before ownership existed (file version 1) have no owner and stay shared. The first controller to
  edit one becomes its owner.
- Console only: `owner <name> <GamerTag>` reassigns a stored script's owner. Visibility, revision and history stay.
- A [script file](#script-files) has no owner to reassign; the file is the source.

### The script menu

When scripting is on, `/bot menu` lists **Scripts...** after **+ New Bot here**, next to **Manage groups...**. The
target list above it contains only targets `/bot` can address.

A script's screen shows its whole source with gray line numbers; wrapped lines stay apart from the numbers. Very long
scripts are cut off with a pointer to `show`. A `§` in the source is shown as `\u{a7}`, so source text cannot color, hide or scramble the preview.

| Button | Who gets it |
| --- | --- |
| Run | Everyone who can see the script |
| Stop runs | Everyone who can see the script |
| Copy and edit | Everyone except the owner (the owner finds it under More) |
| Write into held book ([books](#books)) | Everyone, where export is enabled |
| **Edit lines...** ([line editor](#edit-lines)) | Owner |
| **More...**: Rename, Share/Make private, Earlier revisions, Copy and edit, Write into held book, Delete | Owner |

**Earlier revisions** shows a revision's source and asks before restoring it. **Delete** asks for confirmation.

**Creating a script.** The create screen offers these starting points. A template or copy only asks for a name, and
saving opens the new script.

- **Group row**
- **Simple patrol**
- **Mine at spots** (below)
- A blank script
- **From held book...** ([books](#books))

**Mine at spots** takes `name`, `start`, `spots` (default 2), `step` (default 4) and `blocks` (default 2), in that
order. It teleports along +X from `start` at `step` spacing, faces +Z, runs `destroy{mode="continuous",count=blocks}`,
waits up to 1200 ticks and logs `spot N: <reason>` at each position. Put reachable breakable blocks in front of each
spot before running it.

#### Edit lines

**Edit lines** lists the source one line per button, 40 per page.

- Tapping a line opens a form with that line filled in, an optional line to add below it, and a **Delete this line**
  toggle. The added line takes the edited line's indentation unless you type your own.
- **+ Add a first line** and **+ Add a line at the end** add lines at the ends.
- **Replace all from held book** at the bottom of the list replaces the whole script from a book.

Notes:

- On the Windows client, form fields **drop pasted line breaks**, so make multi-line changes through a
  [book](#books).
- Saving checks that the whole script compiles and that its declaration is valid, then returns to the same page of
  the list. A form that changes nothing just goes back.
- A form opened before the script changed is refused when submitted.
- Canceling does not save, and drafts are not kept.

### Books

A book and quill is the comfortable in-game editor: it keeps line breaks and supports Ctrl+A/C/X/V on Windows.

```text
/botscript import <name>
/botscript export <name> [overwrite]
```

**The multi-line loop:**

1. Create a script from a template (or blank) and open it.
2. **Write into held book** (`export`).
3. Edit the pages.
4. **Replace all from held book** (`import`) to save a new revision.

The book keeps the whole source between edits, so one export is enough.

**Import** reads the book and quill or written book in your main hand. The book is only read.

- Pages are joined with a line break unless a page already ends with one. Trailing blank pages are ignored.
- A new name creates a private script. An existing name saves a new revision, with the same compile, declaration and
  owner checks as the menu.
- In the menu, **From held book...** refuses a name that already exists.
- In the menu, **Replace all from held book** first shows the book's source with both line counts. It replaces the
  script only after **Replace**, and only if the book still holds that text.
- A typed `import` of an existing name saves at once. The previous source stays in Earlier revisions.

**Export** writes a script you can run into the book and quill in your main hand. To edit someone else's shared
script, import it again under a new name.

- An empty book is written at once.
- A book that already has writing is overwritten only by `export <name> overwrite`; plain `export` refuses it.
- In the menu, the book's current text and page count are shown first. **Overwrite** runs `export <name> overwrite`
  and writes only if the book still holds those pages.
- Written (signed) books are never changed, and no item is created.

Limits:

- Pages hold whole lines: up to 256 characters per page and 50 pages. A longer line or script is refused, and the
  offending line is named. These limits are deliberately conservative.
- The client's own book editor silently drops text past a page's capacity, so split long source over pages by hand.
- Export is on by default. An operator who wants the plugin never to change a held item sets
  `[scripting] book-export = false`.

### Optional UI pack field

With the optional [Endbot UI resource pack](UI_PACK.md) added as a global resource, players get a larger form window,
compact buttons and a multi-line field.

With `[scripting] ui-pack-editor = true` (default false):

- The line editor also offers **Edit all at once (UI pack)...**: the whole source in one field, saved like any other
  edit.
- The new-script screen offers the template in the same field, so a script can be written whole before it exists.

Without the pack:

- The client drops the line breaks, and that reply is refused rather than saved. On the new-script screen, the
  **Save as shown** toggle saves the template unchanged instead.
- A form input stops at about 100 characters. Edit a longer line with the pack, in a script file, or through a book.
- For clients the pack has not been checked on, use [books](#books) for multi-line editing.

### Script files

To write scripts in a PC editor, the operator puts `<name>.luau` files directly in `<instance>/scripts/`, next to
`endbot.toml`. Setup and start-up create the folder.

They are read when the server starts and again by `/botscript reload` (console, any controller, or **Reload script
files** in the script list). Nothing watches the folder, and runs already going keep the source they started with.

- A file script is **shared** with every controller and **read-only** in game. Run, Stop runs, show, Copy and edit and
  export work; editing, rename, delete and sharing refuse with a pointer to the file.
- Lists show its owner as `file`. Copying makes an ordinary stored script owned by you.
- It has no revision history; version the folder yourself if you need one.

Which files load:

- Only regular `.luau` files (UTF-8, CRLF accepted) with a valid script name and at most `max-script-bytes` load.
  Links, other extensions and subfolders are ignored or refused. At most `max-scripts` files load.
- A stored script with the same name wins, even someone else's private one (names are server-wide). The file is
  skipped as "Not loaded: a stored script named … already exists". The console and the start-up log also name that
  script's owner.

What `reload` checks:

- `reload` runs each file's declaration check and replies with every skipped file and why. Start-up only reads the
  files and logs skipped ones to the server console.
- A file is dropped only for its own fault: a declaration error, or hanging before `params`.
- When the check cannot run (your run slots are busy, the runner is missing), the file stays loaded and the reply
  says it was not checked.
- A file that hangs before `params` takes about 5 seconds to check, and `/bot` commands wait while it is checked.

### Names and storage

- Names are case-insensitive and follow Groups: 1–16 ASCII letters, digits and underscores, starting with a letter
  or underscore.
- A failed save leaves the previous version intact.
- Runs are not resumed or replayed after a restart.

## Writing a script

### Parameters

Every script calls `params` once, before any Bot, log or wait call, even without arguments (`params {}`).

```lua
local a = params {
    param.group "group",
    param.pos("origin", "~ ~ ~"),
    param.number("spacing", 2),
}
for i, member in group(a.group):members() do
    local result = member:tp(a.origin + vector.create((i - 1) * a.spacing, 0, 0))
    assert(result.ok, result.reason)
end
```

| Constructor | Notes |
| --- | --- |
| `param.group(name[, default])` | A Group, as in `group(a.group)` above |
| `param.bot(name[, default])` | A Bot |
| `param.pos(name[, default])` | Arrives as a vector ([passing arguments](#passing-arguments)) |
| `param.number(name[, default])` | A finite number |
| `param.bool(name[, default])` | `true` or `false` |
| `param.string(name[, default])` | Text |
| `param.choice(name, {"a", "b", ...}[, default])` | 1–32 distinct strings of 1–64 characters; the run form shows a dropdown |
| `param.event(name)` | The occurrence for a [trigger](#triggers) run, nil for a manual run |

Each declaration carries its name, and you read the value as `a.<name>`.

Rules:

- Pass a contiguous array of declarations. Named-key, mixed and sparse tables are refused, as are duplicate names,
  unknown types and invalid defaults.
- Names match `[A-Za-z_][A-Za-z0-9_]{0,31}`; at most 32 parameters.
- The script is checked by running it up to `params`. Code before `params` has the normal budgets but cannot call
  Bot, log or wait functions.
- Declaration errors show the script line of the `params` call where available, and are translated for Japanese
  players.

### Capabilities

A script that needs more than the basic set declares it with `requires`, once and before `params`.

```lua
requires { "observe.chat" }
params {}
```

| Capability | Needs `requires` | Covers |
| --- | --- | --- |
| `act.bot`, `read.world`, `wait`, `log` | No (naming them changes nothing) | Operating Bots, reading the world, waiting and logging |
| `observe.presence` | Yes | The `player.join` and `player.leave` [event kinds](#event-kinds) |
| `observe.chat` | Yes | The `player.chat` event kind |

Notes:

- Any other name is an error naming it. A second call, a call after `params`, or anything but a list of distinct
  strings is a declaration error. Order does not matter.
- The run form shows the declaration on its first line.
- A run started from a checked declaration (the run form, a parameterless menu start, a trigger) is held to it. If
  its own `requires {}` comes out different, it stops before its first operation, so a conditional call cannot widen
  what was shown.
- Why scripts declare capabilities: [PERMISSIONS.md](PERMISSIONS.md#two-layers-the-person-and-the-script).

### Passing arguments

Positional arguments and menu controls follow declaration order. After the positional values, `name=value` sets any
declared parameter, so earlier defaults can be skipped.

```text
/botscript run row #miners
/botscript run row #miners "~ ~ ~" 3
/botscript run row #miners spacing=3
/botscript run row #miners origin="0 70 0"
```

| Type | Accepts |
| --- | --- |
| Group, Bot | An existing Group or Bot |
| Position | `x y z`; quote it when it has spaces. `~` resolves from your position when you submit; console positions must be absolute |
| Number | A finite number |
| Boolean | `true` or `false` |
| Choice | One of its options |
| String | Text; quote it when it has spaces |

Rules:

- A positional value may not follow a named one, and a parameter may be given only once.
- `word=...` whose `word` is not a declared parameter stays an ordinary positional value.
- The run form uses the same order: Group, Bot and choice dropdowns, position fields defaulting to your position,
  number and text inputs, and toggles with the declared defaults.
- A Bot or Group picked in the form, or given as a Bot argument, stays tied to that exact Bot or Group. If it is
  deleted, or deleted and recreated under the same name, the form or the later calls are refused.

Positions arrive as Luau **vectors**. Read `a.origin.x`, add and scale them (`a.origin + vector.create(2, 0, 0) * i`),
and pass one wherever an operation takes `x, y, z` (`bot:tp(a.origin)`).

### Results and codes

An operation call returns **acceptance**, not completion.

```lua
local r = bot("Alice"):jump()
-- {ok=true, code="ok", messages=..., data=...}
-- {ok=false, code=..., reason=..., messages=...}
assert(r.ok, r.reason)
```

| `code` | Meaning |
| --- | --- |
| `ok` | Accepted |
| `unauthorized` | You lack the permission |
| `not_found` | No such Bot or target |
| `invalid_argument` | Fix the request |
| `unavailable` | Try again later |
| `conflict` | The Bot's state does not allow it now, for example offline or a name in use |
| `not_a_target`, `out_of_reach`, `blocked`, `unbreakable` | The Bot cannot target the block; see [targeting blocks](#targeting-blocks) |

Notes:

- Branch on `code`; it is the same in every language. `reason` is wording for people.
- Handle refused calls explicitly, for example `assert(r.ok, r.reason)`.
- Group operations include counts in `data`. A partial failure has `ok=false`, and the Group's `code` is its first
  failed member's. Each query row also carries its member's `code`.
- Revocation, the run deadline, protocol errors and more than `4 × operations-per-second` messages in one
  second (logs and zero waits count) end the run instead of returning.
- An operation over `operations-per-second` does not fail: it waits for a free slot, and the run log notes the
  first such wait.
- There is no command-string API: a script cannot run a typed command.

### Logging and waiting

```lua
wait(20)                       -- 20 ticks = one wall-clock second
waitOnline(bot("Alice"))       -- also accepts a name or a Group handle
log("done")
```

- `wait(ticks)` pauses the run; 20 ticks are one wall-clock second. Waits recheck your authority.
- `waitOnline(...)` waits until the Bot (or every Group member) is online. See [jobs and waits](#jobs-and-waits) for
  timeouts.
- `log(text)` writes one line, tagged with the run id, to the server log and to the chat of the player who started
  the run.

`log` limits:

- Chat gets at most 10 lines per 5 seconds per run, each cut to 256 characters with formatting codes removed.
  Held-back lines are counted on the next chat line or in the end message.
- The server log keeps every line, up to 2000 lines or 512 KiB per run.
- Console runs log only to the server log.
- `[scripting] chat-logs = false` (plugin `config.toml`, or `endbot.toml` for managed instances) keeps logs in the
  server log only.

### Operation replies in chat

Every operation a script issues is also a normal `/bot` command, so its reply would reach your chat as if typed.
`[scripting] replies` (plugin `config.toml`, or `endbot.toml` for managed instances) chooses what is echoed:

| Value | Echoed to chat |
| --- | --- |
| `all` | Every operation, like a typed command |
| `failures` (default) | Only refused operations; accepted ones keep their console log line |
| `none` | Nothing |

Notes:

- Under `failures`, a `destroy` refused as `not_a_target` is not echoed, because its target can vanish after the
  script read it ([targeting blocks](#targeting-blocks)). The script still receives the refusal.
- The run's start and end lines, `log()` and the mining loop's start and stop notices are unaffected.
- Scripts cannot change the policy.

### Structured status and mining waits

`bot:status()` returns the usual result; read its `data` fields below.

```lua
local s = bot("Alice"):status().data
if s.mining.active then log("still mining " .. s.mining.blocksBroken) end
```

| Field | Meaning |
| --- | --- |
| `data.name` | Current Bot name |
| `data.identityId` | Bot UUID |
| `data.connectionState` | Runtime connection state; `online` means connected |
| `data.vehicle` | `{type, runtimeId}` while riding, else nil |
| `data.position` | Feet position `{x, y, z}` |
| `data.dimension` | `Overworld`, `Nether` or `TheEnd` |
| `data.look` | `{yaw, pitch}` in degrees: yaw 0-360 with 0 south (+Z) and 90 west (-X), pitch -90 (up) to 90 (down), the same convention as `look` |
| `data.health`, `data.maxHealth` | BDS health |
| `data.hunger`, `data.saturation` | Food level (0-20) and saturation |
| `data.onGround`, `data.inWater` | Booleans |
| `data.selectedSlot` | Selected hotbar slot, 1-9 |
| `data.held` | `{name, count}` of the selected item, or nil when empty |
| `data.mining.active` | Boolean: a block destroy job or continuous mining loop is active (including waiting for a target) |
| `data.mining.mode` | `once` or `continuous`; nil before any mining job |
| `data.mining.state` | Runtime job state (`active`, `waiting`, `completed`, `canceled`, `failed`), or nil with no job |
| `data.mining.blocksBroken` | Runtime continuous-loop count; for a single block, 1 if completed, otherwise 0; 0 with no job |
| `data.mining.blockCount` | Continuous count limit, or nil when unlimited/no job |
| `data.mining.reason` | Nil while active; otherwise the runtime's terminal reason, or nil with no job |
| `data.mining.target` | `{x, y, z, type}` of the block being broken while `state` is `active`, else nil |
| `data.running` | Running input job ids by kind (`move`, `jump`, `attack`, `use`, `interact`); nil when none runs |
| `data.ended` | The last end of each input kind: `{job, reason}` |
| `data.teleports` | Server teleports the Bot has acknowledged since the runtime started |

Notes:

- Position, look, health and inventory are read from BDS at the time of the call. Hunger and saturation are what BDS
  last sent the Bot.
- A field is nil when the Bot is not in the world, or before BDS has sent it.
- Mining values report the Bot's actual mining job, including the last one that ended. They never guess completion
  from elapsed time.
- Terminal reasons include `block count reached`, `duration elapsed`, `stopped`, and runtime failure reasons.
- `group:status()` returns `data.results` in membership order. Each row has `index`, `identityId`, `name`, `ok`,
  `reason` (nil on success) and `data` with that member's fields above (nil on query failure). A partial query
  failure sets the outer `ok=false`.
- To wait for mining, use [`waitMining`](#jobs-and-waits) or a `destroy` job's `wait`.

### Other queries

| Call | `data` |
| --- | --- |
| `bot:inventory()` | `slots`: non-empty slots as `{slot, name, count}`; slots `"1"`-`"36"`, `offhand`, `head`, `chest`, `legs`, `feet` |
| `bot:hotbar()` | `selectedSlot` and the hotbar `slots` |
| `bot:entities()` | `entities`: up to 10 nearby non-player entities, nearest first, as `{runtimeId, type, distance, position, health, maxHealth, nameTag}`; `health` is nil for non-mobs |
| `bot:entity("@cow")` | `entity`: one such entry within 16 blocks; also `"@9"`, `"@nearest"` or no argument |

- Inventory, hotbar and `held` items carry `durability = {remaining, max}` for damageable items such as tools and
  armor.
- Pass an entity's `selector` (`"@9"`) to `:interact(e.selector)` or `:attack(e.selector)`. Entities always take `@`.
- Repetition uses the final options table: `bot:interact("@cow", {count=5})`,
  `bot:attack("@zombie", {interval=20, ticks=200})`, `bot:destroy("oak_log", {count=10})`.

### World queries

World queries read BDS and count as operations toward the rate limit.

| Call | `data` |
| --- | --- |
| `bot:target()` | `kind` (`"block"`, `"entity"` or nil), `distance`, and `block = {x, y, z, face, type}` or `entity = {runtimeId, type}`: the nearer hit within 6 blocks of the Bot's eyes, ignoring players |
| `bot:block(x, y, z)` | `{x, y, z, type, states, reachable}`, plus `reachCode` and, for `blocked`, `obstruction` when not reachable; `"~"`/`"~-1"` strings are relative to the Bot; any loaded position |
| `bot:blocks("oak_log", {limit=10})` | `{type, radius, blocks}`; `blocks` lists `{x, y, z, distance, reachable}` nearest the Bot first, only blocks a player could see (no x-ray, see below), within a sphere of 8 around the Bot; limit 1-64 |
| `bot:blocks("oak_log", {area=area.sphere(5, x, y, z)})` | `{type, area, blocks}`: the same query narrowed to an [area](#areas), for example around a treetop; never past 16 blocks from the Bot |

`blocks(type, x, y, z)` and `{radius = r}` were removed; calling them fails with a message naming
`blocks(type, {area = area.sphere(r, x, y, z)})`.

### Areas

An area narrows the blocks that `blocks`, typed `destroy`, `destroy` continuous loops and typed `interact` may pick.
Build it with `area`, then pass it as `{area = a}`.

| Luau | Chat | Shape |
| --- | --- | --- |
| `area.cell(x, y, z)` or `area.cell(v)` | `within <x> <y> <z>` | one block |
| `area.box(x1, y1, z1, x2, y2, z2)` or `area.box(v1, v2)` | `within <x1> <y1> <z1> <x2> <y2> <z2>` | a box, both corners included, as for `/fill` |
| `area.sphere(r)` | `within <r>` | a sphere of radius `r` around the Bot |
| `area.sphere(r, x, y, z)` or `area.sphere(r, v)` | `within <r> of <x> <y> <z>` | a sphere of radius `r` around a point |
| `a:minus(b)` | `except <b>` | `a` without the blocks of `b`; `b` takes any form above |

```lua
local footing = area.cell("~", "~-1", "~")
local job = bot:destroy("acacia_log", {count = 20, area = area.sphere(6):minus(footing)}).job
job:wait{}
```

- Coordinates are numbers, vectors, or `"~"`/`"~-1"` strings relative to the Bot.
- `:minus` chains freely (`area.sphere(6):minus(footing):minus(trunk)`); chat takes one `except`.
- Only these functions build an area. A table or a string is refused.
- The Bot's footing is not special-cased, so exclude it explicitly, as in the example.

The rules are the [command rules](COMMANDS.md#areas-within-and-except):

- An area only narrows; it never widens reach or sight.
- `~` resolves when the operation is accepted, so a continuous loop keeps its area while the Bot moves.
- `within` holds at most a radius-16 sphere.
- Without an area, each operation keeps its default.

`interact` takes one `target` argument for both a block type and an `@entity` selector, so `{area = a}` is part of
its signature. It applies only to a block type (`interact("chest", {area = ...})`); with an `@entity` target the call
is refused with "An area applies to a block type …".

### Targeting blocks

Three questions about a block have one answer each.

| Question | Rule | Used by |
| --- | --- | --- |
| Could a player see it? | One of its faces touches a block light passes: air, liquids, plants, leaves, glass, ice and partial shapes such as slabs, stairs, fences, walls, panes and doors | which blocks `blocks` lists |
| Can the Bot target it now? | It is not air or liquid, part of it is within 6 blocks of the Bot's eyes, and a straight line reaches a side facing the Bot through blocks without a collision box only (air, liquids, grass, flowers, torches, rails, snow layers). Leaves, glass and partial shapes stop the line | `reachable`, `destroy x y z`, `destroy <type>`, `interact x y z`, `interact <type>` |
| What is under the crosshair? | The first block along the Bot's look within 6 blocks that is not air or liquid, as for a player: grass and torches are hit, light blocks are not | `target`, `destroy` and `interact` without a target |

So a log behind tall grass is reachable by name or coordinates, while the crosshair hits the grass in front of it. A
log wrapped in leaves is listed by `blocks` but not reachable until a leaf is broken.

When a block cannot be targeted, `block` reports `reachable = false` and `reachCode`, and `destroy`/`interact` fail
with the same value as `code`:

| Code | Meaning |
| --- | --- |
| `not_a_target` | Air or liquid, or nothing under the crosshair |
| `out_of_reach` | No part of it is within 6 blocks of the Bot's eyes |
| `blocked` | Something stops every line to it; `obstruction = {x, y, z, type}` (in `data` for a refused operation) names the first block in the way, when one is found |
| `unbreakable` | `destroy` only: bedrock, barriers and other blocks Survival cannot break; they can still be targeted |

```lua
local info = bot:block(x, y, z).data
if not info.reachable and info.reachCode == "blocked" and info.obstruction then
  local o = info.obstruction
  bot:destroy(o.x, o.y, o.z)  -- clear the leaf first
end
```

Notes:

- `block` and `blocks` are reads, not reservations. `reachable` describes the moment of the read, and `destroy` and
  `interact` check the target again when they run.
- A block can vanish in between: the Bot's own previous swing is still resolving, a leaf decays, or another Bot or
  player breaks it. The operation then fails with `not_a_target`.
- A script that only wants the block gone can treat `not_a_target` on `destroy` as done.

### Jobs and waits

A continuing operation's result carries its **job**: whatever the Bot keeps doing after the operation returns. Wait
for it with `job:wait`.

```lua
local r = bot("Alice"):move("forward", {ticks = 40})
local w = r.job:wait{timeout = 200}   -- {ok, code, reason, results}
if w.ok then log("stopped: " .. w.reason) end  -- duration elapsed, count reached, completed, stopped, replaced, ...
group("#miners"):destroy("stone", {count = 64}).job:wait{timeout = 2400}
```

- `move`, `jump`, `attack`, `use` and `interact` (any mode that keeps running, including `use` once with `hold`),
  `destroy` and `tp` return a job. On a Group the job covers every member that started one.
- A `tp` job ends with `arrived` once the Bot has acknowledged the server teleport.
- A `destroy` job waits like `waitMining`.

**Every wait** takes `{timeout = ticks}` (default 1200) and returns `ok`, `code`, `reason` and, for Bot and Group waits, one row per
member with `name`, `identityId`, `ok`, `code` and `reason`.

| Outcome | Result |
| --- | --- |
| The job ended, for any reason | `ok=true` with that reason (`stopped`, `replaced` by a newer command of the same kind, `died`, `disconnected`, `held item changed`, ...) |
| Timeout | `ok=false, code="timeout"`; the job keeps running |
| Member offline | `ok=false, code="conflict", reason="offline"` |
| Member no longer running, and its end overwritten by a later job of the same kind | `ended` |

**`waitOnline`.** `bot:waitOnline{timeout=...}` (also on Groups) waits until the target is online and returns
`ok=false, code="timeout"` at its timeout. Without `timeout` it waits like `waitOnline(b)`.

**`waitMining`.** `bot:waitMining{timeout=1200}` and `group:waitMining{timeout=1200}` wait only for destroy and mining
jobs; movement and other continuing inputs do not delay them.

- They return `{ok, code, reason, results={...}}`. Each result has `name`, `identityId`, `ok`, `code`, `reason` and
  `mining` (the [status fields](#structured-status-and-mining-waits)).
- An inactive online member returns its terminal reason, or `idle` with no job. It is `ok=true` unless its state is
  `failed`.
- An offline member returns `ok=false, reason="offline"`. A member still active at the timeout returns
  `ok=false, reason="timeout"`; the timeout does not stop its mining job.
- A query failure returns `ok=false` with the query reason.
- The outer `ok` requires every member to succeed. The outer `reason` is the shared reason, `completed` for mixed
  successful reasons, or the first failing member's reason. Per-member results keep mixed outcomes.

`waitMining` notes:

- The timeout defaults to **1200 ticks (60 wall seconds)** and must be an integer from 0 to 2147483647; 0 performs
  one status check.
- Stop, revocation, shutdown or the optional positive run deadline ends the run, rather than returning a successful
  wait.
- A Group wait covers all current members. Membership and each member's identity are fixed at the first check; if the
  membership is replaced, the run ends. A literal Bot's identity is also fixed at the first check.
- It checks every `max(0.25, 1 / operations-per-second)` wall seconds, the last check shortened to the remaining
  timeout. A timeout uses the last check's values.
- Each check counts toward the operation rate limit and rechecks your authority and stop.
- `waitOnline(b)` and `b:waitMining{}` stay as aliases until 1.0, so existing scripts keep working.

## Generated API reference

The table lists every method, generated from the same operation registry `/bot` uses; ranges and option
combinations are checked exactly as for the typed command. Methods are available on `bot("Alice")` and
`group("#miners")`; whole-group rename and forget keep their one-Bot restriction.

<!-- BEGIN GENERATED OPERATIONS -->
| Method | Positional fields | Final options table |
| --- | --- | --- |
| `:status()` | none | none |
| `:inventory()` | none | none |
| `:entities()` | none | none |
| `:entity()` | none | none |
| `:entity()` | target: string | none |
| `:target()` | none | none |
| `:block()` | x: coordinate, y: coordinate, z: coordinate | none |
| `:blocks()` | type: string | limit: integer, area: area |
| `:resume()` | none | none |
| `:reconnect()` | none | none |
| `:despawn()` | none | none |
| `:forget()` | none | none |
| `:visibility()` | visibility: shared / private | none |
| `:stop()` | none | none |
| `:dismount()` | none | none |
| `:menu()` | none | none |
| `:spawn()` | none | none |
| `:spawn()` | x: coordinate, y: coordinate, z: coordinate | yaw: number, pitch: number, dimension: string |
| `:rename()` | name: string | none |
| `:owner()` | gamertag: string | none |
| `:tp()` | target: player | none |
| `:tp()` | x: coordinate, y: coordinate, z: coordinate | yaw: number, pitch: number, dimension: string, facing: player, facingX: coordinate, facingY: coordinate, facingZ: coordinate |
| `:move()` | direction: forward / backward / left / right / stop | ticks: integer |
| `:look()` | yaw: number, pitch: number | none |
| `:look()` | x: coordinate, y: coordinate, z: coordinate | none |
| `:swap()` | a: integer, b: integer | none |
| `:hold()` | item: string | none |
| `:hotbar()` | none | none |
| `:hotbar()` | slot: integer | none |
| `:radius()` | none | none |
| `:radius()` | value: integerOrDefault | none |
| `:interact()` | none | none |
| `:interact()` | x: coordinate, y: coordinate, z: coordinate | none |
| `:interact()` | x: coordinate, y: coordinate, z: coordinate, face: down / up / north / south / west / east | none |
| `:interact()` | target: string | mode: once / continuous / stop, interval: integer, ticks: integer, count: integer, area: area |
| `:destroy()` | none | mode: once / continuous / stop, ticks: integer, count: integer, area: area |
| `:destroy()` | x: coordinate, y: coordinate, z: coordinate | face: down / up / north / south / west / east |
| `:destroy()` | type: string | mode: continuous, ticks: integer, count: integer, area: area |
| `:drop()` | none | none |
| `:drop()` | selector: stringOrInteger | count: integer |
| `:jump()` | none | mode: once / continuous / stop, interval: integer, ticks: integer |
| `:attack()` | none | mode: once / continuous / stop, interval: integer, ticks: integer |
| `:attack()` | target: string | mode: once / continuous / stop, interval: integer, ticks: integer, count: integer |
| `:use()` | none | mode: once / continuous / stop, interval: integer, hold: integer, ticks: integer |
| `:sprint()` | value: boolean | none |
| `:sneak()` | value: boolean | none |
| `:list()` | none | none |
| `:ping()` | none | none |
| `:help()` | none | none |
| `:help()` | topic: stringOrInteger | none |
<!-- END GENERATED OPERATIONS -->

Named options are a final table: `:move("forward", {ticks=20})`, `:destroy{mode="continuous",count=64}`,
`:drop("all")`, `:use{interval=40,hold=20,ticks=120}`.

How options map to the typed command:

- `ticks` maps to `for`; an action's `interval` replaces `mode`.
- Teleport options pair `yaw,pitch`, or use `facing` for a player, or all of `facingX,facingY,facingZ`. `dimension`
  becomes the leading `in` clause.
- Three-argument `look(x, y, z)` is `look at`, relative to the Bot as in commands.
- `sprint` and `sneak` take booleans.

Notes:

- `group:members()` returns indexed member handles in a fixed membership order.
- Root `list`, `ping` and `help` keep their root meaning.
- A script `menu()` returns acceptance without opening any UI.
- `look` (both forms) returns once BDS reports the new rotation, with the observed `{yaw, pitch}` as `data.look`, so a
  crosshair operation right after it uses the new view. If BDS does not report it within about 0.5 s (10 ticks at
  20 TPS), the result has `code="timeout"`.

## Event kinds

The event kinds a script may watch are listed below. Triggers, which start a run for an occurrence, use the same kinds
and filters.

### Watches

A watch collects occurrences of one event kind for the run, and `w:wait` returns the next one.

```lua
params {}
local w = watch("block.change", {area = area.cell(100, 64, 200), to = "air"})
local r = w:wait{timeout = 1200}   -- {ok, code, reason, data, missed}
if r.ok then log(r.data.from .. " became " .. r.data.to .. " (" .. r.data.cause .. ")") end
w:close()                          -- optional; a watch ends with its run
```

```lua
local w = watch("schedule", {every = "1m"})        -- each time the endbotd timer fires
local n = watch("world.time", {phase = "night"})   -- each time night falls
```

Filters by kind:

| Kind | Filters |
| --- | --- |
| `block.change` | `area` (required), `dimension` (default `overworld`), `from`, `to` |
| `schedule` | `every` (an integer and `s`, `m` or `h`, from `10s` to `24h`), `at` (`HH:MM` in endbotd's local time, once a day), or both |
| `world.time` | optional `phase`: `day`, `sunset`, `night` or `sunrise` |
| `player.join`, `player.leave`, `player.chat`, `bot.*`, `job.finished`, `server.started` | none |

- `watch(kind, filter)` checks the kind, every filter name and value, the required filters, and the kind's capability
  against `requires {}` before it returns. A mistake stops the run with a message naming the field.
- Block names may omit `minecraft:`.
- `block.change` takes an `area` built with `area.cell`, `area.box` or `area.sphere` (with at most 16 `:minus`), of
  at most 512 blocks. A watch has no Bot to resolve against, so `~` and the sphere around the Bot are refused: build
  the area from a position, such as a Bot's `status()` coordinates.

Waiting:

- Buffering starts at `watch()`, so nothing is lost between waits. A watch keeps the 64 newest occurrences; `missed`
  counts the ones dropped since the previous successful wait.
- `w:wait{timeout = ticks}` follows the other waits: the default timeout is 1200 ticks and a timeout is
  `ok = false, code = "timeout"`.
- A wait is answered as soon as an occurrence arrives, and it uses no operation budget.
- After the source restarted (BDS or the link) or dropped occurrences, the next wait returns once
  `ok = false, code = "unavailable"` with the reason, then waiting continues. Changes during such a gap are not
  replayed; re-read with `block()` if the script cannot tolerate one.

What `data` holds:

| Kind | `data` |
| --- | --- |
| `block.change` | `x`, `y`, `z`, `dimension`, `from`, `to`, `cause` (`player-break`, `player-place` or `poll`), and `bot` when a Bot did it. A human player's name is never given |
| `schedule` | `at`: the local time of the tick that fired |
| `world.time` | `phase` and `time` (the level time of day, 0-23999); it fires when the day cycle enters another phase |

Players and chat:

- `player.join`, `player.leave` and `player.chat` report human players only (Bots have the `bot.*` kinds). They need
  `requires { "observe.presence" }` or `requires { "observe.chat" }`; the run form and the trigger form show the
  declaration first.
- `player.chat` is public chat only: `/tell`, `/msg` and `/w` are never reported. The message is cut to 256
  characters.

Limits (a watch over a limit is refused with the limit named):

| Limit | Default |
| --- | --- |
| Watches per run | 16 |
| `[limits] max-watches-per-controller`, across one controller's runs | 32 |
| `[limits] max-watched-blocks-per-controller`, cells across one controller's watches and triggers | 1024 |
| `[events] max-watched-blocks`, server-wide | 2048 |

All kinds, their payloads and what delivery each guarantees:

<!-- BEGIN GENERATED EVENTS -->
| Kind | Required filters (at least one) | Payload | Guarantee |
| --- | --- | --- | --- |
| `block.change` | area: area (max 512 blocks) | x: integer, y: integer, z: integer, dimension: string, from: block, to: block, cause: string, bot: string? | At most once, after the fact, in source order. A change that lasts at least 4 ticks is delivered; player-break and player-place are delivered individually. |
| `player.join` | none | player: string | At most once, after the fact, in source order. Every occurrence is delivered while the source is connected. |
| `player.leave` | none | player: string | At most once, after the fact, in source order. Every occurrence is delivered while the source is connected. |
| `player.chat` | none | player: string, message: string | At most once, after the fact, in source order. Every occurrence is delivered while the source is connected. |
| `bot.online` | none | bot: string | At most once, after the fact, in source order. A change that lasts at least 40 ticks is delivered. |
| `bot.offline` | none | bot: string, reason: string? | At most once, after the fact, in source order. A change that lasts at least 40 ticks is delivered. |
| `bot.died` | none | bot: string, message: string | At most once, after the fact, in source order. Every occurrence is delivered while the source is connected. |
| `job.finished` | none | bot: string, job: string, reason: string | At most once, after the fact, in source order. Every occurrence is delivered while the source is connected. |
| `server.started` | none | none | At most once, after the fact, in source order. Every occurrence is delivered while the source is connected. |
| `schedule` | every: duration or at: time | at: string | At most once, after the fact, in source order. Every occurrence is delivered while the source is connected. |
| `world.time` | none | phase: string, time: integer | At most once, after the fact, in source order. Every occurrence is delivered while the source is connected. |
<!-- END GENERATED EVENTS -->

### Triggers

A trigger is a stored binding from an event kind and filter to a script and its arguments. Once armed, it starts a run
for every matching occurrence, without anyone typing a command.

```text
/botscript trigger add <script> <kind> [field=value ...] [name=<name>] [visibility=shared|private] [-- <arg> ...]
/botscript trigger list
/botscript trigger show <name>
/botscript trigger enable <name>
/botscript trigger disable <name>
/botscript trigger remove <name>
/botscript trigger set <name> <policy>=<value> ...
/botscript trigger visibility <name> shared|private
```

The script declares `param.event("event")`. A manual run gets `a.event = nil`, so one script works both ways. The
event parameter carries the payload, `kind` and `missed`.

```lua
params {
    param.event("event"),
    param.number("count", 1),
}
if a.event then
    log(a.event.kind .. " at " .. a.event.x .. " " .. a.event.y .. " " .. a.event.z)
end
```

```text
/botscript trigger add guard block.change area="100 64 200" to=air
```

`trigger add` options:

- `area=` takes the area in one quoted token, in the same forms as `within`: `"100 64 200"`, `"0 64 0 3 65 3"` or
  `"5 of 0 64 0"`, optionally followed inside the token by `except <values>`.
- The area is absolute: a trigger has no Bot or player to resolve `~` against, so `~` and the Bot-centred sphere
  (`"3"`) are refused. Write the coordinates.
- Other filters are `name=value`. For `block.change` they are `dimension` (default `overworld`), `from` and `to`.
  Block names may omit `minecraft:`.
- `--` separates the script arguments, which are stored as strings exactly as `/botscript run` takes them.
- The name defaults to the script name.
- A trigger is private unless `visibility=shared`, which only makes it visible in other people's `list` and `show`;
  it does not let them change it.

Addressing:

- You address your own triggers by `<name>`, and see shared ones as `<owner>/<name>` in `list` and `show`.
- Console only: the console addresses another owner's trigger as `<owner>/<name>`, where the owner is the display
  name (case-insensitive) or the owner key.

**Approval.** Adding or enabling a trigger shows the script's declaration and records it with the script revision and
its parameters. Only someone who may run the script may arm it.

| Script | What the trigger runs |
| --- | --- |
| Your own | The current revision, while its declaration stays within the approved one and its parameters still accept the stored arguments |
| Someone else's shared script | Only the approved revision; any new revision pauses it |
| The operator's script file | The current revision, as for your own |

A started run has the owner's authority intersected with the approved declaration, and counts toward the owner's
concurrent-run limit.

**Policies.** Change them with `trigger set <name> <policy>=<value>`.

| Policy | Default | Bounds | Effect |
| --- | --- | --- | --- |
| `concurrency` | `skip` | `skip`, `queue`, `parallel` | `skip` drops an occurrence while a run from this trigger is active or in cooldown and counts it as missed. `queue` keeps the newest `queue` occurrences and starts them in order. `parallel` allows up to 2 runs at once |
| `cooldown` | 20 ticks | 0-72000 | Minimum time between two starts; under `skip` an occurrence inside it is dropped, not delayed |
| `startsPerMinute` | 10 | 1-60 | Starts in a sliding 60-second window; over it the trigger is throttled: `skip` and `parallel` drop the occurrence as missed, `queue` holds it until the window allows a start |
| `queue` | 4 | 1-16 | Pending occurrences under `queue`; the oldest is dropped and counted as missed |

**Pausing and disabling.** A paused trigger stays paused until its owner enables it again, which re-approves.

| Reason | When |
| --- | --- |
| `declaration grew` | The script's declaration grows |
| `arguments no longer valid` | Its parameters no longer accept the stored arguments |
| `script unavailable` | Its owner can no longer run the script |
| `script changed` | Someone else's script it runs gets a new revision |
| `owner revoked` | The owner's access is revoked; the trigger is disabled. A regrant does not re-enable it by itself; after a regrant, `trigger enable` arms it again |

- Going over the start cap never stops a trigger. It is throttled, and its owner is told once in chat until it starts
  again.
- A pause, a disable by the server, and a start refused at the owner's run limit (once until a start succeeds) are
  also told to the owner in chat.
- A triggered run that ends cleanly is only logged, not shown in chat.
- A disabled trigger keeps its state and can be enabled again.

**Restarts.**

- After an endbotd restart, armed triggers come back with their state, stats and last start, so the cooldown holds.
  The minute window starts empty, and `lastGap` records the gap.
- A BDS or link restart leaves triggers armed, but no run starts while the link is down; those occurrences are
  counted as skipped.
- Kinds that come from the runtime resume after the runtime comes back.

**Limits.**

- `[limits] max-triggers-per-controller` (default 8) bounds armed triggers per owner; `0` turns triggers off on the
  server.
- A trigger's area counts toward `max-watched-blocks-per-controller` like a watch.
- Every other run limit applies to triggered runs unchanged.

## Run lifetime and cleanup

A run lasts until the script ends, it is stopped, or its invoker's authority ends.

- Every operation is checked against your authority like a typed command, and is logged like one.
- Runs survive logout and rejoin.
- Revoking your access ends your runs, and a regrant does not revive them.
- Operations requiring a live player, such as teleporting to that player by name, still require one. Use declared
  absolute positions for work that must continue while you are offline.
- Argument-free `spawn` after you log out uses your captured initial position, dimension and rotation.

When a run ends (normally, by failure or crash, by stop, by revocation or by shutdown):

- Continuing inputs the run started (movement, actions, flags, mining) stop.
- An input you or another controller gave a Bot later on the same channel is kept.
- One-shot uncharged actions are not continuing jobs. Completed inventory and world changes are not rolled back.
- So a script must keep waiting for as long as it needs continuing work.

If the runtime cannot be reached, cleanup can fail. The failure is reported; stop the Bot yourself with
`/bot <name> stop` or `/bot <name> destroy stop`. Inputs from other programs that drive the runtime directly are
not tracked.
