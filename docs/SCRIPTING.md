# Luau scripting (MVP)

Scripting runs in the **Endbot plugin**, in one native child process per run. The Node Bedrock runtime is
unchanged. Each script belongs to the controller who created it (see [ownership](#ownership-visibility-and-revisions));
each run uses its invoker's authenticated identity and grant revision. Scripting is optional and **off by default**. No process starts merely because it is enabled.

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

Startup limits (integers from 1 to 2147483647, except the zero/boolean settings below):

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
| `os-confinement` | true | Boolean; fail the run if OS confinement cannot be applied; explicit false opts out of OS confinement |
| `process-memory-bytes` | 268435456 | 256 MiB process cap; 0 disables this cap; nonzero requires at least `memory-bytes + 67108864` (64 MiB native headroom) |
| `cpu-seconds` | 60 | Cumulative CPU seconds per run, independently of wall time and VM slices; waits use none, so long-waiting macros are unaffected. It is what ends a run stuck inside one C library call (`string.rep`, `string.gsub`), which the per-slice budget cannot interrupt. 0 disables the cap |

Managed instances also accept `os-confinement`, `process-memory-bytes` and `cpu-seconds` in
`endbot.toml` under `[scripting]`; the CLI forwards overrides into the generated plugin config.
Other limits in this table are plugin `config.toml` settings. Managed instances retain the default
32 MiB VM bound, so a nonzero process cap must be at least 96 MiB there. For example:

```toml
[scripting]
enabled = true
os-confinement = true
process-memory-bytes = 268435456
cpu-seconds = 30
```

## OS resource confinement

Each run, including declaration inspection, gets its own confinement object. OS setup failure rejects
the run before supplied script execution, kills/reaps any started runner, releases handles and logs the error.
Players receive a localized error. `os-confinement = false` is an explicit opt-out of all mechanisms
in this table; it leaves VM, IPC, wall-time, authority and concurrency limits in force. Zero disables
only the configured memory or CPU cap; it does not raise inherited OS limits.

| Resource | Windows | Linux |
| --- | --- | --- |
| Process memory | Job Object `JOB_OBJECT_LIMIT_PROCESS_MEMORY` bounds committed bytes | Native runner sets `RLIMIT_AS` at startup to bound virtual address space; allocation may fail with `ENOMEM` |
| Child processes | Job `JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 1`, no breakaway flags; descendants cannot start after assignment | No per-run OS child-process prohibition. `RLIMIT_NPROC` is per UID and is deliberately unused. The trusted runner never spawns; Luau exposes no spawning API |
| Parent lifetime | Non-inheritable job handle and `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`; closing the job or BDS process exit kills the runner | Native startup calls `prctl(PR_SET_PDEATHSIG, SIGKILL)`, then checks `getppid()` against the supplied parent PID before reading stdin. Also kills on creator-thread exit |
| Cumulative CPU | Optional `JOB_OBJECT_LIMIT_PROCESS_TIME`, user CPU seconds, periodically checked by Windows | Optional `RLIMIT_CPU`, user + system CPU seconds; `SIGXCPU` at the soft cap, `SIGKILL` one second later if ignored |
| Priority | `BELOW_NORMAL_PRIORITY_CLASS` at process creation | Native startup calls `nice(+10)` before reading stdin; already lower priorities stay lower |

Windows configures the job first, starts the trusted executable with `Popen`, then immediately assigns
the process handle before starting pipe readers or writing its load frame. Native loader/startup code
can run before assignment; **this startup race is not eliminated**. The runner blocks on its first
NDJSON frame, so no supplied script runs in that interval. If job creation/configuration/assignment
fails (including incompatible enclosing jobs), the run fails closed without retrying unconfined.
See Microsoft's [job limits](https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-jobobject_basic_limit_information)
and [assignment semantics](https://learn.microsoft.com/en-us/windows/win32/api/jobapi2/nf-jobapi2-assignprocesstojobobject).

Linux passes OS limits as runner command-line arguments; Python performs no child callback between
fork and exec. The native runner applies confinement at the start of `main`, before reading any stdin,
and preserves stricter inherited soft/hard limits. Native loader/startup code executes before these
controls, but supplied script input is not read until they succeed. The flags are:

```text
--max-address-space BYTES --max-cpu-seconds N --parent-pid PID --nice 10
```

Pass all four flags together, or omit all four for the standalone harness or `os-confinement = false`.
Values must be unsigned decimal integers: memory 0 disables `RLIMIT_AS`, CPU 0 disables `RLIMIT_CPU`,
PID must be positive, and the nice increment must be 10. Memory must be below 18446744073709551615
and fit the native rlimit type; CPU and PID must be at most 2147483647.
Unknown, missing, duplicate, partial or invalid arguments fail before reading stdin. Any argument or
confinement failure emits one NDJSON `error` with `phase = "confinement"` and a message beginning
`confinement failed:`, then exits nonzero. The plugin treats this as a localized startup failure and
never retries unconfined. Windows validates these flags if supplied but ignores their effects; the
plugin omits them and applies the Job Object instead. Real embedded Python/BDS validation remains pending.

The parent-death signal assumes the normal, unprivileged runner executable: set-ID/file capabilities
or credential changes can clear it. See [Linux resource limits](https://man7.org/linux/man-pages/man2/getrlimit.2.html)
and [parent-death signal semantics](https://man7.org/linux/man-pages/man2/PR_SET_PDEATHSIG.2const.html).

Linux `SIGXCPU` and Windows nonzero exits with job user-time accounting at the cap report a CPU limit
failure. Startup `ENOMEM` and explicit Windows no-memory/commitment-limit native statuses report allocation failure;
they cannot prove which memory constraint was hit. Memory limits generally reject allocations rather
than guaranteeing a special exit code. Linux `SIGKILL` (hard CPU cap, OOM killer, parent death or an
external kill), `SIGSEGV`, native `bad_alloc`/abort and other ambiguous exits remain runner failures.
Host stop/watchdog kills are never attributed to an OS cap. The memory floor is validation headroom,
not a promise that every source or loader/library combination fits; validate on both target systems.

These are resource and lifetime controls, **not a filesystem/network OS sandbox**. Luau's `io`, `os`,
`require`, loaders, debug/environment access and script-managed coroutines are removed at VM level.
The native executable still has its account's filesystem/network privileges. Restricted accounts,
seccomp/namespaces/cgroups and Windows restricted tokens/AppContainer are not installed here.

The protocol also bounds frames to 256 KiB and bounds nesting/string/object sizes. Highly escaped source may
hit the frame bound earlier. Form/save declaration inspection has a deadline of at most five seconds and
requires available concurrency capacity. Changes apply on restart. Disabling scripting or lowering storage
limits retains stored source; oversized stored scripts cannot run until edited or the limit is raised.
IPC events (including logs and zero waits) are bounded by `4 * operations-per-second` in a sliding
one-second window, independently of run lifetime. Each run logs at most 2000 lines or 512 KiB; past that one
notice is logged and further `log` output is dropped. Script text reaching the log or chat (logs and error
messages) has color codes and control characters (CR, LF, ESC) replaced, so it cannot forge log lines or
drive a terminal. Negative `wall-seconds` values are rejected.
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
/botscript stop <run-id|name|all>
/botscript delete <name>
/botscript copy <name> <new-name>
/botscript share <name>
/botscript private <name>
/botscript history <name>
/botscript restore <name> <n>
/botscript help
/botscript menu
```

`runs`/`stop` address the invoking controller's runs. The console is the operator: its `runs` lists every controller's runs with their owner, and its `stop <id|name|all>` stops any of them, so a run left behind by a controller who logged out can be ended without revoking that controller. Start, stop, save and delete are logged with the controller's name, and so is each run's end. Deleting a script
does not cancel running copies. Show displays numbered, bounded pages and wraps long lines without dropping
source. Source is never loaded from a user-supplied filesystem path.

### Ownership, visibility and revisions

Each script records its creator as owner (by XUID, else player UUID). Only the owner, or the console, may edit,
delete, restore or change its visibility; everyone else gets "only the owner may change" and is pointed to
**Copy and edit**. A script is `private` (only the owner sees and runs it; to others it does not exist) or
`shared` (every controller may list, show, run and copy it). New scripts and copies start `private`;
`share`/`private` switch it. `copy <name> <new-name>` makes a private copy owned by the copier. Every save keeps
the previous source in a history of the last 10 revisions; `history` lists them (newest first, with time and
line count) and `restore <name> <n>` makes revision `n` current as a new save, keeping the replaced source in the
history. Runs still use the invoker's authority, whoever owns the script. Scripts saved before ownership (file
version 1) have no owner and stay shared; the first controller to edit one becomes its owner. Lists show
`name (owner, visibility)` with `yours` for your own. Not implemented: separate run/author permissions
(`endbot.script.run`/`endbot.script.author`); every controller may still author.

When enabled, `/bot menu` includes **Scripts...**. A script opens Run, Show source (paged), Stop runs and
Copy and edit; its owner also gets Edit source, Share/Make private, Earlier revisions (restore with a
confirmation that shows that revision's source) and Delete (confirmation). Create offers **Group row**, **Simple patrol**, **Mine at spots**, and a blank script.
Editing uses one paste field with current source as default; the client draws that field only about two lines
tall, so a read-only label above it shows the whole current source with line numbers. Save compiles the complete source and inspects
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
| `data.look` | `{yaw, pitch}` in degrees |
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
| `bot:block(x, y, z)` | `{x, y, z, type, states}`; `"~"`/`"~-1"` strings are relative to the Bot; any loaded position |
| `bot:blocks("oak_log", {radius=8, limit=10})` | `{type, radius, blocks}`; `blocks` lists `{x, y, z, distance}` nearest first, only blocks with a face open to air, water or plants (no x-ray); radius 1-12, limit 1-64 |

A radius-12 search took about 23 ms of server-thread time in the live check (2026-10-24).

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
| `:entities()` | none | none |
| `:entity()` | none | none |
| `:entity()` | target: string | none |
| `:target()` | none | none |
| `:block()` | x: coordinate, y: coordinate, z: coordinate | none |
| `:blocks()` | type: string | radius: integer, limit: integer |
| `:resume()` | none | none |
| `:reconnect()` | none | none |
| `:despawn()` | none | none |
| `:forget()` | none | none |
| `:stop()` | none | none |
| `:dismount()` | none | none |
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
| `:interact()` | none | none |
| `:interact()` | x: coordinate, y: coordinate, z: coordinate | none |
| `:interact()` | x: coordinate, y: coordinate, z: coordinate, face: down / up / north / south / west / east | none |
| `:interact()` | target: string | mode: once / continuous / stop, interval: integer, ticks: integer, count: integer |
| `:destroy()` | none | mode: once / continuous / stop, ticks: integer, count: integer |
| `:destroy()` | x: coordinate, y: coordinate, z: coordinate | face: down / up / north / south / west / east |
| `:destroy()` | type: string | mode: continuous, ticks: integer, count: integer |
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
script-managed coroutines are removed. The executable/bootstrap are trusted installation assets. Native compiler/JSON
allocations are outside VM memory accounting and covered by the optional process cap described above.
OS resource confinement does not restrict native filesystem/network access; restricted accounts and further
distribution hardening remain deployment work. The Node runtime and Endstone core are unchanged.
Keep `online-mode=true`, `allow-cheats=false`, no experiments/Beta APIs/GameTest and normal Microsoft/Xbox authentication.

Fast CI builds/tests the runner on Ubuntu; release-candidate jobs build/test both platform runners and bundle
self-tests smoke-run a params-only script without BDS. These are required checks, not a record of a completed
candidate run. **Caller must run Python suites and live
plugin/BDS checks**: multiline paste limits/cancel behavior, generated controls, Japanese rendering, stale forms,
logout/rejoin, revoke/regrant (including mid-operation), Group replacement, overlap cleanup, crashes, shutdown,
and tick timing. Both platform bundles require real CI/consumer validation, including OS confinement setup,
memory pressure, cumulative CPU termination and parent-death cleanup. Mocked and real-OS probes live in
`plugin/endbot/tests/test_script_confine.py`; real probes run only on their matching OS and the native
runner CPU probe skips when a matching build is unavailable. These new Python/OS probes have not been
run in the implementation sandbox. Tests never establish world achievement
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
