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
- A script is also stopped when it does more than about 20 operations per second, runs out of CPU time, or you
  lose permission to control Bots. The end message says which.
- Runs survive you logging out; they do not survive a server restart.

## 9. Cheat sheet

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
```

Next: the [generated API reference](SCRIPTING.md#generated-api-reference) lists every operation and its options,
and [commands](COMMANDS.md) explains what each one does.
