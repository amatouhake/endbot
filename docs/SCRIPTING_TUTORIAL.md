# Writing Bot scripts: a tutorial

This walks you from nothing to scripts that move, mine and line up Bots. It assumes you can already use `/bot`
(see [commands](COMMANDS.md)) and teaches just enough [Luau](https://luau.org/) along the way. Every method and
limit is listed in the [scripting reference](SCRIPTING.md).

A script is a small Luau program that sends the same operations you would type as `/bot` commands, with loops,
arithmetic and conditions around them. It runs with your permissions: it can control the Bots you can control,
and it stops if you lose that permission.

## Before you start

- The operator has turned scripting on (`[scripting] enabled = true`, see [install and enable](SCRIPTING.md#install-and-enable)).
- You are a controller (you can use `/bot`).
- A Bot exists. The examples use one called Alice: `/bot Alice spawn`.

## 1. Where to write a script

**In a PC editor (recommended when you can reach the server's files).** Save `<name>.luau` in
`<instance>/scripts/`, next to `endbot.toml` (UTF-8, any line endings), then run `/botscript reload` in game or at the console.
The name is the file name: 1 to 16 letters, digits and underscores, starting with a letter or underscore.
Change the file and `reload` again whenever you edit it. These scripts are shared with every controller and
read-only in game; see [script files](SCRIPTING.md#script-files).

**In game.** `/bot menu` → **Scripts...** → **+ New script...**. Pick a template (or **Blank script**), name it
and save. **Edit lines...** changes one line at a time. For longer text, write it in a book and quill and use
**From held book...** (new script) or **Replace all from held book** (see [books](SCRIPTING.md#books)).

Either way, `/botscript menu` lists your scripts, and pressing one shows its source.

## 2. Your first script

Save this as `hello.luau` and `/botscript reload`:

```lua
params {}
local alice = bot("Alice")
waitOnline(alice)
alice:jump()
```

Run it with `/botscript run hello`, or press **hello** → **Run** in the menu. Alice jumps once, and your chat shows
`Script hello started: <run id>` and then `Script hello (<run id>) ended: ...`.

Line by line:

- `params {}` declares the script's parameters, here none. **Every script starts with `params`**, exactly once,
  before anything else.
- `bot("Alice")` returns a handle for the Bot named Alice. It does not check anything yet.
- `waitOnline(alice)` waits until Alice is connected, so the next line is not rejected while she is still joining.
- `alice:jump()` is `/bot Alice jump`. The `:` calls an operation on the handle.

`log("some text")` prints a line in your chat, after the script's name and run id, and in the server console. Add
`log("jumped")` after the jump to try it. Chat shows at most 10 lines every 5 seconds per run and says how many it
held back; the console keeps them all.

## 3. Parameters: one script, many uses

Hard-coding `"Alice"` means editing the script for every Bot. Declare parameters instead, and save this as
`hop.luau`:

```lua
local a = params { param.bot "name", param.number("times", 3) }
local b = bot(a.name)
waitOnline(b)
for i = 1, a.times do
    b:jump()
    wait(20)
end
```

`params` now returns a table `a` with one field per parameter. **Run** in the menu shows a form: a dropdown of
Bots for `name` and a number field for `times` (prefilled with 3). From chat, values follow the declaration
order, and `name=value` sets any parameter by name:

```text
/botscript run hop Alice
/botscript run hop Alice 5
/botscript run hop Alice times=5
```

`wait(20)` pauses for 20 ticks, one second. `for i = 1, a.times do ... end` repeats the block.

| Declaration | Value in the script | Menu control |
| --- | --- | --- |
| `param.bot "name"` | a Bot name for `bot()` | Bot dropdown |
| `param.group "group"` | a `#group` name for `group()` | Group dropdown |
| `param.pos("origin", "~ ~ ~")` | a vector (see section 5) | text field, prefilled with where you stand |
| `param.number("count", 2)` | a number | text field |
| `param.bool("loop", false)` | `true` or `false` | toggle |
| `param.string("label", "")` | text | text field |
| `param.choice("mode", {"walk", "run"}, "walk")` | one of the strings | dropdown |

The second argument is the default; without one, the parameter must be given. `param.bot "name"` is Luau shorthand
for `param.bot("name")`.

## 4. Did it work? Checking results

Every operation returns a result table straight away. It says whether the operation was **accepted**, not
whether it has finished:

```lua
local r = b:move("forward", {ticks = 40})
if not r.ok then
    error("move was rejected: " .. r.reason)
end
```

`r.ok` is `true` or `false`, and `r.reason` explains a rejection, for example when the Bot is offline. The short
form used in the templates is:

```lua
assert(b:move("forward", {ticks = 40}).ok, "move rejected")
-- or, keeping the reason:
local r = b:move("forward", {ticks = 40})
assert(r.ok, r.reason)
```

`error(...)` and a failed `assert` end the run, and the end message in your chat says why.

Because results come back immediately, **wait for the action yourself** before starting the next one. Walking
for 40 ticks takes 40 ticks, so a patrol waits that long:

```lua
local a = params { param.bot "name", param.number("ticks", 40) }
local walker = bot(a.name)
waitOnline(walker)
for side = 1, 4 do
    local r = walker:move("forward", {ticks = a.ticks})
    assert(r.ok, r.reason)
    wait(a.ticks)
    walker:look(side * 90, 0)   -- yaw, pitch in degrees
end
```

Mining is the exception: `waitMining` (section 7) waits for the job itself.

## 5. Positions are vectors

A `param.pos` value is a vector with `.x`, `.y` and `.z`. Vectors add and scale, and you can pass one wherever an
operation takes `x, y, z`:

```lua
local a = params { param.bot "name", param.pos("start", "~ ~ ~") }
local b = bot(a.name)
waitOnline(b)
for i = 0, 4 do
    local spot = a.start + vector.create(i * 2, 0, 0)   -- 2 blocks apart along +X
    local r = b:tp(spot)
    assert(r.ok, r.reason)
    wait(20)
end
```

The default `"~ ~ ~"` means "where you stand when you run it"; from the console, give absolute coordinates in
quotes: `/botscript run steps Alice "100 64 -20"`. `b:tp(x, y, z, {yaw = 90, pitch = 0})` also faces a direction,
and `b:tp("Steve")` teleports to a player.

## 6. Working with Groups

A [Group](GROUPS.md) is a named list of Bots, such as `#miners`. `group("#miners")` takes the same operations as a
Bot and applies them to every member; `:members()` gives one handle per member, in order:

```lua
local a = params { param.group "group", param.pos("origin", "~ ~ ~"), param.number("spacing", 2) }
for i, member in group(a.group):members() do
    local r = member:tp(a.origin + vector.create((i - 1) * a.spacing, 0, 0))
    assert(r.ok, r.reason)
end
```

This is the **Group row** template: it lines the Group up along +X from where you stand. A Group-wide call
reports `ok = false` if any member rejects it.

## 7. Reading the world and waiting for work

Queries return their answer in `data`:

```lua
local s = b:status()
assert(s.ok, s.reason)
local p = s.data.position
log(string.format("%s is at %.1f %.1f %.1f holding %s", s.data.name, p.x, p.y, p.z,
    s.data.held and s.data.held.name or "nothing"))
```

`status()` also has health, hunger, the selected slot and mining state; `inventory()`, `entities()`, `target()`,
`block(x, y, z)` and `blocks(type)` read the rest ([structured status](SCRIPTING.md#structured-status-and-mining-waits)).

Mining has a real completion wait. Stand Alice next to some oak logs and run:

```lua
local a = params { param.bot "name", param.number("logs", 5) }
local b = bot(a.name)
waitOnline(b)
local r = b:destroy("oak_log", {count = a.logs})
assert(r.ok, r.reason)
local done = b:waitMining{timeout = 1200}   -- at most 60 seconds
log("mining ended: " .. done.reason)
assert(done.ok, done.reason)
```

`waitMining` returns when the job ends (`block count reached`, `stopped`, ...) or at the timeout, which does not
stop the job. `group:waitMining{...}` waits for every member.

## 8. Stopping and fixing

- `/botscript runs` lists your running scripts; `/botscript stop <run id|name|all>` stops them. Stopping does
  not undo what Bots already did.
- A mistake in the code (a missing `end`, a typo) is reported with its line, such as `script:4: ...`, when you
  save or run the script, and with `reload` for script files. Fix that line and try again.
- A script that asks for more than about 20 operations per second is slowed down, not stopped: each extra operation
  waits for its turn, and the log says so once. A script is stopped when it floods the host with far more requests
  (logs and zero waits included), runs out of CPU time, or you lose permission to control Bots. The end message says
  which.
- Runs survive you logging out; they do not survive a server restart.

## 9. Reacting to events

So far a script runs when you start it. Events let Bots react to the world: a block changing, a player joining,
night falling, a timer. There are two ways to use them, and they suit different jobs.

- A **trigger** starts a script for you every time an event happens, until you disable it. It is stored by the
  server and survives restarts.
- A **watch** waits for an event inside a script that is already running, so the script can prepare first and
  decide what to do when the event arrives.

The examples use a Bot called Bob and the block at `0 64 0`.

### Your first trigger

A script that a trigger starts declares `param.event`. Save this as `bob_toggle.luau`:

```lua
local a = params { param.event "event", param.bot("name", "Bob") }
local b = bot(a.name)
local s = b:status()
if s.ok and s.data.connectionState == "online" then
    b:despawn()
else
    b:resume()
end
```

Then bind it to the block:

```text
/botscript trigger add bob_toggle block.change area="0 64 0"
```

Break or place a block at `0 64 0` and Bob goes offline or comes back. `trigger add` shows the script's declaration
first and records your approval; the trigger is named after the script unless you add `name=...`. The same is
available without commands from `/botscript menu` → **Triggers...** and on the web.

- `a.event` holds the occurrence: for `block.change` it has `x`, `y`, `z`, `from`, `to`, `cause` (`player-break`,
  `player-place` or `poll`), `bot` when a Bot did it, and `missed` (how many occurrences were dropped since the last
  start). Run the script by hand with `/botscript run bob_toggle` and `a.event` is `nil`, so one script works both
  ways.
- Filters narrow the trigger. `to=air` fires only when the block is removed, `from=air` only when one is placed:

  ```text
  /botscript trigger add bob_off block.change area="0 64 0" to=air
  /botscript trigger add bob_on  block.change area="0 64 0" from=air
  ```

  Two small scripts, one calling `b:despawn()` and one `b:resume()`, never guess from Bob's state, which can lag
  while he is still connecting.
- `area=` takes the same values as `within`: one block `"0 64 0"`, a box `"0 64 0 3 65 3"` or a sphere
  `"5 of 0 64 0"`, at most 512 blocks.
- Other kinds work the same way: `schedule every=10m`, `world.time phase=night` and the rest in
  [event kinds](SCRIPTING.md#event-kinds). Kinds about players (`player.join`, `player.chat`) need the script to
  declare `requires { "observe.presence" }` or `requires { "observe.chat" }`.
- `/botscript trigger list` lists your triggers and their state; `trigger show <name>` adds the counts (starts,
  missed, skipped). `disable`, `enable` and `remove` do what they say.

### Waiting inside a script

A watch is for "do this first, then react". This script brings Bob online, then waits up to ten minutes for the
block to be broken and sends him away only if he is still online at that moment:

```lua
local a = params { param.bot("name", "Bob") }
local b = bot(a.name)
b:resume()
waitOnline(b)

local w = watch("block.change", {area = area.cell(0, 64, 0), to = "air"})
for _ = 1, 10 do
    local r = w:wait{timeout = 1200}           -- one minute
    if r.ok then
        local s = b:status()
        if s.ok and s.data.connectionState == "online" then
            b:despawn()
        end
        break
    elseif r.code == "unavailable" then
        log("events were missed while waiting: " .. r.reason)
    end                                        -- a timeout just waits again
end
```

- Nothing is lost between `watch()` and `wait`: the watch keeps the 64 newest occurrences.
- `wait` returns as soon as an occurrence arrives and uses no CPU or operations while it waits.
- `unavailable` means BDS or its link restarted and changes in between were not seen; read the world again with
  `b:block(x, y, z)` if that matters.
- A watch ends with its run, and runs do not survive an Endbot restart (section 8).

### Which one to use

| You want to... | Use |
| --- | --- |
| React the same way every time, for days, across restarts | A trigger |
| React every time, but only when a condition holds | A trigger whose script checks the condition first and returns early |
| Prepare, wait for one event (or a few), then finish | A watch inside the script |
| Wait for an event in the middle of a longer job | A watch inside the script |

Prefer a trigger when either would do. A running script uses one of your concurrent runs the whole time it waits,
and you have two (`[scripting] controller-runs`). A trigger uses none while it waits: its run exists only while it
reacts. Two long-waiting scripts fill both runs, and then every start of your triggers is skipped (counted in
`skipped` in `trigger show`).

### Things that surprise people

- **The cooldown drops, it does not delay.** By default a trigger starts at most once a second (`cooldown` 20 ticks)
  and skips events while its previous run is still going. Those events are counted as `missed`, not started later.
  To start them later instead, use a queue: `/botscript trigger set bob_toggle concurrency=queue`.
- **Busy events are throttled.** A trigger starts at most 10 times a minute (`startsPerMinute`). Beyond that, events
  are skipped (or queued) and you are told once in chat; the trigger keeps working.
- **A paused trigger waits for you.** A trigger pauses when its script starts asking for more (`declaration grew`),
  its parameters change (`arguments no longer valid`), or someone else changes a script you do not own
  (`script changed`), including a script file the operator edits. Check it and `/botscript trigger enable <name>`
  to approve the new version. Your own scripts can be edited freely as long as they ask for no more.
- **It runs as you.** A triggered run has your permissions, whoever caused the event. If you lose your controller
  grant, your triggers are disabled and stay disabled after a regrant until you enable them.
- **Limits.** You can have 8 armed triggers, watch 1024 blocks across your watches and triggers, and a run holds at
  most 16 watches; the operator can change these in `[limits]`.

The full rules are in [watches](SCRIPTING.md#watches) and [triggers](SCRIPTING.md#triggers).

## 10. Cheat sheet

```lua
local a = params { param.bot "name" }     -- always first; declares parameters
local b = bot(a.name)                     -- a Bot handle; group("#name") for a Group
waitOnline(b)                             -- wait until connected
local r = b:jump()                        -- any /bot operation; returns {ok, reason, data}
assert(r.ok, r.reason)                    -- stop the run if it was rejected
wait(20)                                  -- 20 ticks = 1 second
b:move("forward", {ticks = 20})           -- options go in a final table
b:tp(vector.create(0, 64, 0))             -- a vector stands for x, y, z
b:waitMining{timeout = 1200}              -- wait for a destroy/mining job
log("text")                               -- one line in your chat and the server console
local w = watch("block.change", {area = area.cell(0, 64, 0)})   -- start buffering events
local r = w:wait{timeout = 1200}          -- r.ok and r.data, or code "timeout"/"unavailable"
```

```text
/botscript trigger add <script> <kind> [filter=value ...]   -- the script declares param.event
/botscript trigger list | show | enable | disable | remove <name>
/botscript trigger set <name> concurrency=queue cooldown=0
```

Next: the [generated API reference](SCRIPTING.md#generated-api-reference) lists every operation and its options,
and [commands](COMMANDS.md) explains what each one does.
