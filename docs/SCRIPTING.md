# Luau scripting (MVP)

Scripting runs in the **Endbot plugin**, in one native child process per run. The Node Bedrock runtime is
unchanged. Scripts are shared by controllers; each run uses its invoker's authenticated identity and grant
revision. Scripting is optional and **off by default**. No process starts merely because it is enabled.

## Install and enable

Windows and Linux operator bundles include `app/<version>/runner/endbot-luau-runner[.exe]` and its
**trusted `api.luau` sidecar**. No developer tools or downloads are needed to enable scripting.
In the instance's `endbot.toml`, add or change:

```toml
[scripting]
enabled = true
```

Restart with `endbot start`. The CLI writes the boolean and the running app version's absolute runner
path to the generated plugin config on each start, so update/rollback selects that version's runner.
Fresh setup writes `enabled = false`; omission also means false. Controller authorization still applies.
Managed instances edit `endbot.toml`; their plugin config is overwritten on start. An optional
`[scripting] runner = 'path'` overrides the bundled path. Relative runner paths resolve from the plugin
data folder. With no bundled runner and no override, the plugin's existing default lookup is retained.

For a manual plugin install, build from a standalone clone with `node runner/build.mjs --out build/runner-stage`
(Node 24+, CMake, Ninja, `tar`, C++17 toolchain; MSVC developer shell on Windows, GCC on Ubuntu 22.04).
Install `build/runner-stage/endbot-luau-runner[.exe]` and `api.luau` together, then set `[scripting] runner`
and `enabled = true` directly in the plugin's `config.toml` and restart. Empty `runner` looks for
`endstone_endbot/runner/endbot-luau-runner[.exe]` beside the plugin package. Missing executable/sidecar reports
`scripting runner not built/installed`. See [runner build options](../runner/README.md), including local MinGW.

Official Luau **0.743** remains pinned by commit/SHA-256 in `runner/luau.lock.json`, without source changes.
The build verifies/restores pinned dependencies and copies the bootstrap beside the binary in
`runner/.build/native/bin/`. `--out` (or `ENDBOT_RUNNER_OUT`) also stages the binary, bootstrap and MIT texts.
Only the two pinned source URLs are fetched; checksum mismatches fail the build. Bundles carry
`licenses/LUAU_LICENSE.txt` (from the verified archive) and `licenses/NLOHMANN_JSON_LICENSE.txt`, listed in
`THIRD_PARTY_NOTICES.md`. Preserve these when distributing builds. Source inputs are pinned; compilers and byte-identical output are not.
Dependencies and build artifacts remain ignored.

Startup limits (positive integers, except `wall-seconds` also accepts zero):

| Setting | Default | Meaning |
| --- | ---: | --- |
| `controller-runs` | 2 | Concurrent runs per controller |
| `server-runs` | 8 | Concurrent runs server-wide |
| `wall-seconds` | 0 | No total run deadline; positive values limit declarations, waits and IPC in wall seconds |
| `slice-ms` | 100 | Cooperative VM deadline between yields |
| `interrupt-budget` | 1000000 | Safepoint callbacks per VM slice between yields, not exact instructions |
| `operations-per-second` | 20 | Sliding one-second host operation limit; exceeding fails the run |
| `max-script-bytes` | 32768 | UTF-8 source limit on save/run, maximum 65536 (protocol bound) |
| `max-scripts` | 64 | Stored-script creation limit |
| `memory-bytes` | 33554432 | VM allocator bound, minimum 1 MiB; excludes compiler/process allocations |

The protocol also bounds frames to 256 KiB and bounds nesting/string/object sizes. Highly escaped source may
hit the frame bound earlier. Form/save declaration inspection has a deadline of at most five seconds and
requires available concurrency capacity. Changes apply on restart. Disabling scripting or lowering storage
limits retains stored source; oversized stored scripts cannot run until edited or the limit is raised.
IPC events (including logs and zero waits) are bounded by `4 * operations-per-second` in a sliding
one-second window, independently of run lifetime. Negative `wall-seconds` values are rejected.
With zero, runs still end on completion, `/botscript stop`, authority revocation, or plugin/server shutdown.
Per-slice interrupt/time, memory, operation rate and concurrency limits always apply.

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
Delete (confirmation), and Edit source. Create offers **Group row**, **Simple patrol**, **Mine at spots**, and a blank script.
Editing uses one paste field with current source as default. Save compiles the complete source and inspects
its declaration without accepting the body. `save` is an internal form action; typing it cannot update source.
Stale edit/delete/run forms reject source revision changes. Canceling does not save; drafts are not persisted.

Scripts are stored atomically in versioned `scripts.json` in the plugin data folder. Names are case-insensitive
and follow Groups (1–16 ASCII letters/digits/underscores, starting with a letter/underscore). Disk write errors
leave the previous file/in-memory version intact. There is no automatic resume or replay after restart.

```lua
local a = params {
    param.group "group",
    param.pos("origin", "~ ~ ~"),
    param.number("spacing", 2),
}
for i, member in group(a.group):members() do
    local result = member:tp(a.origin.x + (i - 1) * a.spacing, a.origin.y, a.origin.z)
    if not result.ok then error(result.reason or "placement rejected") end
end
```

Call `params` exactly once before host work, even without arguments (`params {}`). It yields the declaration
before the host accepts the body. Inspection executes up to this declaration; code before it has normal budgets
and cannot call Bot/log/wait functions. Constructors are `param.group(name[, default])`, `param.bot(name[, default])`,
`param.pos(name[, default])`, `param.number(name[, default])`, `param.bool(name[, default])`,
`param.string(name[, default])`. Each declaration carries its name; `a.<name>` access is unchanged.
Pass a contiguous array of declarations. Named-key, mixed and sparse tables are rejected, as are duplicate
names, unknown types and invalid defaults. Names match `[A-Za-z_][A-Za-z0-9_]{0,31}`; at most 32 are allowed.
Declaration errors show the `params` call's script line where available and are localized for Japanese players.
The runner/host protocol carries `schema` as a JSON array, including `[]` for no parameters; each entry is
`{"name":"spacing","type":"number","default":2}`. The plugin validates this array again before forms or execution.

**Positional arguments and menu controls follow declaration order.**
Only trailing arguments can be omitted; earlier defaulted parameters still occupy positions. The example accepts
`/botscript run row #miners` or `/botscript run row #miners "~ ~ ~" 3`. Quote positions and strings with spaces.
Positions return `{x,y,z}`; declared `~` coordinates resolve from the player's position captured when submitting
the command/form. Console positions must be absolute. Groups/Bots must exist; numbers must be finite;
booleans accept `true`/`false`. Run forms use that same order with Group/Bot dropdowns, position fields
defaulting to the player, number/text inputs and toggles with declared defaults.
Form selections also pin Group UUID/order and Bot UUID, rejecting deletion/recreation before submission.
Declared Bot arguments keep their UUID pin on subsequent requests; explicitly Group-pinned calls retain
the Group's own identity guard.

日本語: 引数は配列で宣言し、各 `param` の最初の引数に名前を指定します。コマンドの引数と実行フォームは
宣言順です。上の例は `group`、`origin`、`spacing` の順で、末尾の引数を省略すると宣言した初期値を使います。
引数名の重複、無効な名前、不明な型、名前付きキーや配列との混在はエラーになります。

Ordinary operation calls return **acceptance**, not completion: `{ok=true,messages=...,data=...}` or
`{ok=false,reason=...,messages=...}`. Group mutations include counts in `data`; partial failure has `ok=false`.
Handle rejected calls explicitly. Revocation, rate/deadline and protocol errors end the run.
Use `wait(ticks)` (20 ticks = one wall-clock second) and `waitOnline(bot("Alice"))`, which also accepts a name
or Group handle. `log(text)` emits bounded log text. Waits recheck authority. No command-string API exists.

### Structured status and mining waits

`bot:status()` returns the usual result wrapper; read its `data` fields below. These values come from
runtime status, including its retained last mining job; they do not infer completion from elapsed time.

| Field | Meaning |
| --- | --- |
| `data.name` | Current Bot name |
| `data.identityId` | Bot UUID |
| `data.connectionState` | Runtime connection state; `online` means connected |
| `data.mining.active` | Boolean: a block destroy job or continuous mining loop is active (including waiting for a target) |
| `data.mining.mode` | `once` or `continuous`; nil before any mining job |
| `data.mining.state` | Runtime job state (`active`, `waiting`, `completed`, `canceled`, `failed`), or nil with no job |
| `data.mining.blocksBroken` | Runtime continuous-loop count; for a single block, 1 if completed, otherwise 0; 0 with no job |
| `data.mining.blockCount` | Continuous count limit, or nil when unlimited/no job |
| `data.mining.reason` | Nil while active; otherwise the runtime's terminal reason, or nil with no job |

Terminal reasons include `block count reached`, `duration elapsed`, `stopped`, and runtime failure reasons.
Null JSON fields become nil in Luau. `group:status()` returns `data.results` in pinned membership order;
each row has `index`, `identityId`, `name`, `ok`, `reason` (nil on success), and `data` containing that
member's fields above (nil on query failure). Partial query failure sets the outer `ok=false`.

`bot:waitMining{timeout=1200}` and `group:waitMining{timeout=1200}` wait only for destroy/mining jobs.
They return `{ok=boolean, reason=string, results={...}}`. Each result has `name`, `identityId`, `ok`,
`reason`, and `mining` (the fields above). The group waits for all current members, pinning membership
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

Fast CI builds/tests the runner on Ubuntu; release-candidate jobs build/test both platform runners and bundle
self-tests smoke-run a params-only script without BDS. These are required checks, not a record of a completed
candidate run. **Caller must run Python suites and live
plugin/BDS checks**: multiline paste limits/cancel behavior, generated controls, Japanese rendering, stale forms,
logout/rejoin, revoke/regrant (including mid-operation), Group replacement, overlap cleanup, crashes, shutdown,
and tick timing. Both platform bundles require real CI/consumer validation; OS confinement remains deployment work. Tests never establish world achievement
history, actual Xbox achievement unlocks or another Endstone/BDS pair's support. See [the spike](LUAU_RUNNER_SPIKE.md).

## Live validation

Live results below come from `build/scripting-live-3` and, after the run-lifetime/status/`waitMining` changes, `build/scripting-live-4` (all cases including Mine at spots) on 2026-10-23 JST (Windows RakNet, Endstone `0.11.13+endbot.1` / BDS `1.26.52.3`, reusable sandbox). Earlier attempts found that Endstone's tomlkit config integers were rejected (fixed) and that the probe sampled positions too coarsely (fixed). In the pinned Python environment with the plugin
installed and runtime dependencies present, build the native runner, then run from the repository root:

```text
node runner/build.mjs
python scripts/raknet_spike.py --scripting --server-dir build/scripting-sandbox --output build/scripting-live-1
```

Use a new ignored output directory each time. `--server-dir` is the existing disposable sandbox mechanism;
it resets the sandbox world. The probe enables `[scripting]` only in that generated instance config and uses
the locally built `runner/.build/native/bin/endbot-luau-runner[.exe]` and matching `api.luau`. Missing/stale builds
fail with the build command. No server settings are weakened. Omit `--scripting` to retain the default config.

| Check | Evidence required | Result |
| --- | --- | --- |
| Group row template, spacing 3 | Two Bots placed by `botscript run line #script_probe "x y z" 3`; BDS SDK positions within 0.35 blocks of expected offsets; completion and input cleanup log | Passed |
| Patrol stopped by name and run ID | Real move/wait loop runs listed; both Bots move, stop retires the run, fresh BDS samples stay within 0.15 blocks over 1.2 seconds | Passed |
| Console overlap and subsequent input | Later console movement survives run cleanup; console movement after stop also advances by at least 0.5 blocks | Passed |
| Runaway loop | `while true do end` terminates with `execution budget exceeded` and cleanup log | Passed |
| Mine at spots (new case) | Four SDK-placed dirt blocks at two spots become air in BDS; each spot logs `block count reached`; normal completion and cleanup | Passed |
| Console list/runs | Stored names, active run ID/name and final empty runs output | Passed |
| Disabled default | Unit coverage in `test_scripting.py`: every `/botscript` entry rejects under default `ScriptConfig`, without constructing a runner; no restart phase | Passed (plugin unittest suite) |

The fixture installs only into the output's plugin copy, prepares a flat Survival arena using the SDK, and
saves via the same guarded paste-form command path (including compilation/declaration inspection). Position
evidence comes from BDS `player.location`, never runtime prediction. Console positions are absolute because
console has no player origin; in-game callers can use `"~ ~ ~"`.
`report.json` contains `scripting` results and source/binary hashes; `scripting-results.json` retains phase
evidence even on failure, including sample sequences, observed/expected positions, tolerances and console output.
These ignored raw artifacts are operator evidence. They do not establish achievement history or Xbox unlocks.
