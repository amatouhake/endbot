# Luau scripting reference

Scripts are Luau programs that drive Bots. Each script belongs to the controller who created it (see
[ownership](#ownership-visibility-and-revisions)); each run uses its invoker's authority. This page is the reference
for writing and running them.

- New to scripts? Start with the [tutorial](SCRIPTING_TUTORIAL.md).
- Scripting is **off by default**. Turning it on, its limits and the runner's confinement are the operator's side:
  [SCRIPTING_SETUP.md](SCRIPTING_SETUP.md).

## Commands, authoring and parameters

`/botscript` requires `endbot.command.control`. Native operation hints follow `/botgroup`'s approach and
retain the pinned BDS baseline's completion limitations.

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

`runs`/`stop` address the invoking controller's runs. The console is the operator: its `runs` lists every controller's runs with their owner, and its `stop <id|name|all>` stops any of them, so a run left behind by a controller who logged out can be ended without revoking that controller. Start, stop, save and delete are logged with the controller's name, and so is each run's end. Deleting a script
does not cancel running copies. Show displays numbered, bounded pages and wraps long lines without dropping
source. Source is never loaded from a user-supplied filesystem path; the only files read are the operator's
[script files](#script-files).

### Ownership, visibility and revisions

Each script records its creator as owner (by XUID, else player UUID). Only the owner, or the console, may edit,
delete, restore or change its visibility; everyone else gets "only the owner may change" and is pointed to
**Copy and edit**. A script is `private` (only the owner sees and runs it; to others it does not exist) or
`shared` (every controller may list, show, run and copy it). New scripts and copies start `private`;
`visibility <name> shared|private` switches it (the 0.4.0 `share <name>` and `private <name>` spellings were removed in
0.6.0 and are refused with a pointer to this form). `copy <name> <new-name>` makes a private copy owned by the copier, and the owner's
`rename <name> <new-name>` keeps the owner, visibility and history (running copies keep the old name). Every save keeps
the previous source in a history of the last 10 revisions; `history` lists them (newest first, with time and
line count) and `restore <name> <n>` makes revision `n` current as a new save, keeping the replaced source in the
history. Runs still use the invoker's authority, whoever owns the script. Scripts saved before ownership (file
version 1) have no owner and stay shared; the first controller to edit one becomes its owner. Lists show
`name (owner, visibility)` with `yours` for your own. The console can reassign a stored script's owner with
`owner <name> <GamerTag>`: the owner key and name change, while the visibility, revision and history stay. A file
script has no owner to reassign; the file is the source. Not implemented: separate run/author permissions
(`endbot.script.run`/`endbot.script.author`); every controller may still author.

When enabled, `/bot menu` lists **Scripts...** after **+ New Bot here** (next to **Manage groups...**);
the target list above it contains only targets `/bot` can address. A script's screen shows its whole source with gray
line numbers (wrapped lines stay apart from the numbers; very long scripts are cut off with a pointer to `show`;
a `§` in the source is shown as `\u{a7}`, so source text cannot color, hide or scramble the preview) and
offers Run, Stop runs and Copy and edit (and Write into held book where export is enabled). Its owner gets **Edit
lines...** and **More...** instead of Copy and edit; More holds Rename, Share/Make private, Earlier revisions (restore with a
confirmation that shows that revision's source), Copy and edit, Write into held book and Delete (confirmation).
Create offers **Group row**, **Simple patrol**, **Mine at spots**, a blank script and **From held book...**; a template
or copy only asks for a name, and saving opens the new script.

**Edit lines** lists the source one line per button (40 per page). Tapping a line opens a form with that line already
filled in, an optional line to add below it (taking the edited line's indentation unless typed with its own) and a
**Delete this line** toggle; **+ Add a first line** and **+ Add a line at the end** add lines at the ends. A one-line
field is easy to change in place, unlike a whole prefilled source in the client's two-line field, which could not be
cleared. On the Windows client form fields **drop pasted line breaks** (checked 2026-10-25), so multi-line changes go
through a book (**Replace all from held book** at the bottom of the list; see below). Saving returns to the same page
of the list, and a form that changes nothing just goes back. Save compiles the complete source and inspects its
declaration without accepting the body. `save` is an internal form action; typing it cannot update source.
Stale edit/delete/run forms reject source revision changes. Canceling does not save; drafts are not persisted.

### Books

A book and quill is the comfortable in-game editor: it keeps line breaks and supports Ctrl+A/C/X/V on Windows.
`import <name>` (menu: **From held book...** for a new script, **Replace all from held book** for your own) reads the
book and quill or written book in the main hand. Pages are joined with a line break unless a page already ends with
one; trailing blank pages are ignored. A new name creates a private script; an existing name saves a new revision
through the same compile, declaration and owner checks as the menu. The book is only read. In the menu, **Replace
all from held book** first shows the book's source with both line counts and replaces the script only after
**Replace**, and only if the book still holds that text; **From held book...** refuses a name that already exists.
A typed `import` of an existing name saves at once (the previous source stays in Earlier revisions).

**Multi-line authoring loop (2026-10-25 Windows check):** create from a template (or blank), open the script,
**Write into held book**, edit the pages, then **Replace all from held book** to save a new revision. The book keeps the
whole source between edits, so one export is enough. The client's own book editor silently drops text past a page's
capacity, so split long source over pages by hand (up to 50 pages, 256 characters each); the plugin refuses a script
that cannot fit and names the offending line.

`export <name>` (menu: **Write into held book**) writes a script you can run into the book and quill in your main
hand, so you can edit it there and import it again (for someone else's shared script, import under a new name). An
empty book is written at once. A book that already has writing is overwritten only by `export <name> overwrite`;
plain `export` refuses it. The menu asks first: it shows the book's current text and page count, and its
**Overwrite** runs `export <name> overwrite`, writing only if the book still holds those pages. Written (signed)
books are never changed. Pages hold whole lines: up to 256 characters per page and 50 pages, so a longer line or script is refused.
No item is created. Export is on by default (it is the first step of the multi-line loop); an operator who wants
the plugin never to change a held item sets `[scripting] book-export = false`. The page and character limits are
conservative; real-client limits are recorded with the human-client checks.

### Optional UI pack field

Players who add the optional [Endbot UI resource pack](UI_PACK.md) as a global resource get a larger form window, compact
buttons and a multi-line field. With `[scripting] ui-pack-editor = true` (default false), the line editor also offers
**Edit all at once (UI pack)...**: the whole source in one field, saved like any other edit, and the new-script screen
offers the template in the same field so a script can be written as a whole before it exists. Without the pack the
client drops the line breaks, and that reply is refused rather than saved; the new-script screen's **Save as shown**
toggle saves the template unchanged instead. Without the pack a form input also stops at about 100 characters, so a
longer line has to be edited with the pack, in a script file, or through the book. The pack's window, buttons and
multi-line field were checked on a 1.26.60.5 client ([UI pack](UI_PACK.md)); books remain the verified multi-line
path for other clients.

### Script files

To write scripts in a PC editor, the operator puts `<name>.luau` files directly in `<instance>/scripts/`, next to
`endbot.toml` (created by setup and at start-up; before endbotd it was `server/plugins/endbot/scripts/`). They are read when the
server starts and again by `/botscript reload` (console, any controller, or **Reload script files** in the script
list); nothing watches the folder. Runs already going keep the source they started with.

- A file script is **shared** with every controller and **read-only** in game: Run, Stop runs, show, Copy and edit
  and export work; editing, rename, delete and sharing refuse with a pointer to the file. Lists show its owner as
  `file`. Copying makes an ordinary stored script owned by the copier.
- It has no revision history; version the folder yourself if you need one. It is never written to `scripts.json`.
- Only regular `.luau` files (UTF-8, CRLF accepted) with a valid script name and at most `max-script-bytes` load;
  links, other extensions and subfolders are ignored or refused. A stored script with the same name (anyone's, even
  private: names are server-wide) wins and the file is skipped as "Not loaded: a stored script named … already
  exists"; the console and the start-up log also name that script's owner, so the operator can sort it out.
- `reload` also runs each file's declaration check and replies with every skipped file and why; start-up only
  reads the files and logs skipped ones to the server console. A file is dropped only for its own fault (a
  declaration error, or hanging before `params`); when the check cannot run (the caller's run slots are busy, the
  runner is missing) the file stays loaded and the reply says it was not checked. At most `max-scripts` files load.
  The checks run on the command worker like saves, about 5 seconds at worst per file that hangs before `params`,
  so `/bot` commands wait during a reload of such a folder.

Scripts are stored atomically in versioned `state/scripts.json` (the plugin data folder before endbotd). Names are case-insensitive
and follow Groups (1–16 ASCII letters/digits/underscores, starting with a letter/underscore). Disk write errors
leave the previous file/in-memory version intact. There is no automatic resume or replay after restart.

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

Call `params` exactly once before host work, even without arguments (`params {}`). It yields the declaration
before the host accepts the body. Inspection executes up to this declaration; code before it has normal budgets
and cannot call Bot/log/wait functions. Constructors are `param.group(name[, default])`, `param.bot(name[, default])`,
`param.pos(name[, default])`, `param.number(name[, default])`, `param.bool(name[, default])`,
`param.string(name[, default])` and `param.choice(name, {"a", "b", ...}[, default])` (1–32 distinct strings of
1–64 characters; the run form shows a dropdown). Each declaration carries its name; `a.<name>` access is unchanged.
Pass a contiguous array of declarations. Named-key, mixed and sparse tables are rejected, as are duplicate
names, unknown types and invalid defaults. Names match `[A-Za-z_][A-Za-z0-9_]{0,31}`; at most 32 are allowed.
Declaration errors show the `params` call's script line where available and are localized for Japanese players.
The runner/host protocol carries `schema` as a JSON array, including `[]` for no parameters; each entry is
`{"name":"spacing","type":"number","default":2}` (a choice adds `"choices":[...]`). The plugin validates this array
again before forms or execution.

A script that needs a capability beyond the basic set declares it with `requires { "name", ... }`, called once and
before `params` (see [PERMISSIONS.md](PERMISSIONS.md#two-layers-the-person-and-the-script)). The basic set —
operating Bots, reading the world, waiting and logging (`act.bot`, `read.world`, `wait`, `log`) — needs no
declaration, and naming it changes nothing. The other capabilities come from event kinds
(`observe.chat`, `observe.presence`); any other name is an inspection error naming it. A second call, a call after
`params`, or anything but a list of distinct strings is a declaration error. The `params` request carries the list as
`requires` (`[]` when there is no call); inspection returns it with the schema as a set, so order does not matter. A
run started from an inspection (the run form, which carries the declaration in its guard, the parameterless menu
start, a trigger) stops before its first operation if its own `requires {}` differs from what that inspection
read, so a conditional call cannot widen what was shown. The run form shows the declaration on its first line, and a
run submitted from that form is held to it.

**Positional arguments and menu controls follow declaration order.** After the positional values, `name=value`
sets any declared parameter, so earlier defaults can be skipped. The example accepts `/botscript run row #miners`,
`/botscript run row #miners "~ ~ ~" 3` or `/botscript run row #miners spacing=3`. A positional value may not follow a
named one, a parameter may be given only once, and `word=...` whose `word` is not a declared parameter stays an
ordinary positional value. Quote positions and strings with spaces (`origin="0 70 0"`).
Positions arrive as Luau **vectors**: read `a.origin.x`, add and scale them (`a.origin + vector.create(2, 0, 0) * i`),
and pass one wherever an operation takes `x, y, z` (`bot:tp(a.origin)`). Declared `~` coordinates resolve from the
player's position captured when submitting
the command/form. Console positions must be absolute. Groups/Bots must exist; numbers must be finite;
booleans accept `true`/`false`; choices accept one of their options. Run forms use that same order with
Group/Bot/choice dropdowns, position fields
defaulting to the player, number/text inputs and toggles with declared defaults.
Form selections also pin Group UUID/order and Bot UUID, rejecting deletion/recreation before submission.
Declared Bot arguments keep their UUID pin on subsequent requests; explicitly Group-pinned calls retain
the Group's own identity guard.

Ordinary operation calls return **acceptance**, not completion: `{ok=true,code="ok",messages=...,data=...}` or
`{ok=false,code=...,reason=...,messages=...}`. Group mutations include counts in `data`; partial failure has `ok=false`.
`code` says what to do next and is the same in every language: `unauthorized` (permission), `not_found` (no such
Bot or target), `invalid_argument` (fix the request), `unavailable` (try again later) or `conflict` (the Bot's state
does not allow it now, for example offline or a name in use). A block the Bot cannot target has its own codes, listed
under [targeting blocks](#targeting-blocks). A Group's code is its first failed member's; each query
row also carries its member's `code`. Branch on `code`; `reason` is wording for people.
Handle rejected calls explicitly, for example `assert(r.ok, r.reason)`. Revocation, rate/deadline and protocol errors end the run.
Use `wait(ticks)` (20 ticks = one wall-clock second) and `waitOnline(bot("Alice"))`, which also accepts a name
or Group handle. `log(text)` writes one line, tagged with the run id, to the server log, and also to the chat of
the player who started the run: at most 10 lines per 5 seconds per run, each cut to 256 characters with formatting
codes removed; held-back lines are counted on the next chat line or the end message, and the server log keeps every
line (up to 2000 lines / 512 KiB per run). `[scripting] chat-logs = false` (plugin `config.toml`, or `endbot.toml`
for managed instances) keeps logs in the server log only. Console runs log only there. Waits recheck authority.
No command-string API exists.

Every operation a script issues is also a normal `/bot` command, so its reply would reach the invoker's chat as if
typed. That echo is noise for a script that issues dozens of operations, so `[scripting] replies` (plugin
`config.toml`, or `endbot.toml` for managed instances) chooses the policy: `all` echoes every operation like a typed
command, `failures` (default) echoes only rejected operations while accepted ones keep their console log line, and
`none` is silent. Under `failures`, a `destroy` refused as `not_a_target` is not echoed, because its target can vanish
after the script read it (see [Targeting blocks](#targeting-blocks)); the script still receives the refusal. The run's start/end lines and `log()` are unaffected, and the mining loop's start/stop notices
still reach the chat. Scripts cannot change the policy.

### Structured status and mining waits

`bot:status()` returns the usual result wrapper; read its `data` fields below. Mining values come from
runtime status, including its retained last mining job; they do not infer completion from elapsed time.
Position, look, health and inventory come from BDS through the Endstone SDK at the time of the call;
hunger and saturation come from the attributes BDS sends the Bot. A field is nil when the Bot is not in
the world (or before BDS has sent it).

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

Terminal reasons include `block count reached`, `duration elapsed`, `stopped`, and runtime failure reasons.
Null JSON fields become nil in Luau. `group:status()` returns `data.results` in pinned membership order;
each row has `index`, `identityId`, `name`, `ok`, `reason` (nil on success), and `data` containing that
member's fields above (nil on query failure). Partial query failure sets the outer `ok=false`.

Other queries also return data: `bot:inventory().data.slots` lists non-empty slots as `{slot, name, count}`
(slots `"1"`-`"36"`, `offhand`, `head`, `chest`, `legs`, `feet`); `bot:hotbar().data` has `selectedSlot` and the
hotbar `slots`; `bot:entities().data.entities` lists up to 10 nearby non-player entities, nearest first, as
`{runtimeId, type, distance, position, health, maxHealth, nameTag}` (health is nil for non-mobs). Pass a
`selector` (`"@9"`) to `:interact(e.selector)` or `:attack(e.selector)`; entities always take `@`.
`bot:entity("@cow")` (or `"@9"`, `"@nearest"`, or no argument) returns `data.entity`, one such entry within 16 blocks.
Repetition uses the final options table: `bot:interact("@cow", {count=5})`, `bot:attack("@zombie", {interval=20,
ticks=200})`, `bot:destroy("oak_log", {count=10})`. Inventory, hotbar and `held` items carry `durability = {remaining, max}` for damageable
items such as tools and armor.

World queries read BDS on the server thread and count as operations:

| Call | `data` |
| --- | --- |
| `bot:target()` | `kind` (`"block"`, `"entity"` or nil), `distance`, and `block = {x, y, z, face, type}` or `entity = {runtimeId, type}`: the nearer hit within 6 blocks of the Bot's eyes, ignoring players |
| `bot:block(x, y, z)` | `{x, y, z, type, states, reachable}`, plus `reachCode` and, for `blocked`, `obstruction` when not reachable; `"~"`/`"~-1"` strings are relative to the Bot; any loaded position |
| `bot:blocks("oak_log", {limit=10})` | `{type, radius, blocks}`; `blocks` lists `{x, y, z, distance, reachable}` nearest the Bot first, only blocks a player could see (no x-ray, see below), within a sphere of 8 around the Bot; limit 1-64 |
| `bot:blocks("oak_log", {area=area.sphere(5, x, y, z)})` | `{type, area, blocks}`: the same query narrowed to an [area](#areas), for example around a treetop; never past 16 blocks from the Bot |

A radius-12 search took about 23 ms of server-thread time in the live check (2026-10-24).

### Areas

One area value narrows the blocks `blocks`, typed `destroy`, `destroy` continuous loops and typed `interact` may pick.
Build it with `area`, then pass it as `{area = a}`:

| Luau | Chat | Shape |
| --- | --- | --- |
| `area.cell(x, y, z)` or `area.cell(v)` | `within <x> <y> <z>` | one block |
| `area.box(x1, y1, z1, x2, y2, z2)` or `area.box(v1, v2)` | `within <x1> <y1> <z1> <x2> <y2> <z2>` | a box, both corners included, as for `/fill` |
| `area.sphere(r)` | `within <r>` | a sphere of radius `r` around the Bot |
| `area.sphere(r, x, y, z)` or `area.sphere(r, v)` | `within <r> of <x> <y> <z>` | a sphere of radius `r` around a point |
| `a:minus(b)` | `except <b>` | `a` without the blocks of `b`; `b` takes any form above |

Coordinates are numbers, vectors, or `"~"`/`"~-1"` strings relative to the Bot. `:minus` chains freely
(`area.sphere(6):minus(footing):minus(trunk)`); chat takes one `except`. Area values are built only by these
functions, so a table or a string is refused before anything reaches the host.

The rules are the [command rules](COMMANDS.md#areas-within-and-except): an area only narrows, never widens reach or
sight; `~` resolves when the operation is accepted, so a continuous loop keeps its area while the Bot moves; `within`
holds at most a radius-16 sphere; without an area each operation keeps its default. The Bot's footing is not
special-cased, so exclude it explicitly:

```lua
local footing = area.cell("~", "~-1", "~")
local job = bot:destroy("acacia_log", {count = 20, area = area.sphere(6):minus(footing)}).job
job:wait{}
```

`blocks(type, x, y, z)` and `{radius = r}` were removed; calling them fails with a message naming
`blocks(type, {area = area.sphere(r, x, y, z)})`.

`interact` shares one `target` argument for a block type and an `@entity` selector, so `{area = a}` is part of
its signature. It applies only when the target is a block type (`interact("chest", {area = ...})`); an `@entity`
target is refused by the command layer with "An area applies to a block type …", because areas narrow blocks.

### Targeting blocks

Three questions about a block have one answer each. Block properties come from minecraft-data
for the locked Bedrock version (`scripts/generate_block_properties.py`).

| Question | Rule | Used by |
| --- | --- | --- |
| Could a player see it? | One of its faces touches a block light passes: air, liquids, plants, leaves, glass, ice and partial shapes such as slabs, stairs, fences, walls, panes and doors | which blocks `blocks` lists |
| Can the Bot target it now? | It is not air or liquid, part of it is within 6 blocks of the Bot's eyes, and a straight line reaches a side facing the Bot through blocks without a collision box only (air, liquids, grass, flowers, torches, rails, snow layers). Leaves, glass and partial shapes stop the line | `reachable`, `destroy x y z`, `destroy <type>`, `interact x y z`, `interact <type>` |
| What is under the crosshair? | The first block along the Bot's look within 6 blocks that is not air or liquid, as for a player: grass and torches are hit, light blocks are not | `target`, `destroy` and `interact` without a target |

So a log behind tall grass is reachable by name or coordinates, while the crosshair hits the grass in front of it;
a log wrapped in leaves is listed by `blocks` but not reachable until a leaf is broken.

When a block cannot be targeted, `block` reports `reachable = false` and `reachCode`, and `destroy`/`interact` fail
with the same value as `code`:

| Code | Meaning |
| --- | --- |
| `not_a_target` | Air or liquid, or nothing under the crosshair |
| `out_of_reach` | No part of it is within 6 blocks of the Bot's eyes |
| `blocked` | Something stops every line to it; `obstruction = {x, y, z, type}` (in `data` for a refused operation) names the first block in the way, when one is found |
| `unbreakable` | `destroy` only: bedrock, barriers and other blocks Survival cannot break; they can still be targeted |

`block` and `blocks` are reads, not reservations: `reachable` describes the moment of the read, and `destroy` and
`interact` check the target again when they run. A block can vanish in between — the Bot's own previous swing is
still resolving, a leaf decays, or another Bot or player breaks it — and the operation then fails with
`not_a_target`. A script that only wants the block gone can treat that code on `destroy` as done.

```lua
local info = bot:block(x, y, z).data
if not info.reachable and info.reachCode == "blocked" and info.obstruction then
  local o = info.obstruction
  bot:destroy(o.x, o.y, o.z)  -- clear the leaf first
end
```

### Jobs and waits

A continuing operation's result carries its **job**: whatever the Bot keeps doing after the operation returns.
`move`, `jump`, `attack`, `use` and `interact` (any mode that keeps running, including `use` once with `hold`),
`destroy` and `tp` return one; on a Group the job covers every member that started one. A `tp` job ends with
`arrived` once the Bot has acknowledged the server teleport. Wait for it with `job:wait`:

```lua
local r = bot("Alice"):move("forward", {ticks = 40})
local w = r.job:wait{timeout = 200}   -- {ok, code, reason, results}
if w.ok then log("stopped: " .. w.reason) end  -- duration elapsed, count reached, completed, stopped, replaced, ...
group("#miners"):destroy("stone", {count = 64}).job:wait{timeout = 2400}
```

Every wait takes `{timeout = ticks}` (default 1200) and returns the common result shape: `ok`, `code`, `reason` and,
for Bot and Group waits, one row per member with `name`, `identityId`, `ok`, `code` and `reason`. A job that ended
for any reason is `ok=true` with that reason (`stopped`, `replaced` by a newer command of the same kind, `died`,
`disconnected`, `held item changed`, ...); a timeout is `ok=false, code="timeout"` and does **not** stop the job.
An offline member is `ok=false, code="conflict", reason="offline"`. A job of a member that is no longer running and
whose end was overwritten by a later job of the same kind reports `ended`. A `destroy` job waits like `waitMining`.

`bot:waitOnline{timeout=...}` (also on Groups) waits until the target is online and returns `ok=false,
code="timeout"` at its timeout; without `timeout` it waits like `waitOnline(b)`. `waitOnline(b)` and
`b:waitMining{}` stay as aliases until 1.0, so existing scripts keep working.

`bot:waitMining{timeout=1200}` and `group:waitMining{timeout=1200}` wait only for destroy/mining jobs.
They return `{ok=boolean, code=string, reason=string, results={...}}`. Each result has `name`, `identityId`, `ok`,
`code`, `reason`, and `mining` (the fields above). The group waits for all current members, pinning membership
and identity on the first poll; membership replacement ends the run. A literal Bot's identity is also
pinned on the first poll. Movement and other continuing inputs do not delay this wait.

Timeout defaults to **1200 ticks (60 wall seconds)** and must be an integer from 0 to 2147483647;
0 performs one status check. Polling runs on the host every `max(0.25, 1 / operations-per-second)` wall
seconds, with the final sleep shortened to the remaining timeout (timeout uses the last polled snapshot).
Polls use the normal operation rate
limit and recheck authorization/stop; no command worker is held while sleeping. Stop/revocation/shutdown
or the optional positive run deadline ends the run, rather than returning a successful wait.

An inactive online member returns its terminal reason (or `idle` with no job), with `ok=true` unless its
state is `failed`. An offline member returns `ok=false, reason="offline"`; a still-active member at the
timeout returns `ok=false, reason="timeout"`. A timeout does not stop its mining job. Query failures
return `ok=false` with the query reason. The outer `ok` requires every member to succeed; `reason` is
the shared reason, `completed` for mixed successful reasons, or the first failing member's reason.
Per-member results preserve mixed outcomes.

**Mine at spots** accepts `name`, `start`, `spots` (default 2), `step` (default 4), and `blocks` (default 2),
in that order. It teleports along +X from `start` at `step` spacing, faces +Z, runs
`destroy{mode="continuous",count=blocks}`, waits up to 1200 ticks, and logs `spot N: <reason>` at each
position. Prepare reachable breakable blocks in front of each spot before running it.

## Generated API reference

Generated from the same `operations.json` read by the `/bot` parser, typed argv adapter and runner tests.
Update with `node runner/api-reference.mjs`; verify with `node runner/api-reference.mjs --check`.
The ordinary parser remains the semantic validator for ranges and option combinations. Methods are available
on `bot("Alice")` and `group("#miners")`; whole-group rename/forget retain their one-Bot restriction.

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
`:drop("all")`, `:use{interval=40,hold=20,ticks=120}`. `ticks` maps to `for`; action `interval` replaces `mode`.
Sprint/sneak take booleans. Teleport options pair `yaw,pitch`, or use `facing` for a player, or all of
`facingX,facingY,facingZ`; `dimension` becomes the leading `in` clause. Three-argument `look(x,y,z)` is `look at`,
relative to the Bot as in commands. `group:members()` pins UUID/membership order and returns indexed member
handles. Root list/ping/help retain their root semantics. A script `menu()` returns acceptance without opening UI.
`look` (both forms) returns once BDS reports the new rotation, with the observed `{yaw, pitch}` as `data.look`, so a
crosshair operation right after it uses the new view; if BDS does not report it within about 0.5 s (10 ticks at 20 TPS) the result has
`code="timeout"`.

## Event kinds

The event kinds a script may watch come from the same registry. Triggers, which start a run for an occurrence,
use the same kinds and filters.

### Watches

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

- `schedule` takes `every` (an integer and `s`, `m` or `h`, from `10s` to `24h`), `at` (`HH:MM` in endbotd's
  local time, once a day) or both. `data.at` is the local time of the tick that fired.
- `world.time` takes an optional `phase` (`day`, `sunset`, `night` or `sunrise`). `data.phase` and `data.time`
  (the level time of day, 0-23999) change when the day cycle enters another phase.

- `watch(kind, filter)` checks the kind, every filter name and value, the required filters and the kind's capability
  against `requires {}` before it returns; a mistake stops the run with a message naming the field. Block names may
  omit `minecraft:`.
- `block.change` takes an `area` built with `area.cell`, `area.box` or `area.sphere` (with at most 16 `:minus`), of at
  most 512 blocks. A watch has no Bot to resolve against, so `~` and the sphere around the Bot are refused: build the area from
  a position, such as a Bot's `status()` coordinates. Optional filters: `dimension` (default `overworld`), `from` and
  `to`.
- Buffering starts at `watch()`, so nothing is lost between waits. A watch keeps the 64 newest occurrences; `missed`
  counts the ones dropped since the previous successful wait.
- `w:wait{timeout = ticks}` follows the other waits: the default timeout is 1200 ticks and a timeout is
  `ok = false, code = "timeout"`. `data` is the occurrence: for `block.change` `x`, `y`, `z`, `dimension`, `from`,
  `to`, `cause` (`player-break`, `player-place` or `poll`) and `bot` when a Bot did it. A human player's name is never
  given.
- After the source restarted (BDS or the link) or dropped occurrences, the next wait returns once
  `ok = false, code = "unavailable"` with the reason, then waiting continues. Changes during such a gap are not
  replayed; re-read with `block()` if the script cannot tolerate one.
- A wait is answered as soon as an occurrence arrives; it uses no operation budget.
- `player.join`, `player.leave` and `player.chat` report human players only (Bots have the `bot.*` kinds) and need
  `requires { "observe.presence" }` or `requires { "observe.chat" }`; the run form and the trigger form show the
  declaration first. `player.chat` is public chat only: `/tell`, `/msg` and `/w` are never reported. The message is
  cut to 256 characters.
- Limits: 16 watches per run, `[limits] max-watches-per-controller` (default 32) across one controller's runs,
  `[limits] max-watched-blocks-per-controller` (default 1024) cells across one controller's watches and triggers, and
  the server-wide `[events] max-watched-blocks` (default 2048). A watch over a limit is refused with the limit named.

<!-- BEGIN GENERATED EVENTS -->
| Kind | Filters (one of) | Payload | Guarantee |
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

A trigger is a stored binding from an event kind and filter to a script and its arguments. It is armed once and
then starts a run for every matching occurrence, without anyone typing a command. The script declares
`param.event("event")`; a manual run gets `a.event = nil`, so one script works both ways. The event parameter
carries the payload, `kind` and `missed`.

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
/botscript trigger add <script> <kind> [field=value ...] [name=<name>] [visibility=shared|private] [-- <arg> ...]
/botscript trigger add guard block.change area="100 64 200" to=air
/botscript trigger list
/botscript trigger show <name>
/botscript trigger enable <name>
/botscript trigger disable <name>
/botscript trigger remove <name>
/botscript trigger set <name> <policy>=<value> ...
/botscript trigger visibility <name> shared|private
```

- `area=` takes the area values in one quoted token, in the same forms as `within`: `"100 64 200"`,
  `"0 64 0 3 65 3"` or `"5 of 0 64 0"`, optionally followed inside the token by `except <values>`. Other filters
  are `name=value`; for `block.change` they are `dimension` (default `overworld`), `from` and `to`. Block names may
  omit `minecraft:`. The area is absolute: a trigger has no Bot or player to resolve `~` against, so `~` and the
  Bot-centred sphere (`"3"`) are refused; write the coordinates.
- `--` separates the script arguments, which are stored as strings exactly as `/botscript run` takes them. The
  name defaults to the script name. A trigger is private unless `visibility=shared`, which only makes it visible
  in other people's `list` and `show`; it does not let them change it.
- The console addresses another owner's trigger as `<owner>/<name>`, where the owner is the display name
  (case-insensitive) or the owner key. Players address their own by `<name>` and see shared ones as
  `<owner>/<name>` in `list` and `show`.

**Approval.** Adding or enabling shows the script's declaration and records it with the script revision and its
parameters; only someone who may run the script may arm it. On your own script the trigger then runs the
current revision while its declaration stays within the approved one and its parameters still accept the stored
arguments. On someone else's shared script it runs only the approved revision; any new revision pauses it. The
operator's script files follow their current revision like your own. A
started run has the owner's authority intersected with the approved declaration and counts toward the owner's
concurrent-run limit.

| Policy | Default | Bounds | Effect |
| --- | --- | --- | --- |
| `concurrency` | `skip` | `skip`, `queue`, `parallel` | `skip` drops an occurrence while a run from this trigger is active or in cooldown and counts it as missed. `queue` keeps the newest `queue` occurrences and starts them in order. `parallel` allows up to 2 runs at once |
| `cooldown` | 20 ticks | 0-72000 | Minimum time between two starts; under `skip` an occurrence inside it is dropped, not delayed |
| `startsPerMinute` | 10 | 1-60 | Starts in a sliding 60-second window; over it the trigger is throttled: `skip` and `parallel` drop the occurrence as missed, `queue` holds it until the window allows a start |
| `queue` | 4 | 1-16 | Pending occurrences under `queue`; the oldest is dropped and counted as missed |

**Pausing and disabling.** A trigger pauses when its script's declaration grows (`declaration grew`), its
parameters no longer accept the stored arguments (`arguments no longer valid`), or the script can no longer be
run by its owner (`script unavailable`), or someone else's script it runs gets a new revision (`script changed`);
it stays paused until its owner enables it again, which re-approves. Going over the start cap never stops a
trigger: it is throttled and its owner is told once in chat until it starts again. A pause, a disable by the
server and a start refused at the owner's run limit (told once until a start succeeds) are also told to the owner in
chat. A triggered run that ends cleanly is only logged, not shown in chat. Revoking the owner disables it
(`owner revoked`), and a regrant does not re-enable it. A disabled trigger keeps its state and can be enabled
again.

**Restarts.** endbotd loads armed triggers with their state, stats and last start, so the cooldown holds; the
minute window starts empty and `lastGap` records the gap. A BDS or link restart leaves them armed, but no run
starts while the link is down; those occurrences are counted as skipped. Runtime-sourced kinds resume after the
runtime comes back.

**Limits.** `[limits] max-triggers-per-controller` (default 8) bounds armed triggers per owner; `0` turns
triggers off on the server. A trigger's area counts toward `max-watched-blocks-per-controller` like a watch. Every
other run limit applies to triggered runs unchanged.

## Run lifetime and cleanup

All operations enter the existing ordered command service with the invoker's authority and normal command
audit. Every runtime request is authorized again; Group calls retain UUID pins and membership guards. Runs
survive logout/rejoin. Revocation changes a separate grant revision, so revoke/regrant cannot revive an old run.
Operations requiring a live player, such as teleporting to that player by name, still require one; use declared
absolute positions for work that must continue offline. Argument-free spawn after logout uses the controller's
captured initial position/dimension/rotation.

Normal end, failure/crash, stop, revocation and shutdown stop continuing inputs still owned by the run.
Ownership tracks Bot UUID and movement/action/flag/mining channels for scripts, commands and Group batches.
Later controller inputs on a channel are preserved. Cleanup pins UUIDs and mining loop IDs. One-shot uncharged
actions are not continuing jobs. Scripts must wait while continuing work is needed. Completed inventory/world
changes are not rolled back. Unreachable runtime transport can prevent cleanup; failure is reported and operators
can use `/bot <name> stop` or `destroy stop`. Independent external runtime clients are outside this plugin's input
ownership tracking. Cleanup stops attempting further channels after an unavailable-transport error, rather than
queueing one timeout per member. Shutdown cleanup uses the existing bounded runtime control timeout.
If an input's acknowledgement is lost, the plugin conservatively retains its pinned cleanup ownership because
acceptance is unknown. A later input from another caller still replaces that ownership.
