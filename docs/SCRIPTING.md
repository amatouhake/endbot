# Luau scripting (MVP)

Scripting runs in the **Endbot plugin**, in one native child process per run. The Node Bedrock runtime is
unchanged. Scripts are shared by controllers; each run uses its invoker's authenticated identity and grant
revision. Scripting is optional and **off by default**. No process starts merely because it is enabled.

## Install and enable

1. Build in a standalone clone: `cd runner`, then `node build.mjs`. Requires Node 24+, CMake, `tar`, and
   a C++17 toolchain. `CMAKE` may name the installed CMake executable. Keep the same generator on rebuilds;
   MinGW instructions are in [the spike](LUAU_RUNNER_SPIKE.md).
2. Install `.build/native/bin/endbot-luau-spike[.exe]` and its **trusted `api.luau` sidecar** together.
   Set `[scripting] runner` in the plugin's `config.toml` to that executable. Relative paths resolve from
   the plugin data folder. Empty `runner` uses `endstone_endbot/runner/endbot-luau-spike[.exe]` inside an
   operator-built plugin bundle. No binaries are tracked. Missing executable/sidecar reports
   `scripting runner not built/installed`.
3. Set `[scripting] enabled = true` and restart. Controller authorization still applies.

Official Luau **0.743** remains pinned by commit/SHA-256 in `runner/luau.lock.json`, without source changes.
The build verifies/restores pinned dependencies and copies the bootstrap beside the binary. Preserve upstream
license notices when distributing builds. Source inputs are pinned; compilers and byte-identical output are not.
Dependencies and build artifacts remain ignored.

Startup limits (positive integers):

| Setting | Default | Meaning |
| --- | ---: | --- |
| `controller-runs` | 2 | Concurrent runs per controller |
| `server-runs` | 8 | Concurrent runs server-wide |
| `wall-seconds` | 300 | Total deadline including declarations, waits and IPC |
| `slice-ms` | 100 | Cooperative VM deadline between yields |
| `interrupt-budget` | 1000000 | Cumulative safepoint callbacks, not exact instructions |
| `operations-per-second` | 20 | Sliding one-second host operation limit; exceeding fails the run |
| `max-script-bytes` | 32768 | UTF-8 source limit on save/run, maximum 65536 (protocol bound) |
| `max-scripts` | 64 | Stored-script creation limit |
| `memory-bytes` | 33554432 | VM allocator bound, minimum 1 MiB; excludes compiler/process allocations |

The protocol also bounds frames to 256 KiB and bounds nesting/string/object sizes. Highly escaped source may
hit the frame bound earlier. Form/save declaration inspection has a deadline of at most five seconds and
requires available concurrency capacity. Changes apply on restart. Disabling scripting or lowering storage
limits retains stored source; oversized stored scripts cannot run until edited or the limit is raised.
Total IPC events (including logs and zero waits) are bounded by `4 * operations-per-second * wall-seconds`.

## Commands, authoring and parameters

`/botscript` requires `endbot.command.control`. Native operation hints follow `/botgroup`'s approach and
retain the pinned BDS baseline's completion limitations.

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

`runs`/`stop` address the invoking controller's runs; the console has its own slot. Deleting shared source
does not cancel running copies. Show displays numbered, bounded pages and wraps long lines without dropping
source. Source is never loaded from a user-supplied filesystem path.

When enabled, `/bot menu` includes **Scripts...**. A script opens Run, Show source (paged), Stop runs,
Delete (confirmation), and Edit source. Create offers **Group row**, **Simple patrol**, and a blank script.
Editing uses one paste field with current source as default. Save compiles the complete source and inspects
its declaration without accepting the body. `save` is an internal form action; typing it cannot update source.
Stale edit/delete/run forms reject source revision changes. Canceling does not save; drafts are not persisted.

Scripts are stored atomically in versioned `scripts.json` in the plugin data folder. Names are case-insensitive
and follow Groups (1–16 ASCII letters/digits/underscores, starting with a letter/underscore). Disk write errors
leave the previous file/in-memory version intact. There is no automatic resume or replay after restart.

```lua
local a = params {
    group = param.group(),
    origin = param.pos("~ ~ ~"),
    spacing = param.number(2),
}
for i, member in group(a.group):members() do
    local result = member:tp(a.origin.x + (i - 1) * a.spacing, a.origin.y, a.origin.z)
    if not result.ok then error(result.reason or "placement rejected") end
end
```

Call `params` exactly once before host work, even without arguments (`params {}`). It yields the declaration
before the host accepts the body. Inspection executes up to this declaration; code before it has normal budgets
and cannot call Bot/log/wait functions. Constructors are `param.group([default])`, `param.bot([default])`,
`param.pos([default])`, `param.number([default])`, `param.bool([default])`, `param.string([default])`.

**Positional arguments follow alphabetical parameter names**, since Luau tables have no declaration order.
Only trailing arguments can be omitted; earlier defaulted parameters still occupy positions. The example accepts
`/botscript run row #miners` or `/botscript run row #miners "~ ~ ~" 3`. Quote positions and strings with spaces.
Positions return `{x,y,z}`; declared `~` coordinates resolve from the player's position captured when submitting
the command/form. Console positions must be absolute. Groups/Bots must exist; numbers must be finite;
booleans accept `true`/`false`. Run forms use that same order with Group/Bot dropdowns, position fields
defaulting to the player, number/text inputs and toggles with declared defaults.
Form selections also pin Group UUID/order and Bot UUID, rejecting deletion/recreation before submission.
Declared Bot arguments keep their UUID pin on subsequent requests; explicitly Group-pinned calls retain
the Group's own identity guard.

Calls return **acceptance**, not completion: `{ok=true,messages=...,data=...}` or
`{ok=false,reason=...,messages=...}`. Group mutations include counts in `data`; partial failure has `ok=false`.
Handle rejected calls explicitly. Revocation, rate/deadline and protocol errors end the run.
Use `wait(ticks)` (20 ticks = one wall-clock second) and `waitOnline(bot("Alice"))`, which also accepts a name
or Group handle. `log(text)` emits bounded log text. Waits recheck authority. No command-string API exists.

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
| `:resume()` | none | none |
| `:reconnect()` | none | none |
| `:despawn()` | none | none |
| `:forget()` | none | none |
| `:stop()` | none | none |
| `:menu()` | none | none |
| `:spawn()` | none | none |
| `:spawn()` | x: coordinate, y: coordinate, z: coordinate | yaw: number, pitch: number, dimension: string |
| `:rename()` | name: string | none |
| `:tp()` | target: string | none |
| `:tp()` | x: coordinate, y: coordinate, z: coordinate | yaw: number, pitch: number, dimension: string, facing: string, facingX: coordinate, facingY: coordinate, facingZ: coordinate |
| `:move()` | direction: forward / backward / left / right / stop | ticks: integer |
| `:look()` | yaw: number, pitch: number | none |
| `:look()` | x: coordinate, y: coordinate, z: coordinate | none |
| `:swap()` | a: integer, b: integer | none |
| `:hold()` | item: string | none |
| `:hotbar()` | none | none |
| `:hotbar()` | slot: integer | none |
| `:radius()` | none | none |
| `:radius()` | value: integerOrDefault | none |
| `:interact()` | x: coordinate, y: coordinate, z: coordinate, face: down / up / north / south / west / east | none |
| `:destroy()` | none | mode: once / continuous / stop, ticks: integer, count: integer |
| `:destroy()` | x: coordinate, y: coordinate, z: coordinate | face: down / up / north / south / west / east |
| `:drop()` | none | none |
| `:drop()` | selector: stringOrInteger | count: integer |
| `:jump()` | none | mode: once / continuous / stop, interval: integer, ticks: integer |
| `:attack()` | none | mode: once / continuous / stop, interval: integer, ticks: integer |
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

## Lifetime, boundaries and unverified gates

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

Runner VM/IPC/time budgets isolate failures to a run. io/os/require, file loaders, debug/environment access and
script-managed coroutines are removed. The executable/bootstrap are trusted installation assets. Process separation
is **not OS confinement**; native compiler/JSON allocations are outside VM memory accounting. OS resource limits,
restricted accounts and distribution hardening remain deployment work. The Node runtime and Endstone core are unchanged.
Keep `online-mode=true`, `allow-cheats=false`, no experiments/Beta APIs/GameTest and normal Microsoft/Xbox authentication.

The real pinned VM's Windows build/Node tests are automated evidence. **Caller must run Python suites and live
plugin/BDS checks**: multiline paste limits/cancel behavior, generated controls, Japanese rendering, stale forms,
logout/rejoin, revoke/regrant (including mid-operation), Group replacement, overlap cleanup, crashes, shutdown,
and tick timing. Linux build/bundling and OS confinement remain unverified. Tests never establish world achievement
history, actual Xbox achievement unlocks or another Endstone/BDS pair's support. See [the spike](LUAU_RUNNER_SPIKE.md).
