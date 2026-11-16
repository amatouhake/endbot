# Scripting setup for operators

How to turn Luau scripting on, the settings and limits it runs under, and how each run is confined. Players and
scripters read [SCRIPTING.md](SCRIPTING.md) instead; how the runner works inside is in
[SCRIPTING_INTERNALS.md](SCRIPTING_INTERNALS.md#runner-process-and-os-confinement).

Scripting is **off by default**. Each run is one native child process started by endbotd; the Node Bedrock runtime
and the Endstone core are not involved. No process starts merely because scripting is enabled.

**Contents**

- [Install and enable](#install-and-enable)
- [Settings](#settings)
  - [Settings in `endbot.toml`](#settings-in-endbottoml)
  - [Fixed limits](#fixed-limits)
- [How the limits behave](#how-the-limits-behave)
- [OS resource confinement](#os-resource-confinement)
  - [What each platform applies](#what-each-platform-applies)
  - [When confinement fails or a cap is hit](#when-confinement-fails-or-a-cap-is-hit)
- [What the sandbox is and is not](#what-the-sandbox-is-and-is-not)
- [Building the runner from source](#building-the-runner-from-source)

## Install and enable

The Windows and Linux operator bundles include `app/<version>/runner/endbot-luau-runner[.exe]` and its trusted
`api.luau` sidecar. No developer tools or downloads are needed.

1. In the instance's `endbot.toml`, add or change:

   ```toml
   [scripting]
   enabled = true
   ```

2. Restart with `endbot start`.

Controller grants and roles still decide who may save and run scripts ([PERMISSIONS.md](PERMISSIONS.md)).

What `endbot start` does with this section:

- It writes `[scripting]` into the generated `state/generated/endbotd.toml` on every start. Edit `endbot.toml`, never
  the generated file: it is overwritten.
- It writes the absolute path of the **running app version's** runner, so `endbot update` and `--rollback` select
  that version's runner.
- `endbot setup` writes `enabled = false`. A missing key or section also means off.

To use another runner, set `[scripting] runner = 'path'` (see [Settings](#settings-in-endbottoml)). The runner
executable and `api.luau` must sit together; if either is missing, a run or save fails with
`scripting runner not built/installed`.

## Settings

All scripting settings are read at start. Restart Endbot after changing any of them.

### Settings in `endbot.toml`

The `[scripting]` keys an instance accepts. Any other key in this section is refused with `is not recognized`.

| Setting | Default | Effect |
| --- | --- | --- |
| `enabled` | `false` | Turns scripting on |
| `runner` | empty | Runner executable to use instead of the bundled one. A relative path resolves from `state/generated/` (the directory of `endbotd.toml`), so give an absolute path. Empty with no bundled runner looks for `endbot_core/runner/endbot-luau-runner[.exe]` beside the installed `endbot_core` package |
| `os-confinement` | `true` | Applies the [OS resource confinement](#os-resource-confinement) and fails the run if it cannot be applied. `false` opts out of every OS mechanism |
| `process-memory-bytes` | `268435456` (256 MiB) | Memory cap for the runner process. `0` disables it. A nonzero value must be at least `100663296` (96 MiB: the 32 MiB VM bound plus 64 MiB native headroom) |
| `cpu-seconds` | `60` | Cumulative CPU seconds per run, separate from wall time and VM slices. `0` disables it. See below |
| `book-export` | `true` | Lets controllers write a script into the book and quill they hold (`/botscript export`). `false` means the plugin never changes a held item. Import from a book is always available |
| `chat-logs` | `true` | `false` keeps a script's `log` output in the server log only, not in the invoker's chat |
| `ui-pack-editor` | `false` | Offers the whole-script field that needs the optional [UI pack](UI_PACK.md#for-operators). Turn it on only if players install the pack |
| `replies` | `"failures"` | What a script's operation replies echo to the invoker's chat: `all`, `failures` (refused operations only) or `none` ([details](SCRIPTING.md#operation-replies-in-chat)) |

`cpu-seconds` counts CPU time only, so waits use none and long-waiting macros are unaffected. It is what ends a run
stuck inside one C library call (`string.rep`, `string.gsub`), which the per-slice budget cannot interrupt.

Example:

```toml
[scripting]
enabled = true
os-confinement = true
process-memory-bytes = 268435456
cpu-seconds = 30
```

### Fixed limits

These limits cannot be set in `endbot.toml`: it refuses them, and `endbot start` regenerates `endbotd.toml` without
them, so a managed instance runs with these defaults. Only a developer who runs endbotd with their own config file
(`python -m endbotd --config ...`) can change them, under that file's `[scripting]`.

| Limit | Default | Meaning |
| --- | ---: | --- |
| `controller-runs` | 2 | Concurrent runs per controller |
| `server-runs` | 8 | Concurrent runs server-wide |
| `wall-seconds` | 0 | `0` sets no total run deadline. A positive value limits each run, including declarations, waits and IPC, in wall seconds |
| `slice-ms` | 100 | Cooperative VM deadline between yields |
| `interrupt-budget` | 1000000 | Safepoint callbacks per VM slice between yields, not exact instructions |
| `operations-per-second` | 20 | Sliding one-second limit on host operations; an operation over it waits for a free slot |
| `max-script-bytes` | 32768 | UTF-8 source limit on save and run; at most 65536 (the protocol bound) |
| `max-scripts` | 64 | Stored-script creation limit |
| `memory-bytes` | 33554432 (32 MiB) | VM allocator bound, at least 1 MiB. Excludes compiler and process allocations, which `process-memory-bytes` covers |

Where a developer config sets them: values are integers from 1 to 2147483647 (`wall-seconds` from 0), and a nonzero
`process-memory-bytes` must be at least `memory-bytes + 67108864`. An invalid `[scripting]` section in `endbotd.toml`
is logged and turns scripting off; `/bot` keeps working.

## How the limits behave

**Run lifetime.**

- With `wall-seconds = 0`, a run ends on completion, `/botscript stop`, authority revocation, or plugin or server
  shutdown.
- The per-slice interrupt and time budget, VM memory, the operation rate and the concurrency limits always apply.

**Operation rate.**

- An operation over `operations-per-second` waits on the host until the one-second window has room, then runs.
- The wait counts against `wall-seconds` and ends on `/botscript stop`, like `wait`. The run log notes the first such
  wait.
- All IPC events, including logs and zero waits, are bounded separately by `4 * operations-per-second` in a sliding
  one-second window. Exceeding that bound fails the run with `Script operation rate limit reached`.

**Logs.**

- Each run logs at most 2000 lines or 512 KiB. Past that, one notice is logged and further `log` output is dropped.
- Script text reaching the log or chat (logs and error messages) has color codes and control characters (CR, LF,
  ESC) replaced, so it cannot forge log lines or drive a terminal.

**Saves and declaration checks.**

- A form or save declaration check has a deadline of at most 5 seconds and needs free concurrency capacity.
- The protocol bounds each frame to 256 KiB and bounds nesting, string and object sizes. Highly escaped source may
  hit the frame bound before `max-script-bytes`.

**Changing limits.**

- Changes apply on restart.
- Disabling scripting or lowering storage limits keeps stored source. A stored script over the new size limit cannot
  run until it is edited or the limit is raised.

## OS resource confinement

Each run, including a declaration check, gets its own confinement object. It bounds resources and lifetime; it is not
a filesystem or network sandbox (see [What the sandbox is and is not](#what-the-sandbox-is-and-is-not)).

- Confinement is supported on Windows and Linux. With `os-confinement = true` on another OS, every run fails.
- `os-confinement = false` turns off every mechanism in the table below. VM, IPC, wall-time, authority and
  concurrency limits stay in force.
- A setting of `0` disables only that memory or CPU cap. It never raises a limit the process inherited from the OS.

### What each platform applies

| Resource | Windows (Job Object) | Linux (applied by the runner before it reads input) |
| --- | --- | --- |
| Process memory | `JOB_OBJECT_LIMIT_PROCESS_MEMORY` bounds committed bytes | `RLIMIT_AS` bounds virtual address space; an allocation may fail with `ENOMEM` |
| Child processes | `JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 1`, no breakaway: the runner cannot start descendants | No per-run prohibition (`RLIMIT_NPROC` is per user and deliberately unused). The trusted runner never spawns, and Luau has no spawning API |
| Parent lifetime | `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`: closing the job or BDS exiting kills the runner | `PR_SET_PDEATHSIG` with `SIGKILL`, and a check that the parent is still the expected process |
| Cumulative CPU | Optional `JOB_OBJECT_LIMIT_PROCESS_TIME`, user CPU seconds, checked periodically by Windows | Optional `RLIMIT_CPU`, user and system CPU seconds: `SIGXCPU` at the cap, `SIGKILL` one second later |
| Priority | Below normal for runs; a declaration check runs at normal priority, because a person is waiting | `nice(+10)` for runs, none for a declaration check; an already lower priority stays lower |

Notes:

- On Linux, stricter inherited soft or hard limits are kept.
- On Windows, the runner's native startup code can run in the short interval before it is assigned to its job. It
  waits for its first input frame, so no script runs in that interval.
- The parent-death signal assumes the normal, unprivileged runner executable. Set-ID bits, file capabilities or
  credential changes can clear it.

### When confinement fails or a cap is hit

If confinement cannot be set up, the run is rejected before any script code runs. Any started runner is killed, the
error is logged, and the run is never retried unconfined. This includes a Windows job that cannot be created or
assigned, for example inside an incompatible enclosing job.

| Message the player sees | Cause |
| --- | --- |
| `Script runner OS confinement failed; see the server log` | Confinement could not be applied; the server log has the reason |
| `Script runner exceeded the CPU time limit` | `cpu-seconds` was reached (Linux `SIGXCPU`, or Windows job CPU accounting at the cap) |
| `Script runner process memory allocation failed` | Startup `ENOMEM`, or a Windows no-memory or commitment-limit status. It cannot tell which memory limit was hit |
| `Script runner failed: ...` | Anything ambiguous: Linux `SIGKILL` (hard CPU cap, OOM killer, parent death, external kill), `SIGSEGV`, a native abort |
| `Script stopped or time limit reached` | `wall-seconds` was reached, or `/botscript stop` |
| `Script runner did not respond in time; the server may be busy, try again` | The host stopped a runner that had not read its input in time, such as a declaration check on a busy PC |

A runner the host stops itself is never reported as an OS cap.

Memory caps usually refuse allocations rather than produce a special exit code. The 64 MiB headroom over
`memory-bytes` is a validation margin, not a promise that every script and library combination fits; check your
settings on each platform you run.

## What the sandbox is and is not

What isolates a run:

- The runner's VM, IPC and time budgets keep a failure inside one run.
- Luau's `io`, `os`, `require`, file loaders, debug and environment access, and script-managed coroutines are removed
  at VM level.
- The runner executable and `api.luau` are trusted installation assets.
- Native compiler and JSON allocations are outside VM memory accounting and covered by `process-memory-bytes`.

What it does not do:

- OS confinement does not restrict filesystem or network access. The runner has the privileges of the account that
  runs Endbot.
- Restricted accounts, seccomp, namespaces, cgroups, and Windows restricted tokens or AppContainer are not installed.
  Running Endbot under a restricted account is deployment work for you.

Scripting changes neither the Node runtime nor the Endstone core. Keep `online-mode=true`, `allow-cheats=false`, no
experiments, Beta APIs or GameTest, and the normal Microsoft/Xbox authentication.

What was checked on real servers and clients is in [validation status](COMPATIBILITY.md#scripting).

## Building the runner from source

Bundles carry a prebuilt runner. Build one only for a development install:

1. From a standalone clone, run `node runner/build.mjs --out build/runner-stage`. It needs Node 24+, CMake, Ninja,
   `tar` and a C++17 toolchain (an MSVC developer shell on Windows, GCC on Ubuntu 22.04).
2. Install `build/runner-stage/endbot-luau-runner[.exe]` and `api.luau` together.
3. In `endbot.toml`, set `[scripting] runner` to the executable's absolute path and `enabled = true`, then restart.

Build options, local MinGW and the pinned dependencies are in [runner/README.md](../runner/README.md). Official Luau
0.743 is pinned by commit and SHA-256 in `runner/luau.lock.json`, without source changes.

If you distribute a build, keep `licenses/LUAU_LICENSE.txt` and `licenses/NLOHMANN_JSON_LICENSE.txt` with it; bundles
list them in `THIRD_PARTY_NOTICES.md`.
