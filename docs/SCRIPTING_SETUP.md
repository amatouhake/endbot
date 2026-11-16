# Scripting setup for operators

How to turn Luau scripting on, what limits it runs under, and how each run is confined. Players and scripters read
[SCRIPTING.md](SCRIPTING.md) instead.

Each run is one native child process started by endbotd. The Node Bedrock runtime and the Endstone core are not
involved. Scripting is **off by default**, and no process starts merely because it is enabled.

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
| `operations-per-second` | 20 | Sliding one-second host operation limit; an operation over it waits for a free slot |
| `max-script-bytes` | 32768 | UTF-8 source limit on save/run, maximum 65536 (protocol bound) |
| `max-scripts` | 64 | Stored-script creation limit |
| `memory-bytes` | 33554432 | VM allocator bound, minimum 1 MiB; excludes compiler/process allocations |
| `os-confinement` | true | Boolean; fail the run if OS confinement cannot be applied; explicit false opts out of OS confinement |
| `process-memory-bytes` | 268435456 | 256 MiB process cap; 0 disables this cap; nonzero requires at least `memory-bytes + 67108864` (64 MiB native headroom) |
| `cpu-seconds` | 60 | Cumulative CPU seconds per run, independently of wall time and VM slices; waits use none, so long-waiting macros are unaffected. It is what ends a run stuck inside one C library call (`string.rep`, `string.gsub`), which the per-slice budget cannot interrupt. 0 disables the cap |

Managed instances also accept `os-confinement`, `process-memory-bytes`, `cpu-seconds`, `book-export`, `chat-logs`,
`ui-pack-editor` and `replies` in
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
An operation over `operations-per-second` waits on the host until the window has room, then runs; the wait counts
against `wall-seconds` and ends on `/botscript stop` like `wait`, and the run log notes the first such wait. IPC events
(including logs and zero waits) are bounded by `4 * operations-per-second` in a sliding one-second window,
independently of run lifetime; exceeding that bound still fails the run. Each run logs at most 2000 lines or 512 KiB; past that one
notice is logged and further `log` output is dropped. Script text reaching the log or chat (logs and error
messages) has color codes and control characters (CR, LF, ESC) replaced, so it cannot forge log lines or
drive a terminal. Negative `wall-seconds` values are rejected.
With zero, runs still end on completion, `/botscript stop`, authority revocation, or plugin/server shutdown.
Per-slice interrupt/time, memory, operation rate and concurrency limits always apply.

## What the sandbox is and is not

Runner VM/IPC/time budgets isolate failures to a run. io/os/require, file loaders, debug/environment access and
script-managed coroutines are removed. The executable/bootstrap are trusted installation assets. Native compiler/JSON
allocations are outside VM memory accounting and covered by the optional process cap described above.
OS resource confinement does not restrict native filesystem/network access; restricted accounts and further
distribution hardening remain deployment work. The Node runtime and Endstone core are unchanged.
Keep `online-mode=true`, `allow-cheats=false`, no experiments/Beta APIs/GameTest and normal Microsoft/Xbox authentication.

## What is checked

Fast CI builds and tests the runner on Ubuntu; release-candidate jobs build and test both platform runners, and the
bundle self-tests smoke-run a params-only script without BDS. Mocked and real-OS confinement probes live in
`plugin/endbot/tests/test_script_confine.py`; a real probe runs only on its matching OS, and the native runner CPU
probe skips when no matching build is available. The live checks against BDS and on clients are under
[validation status](COMPATIBILITY.md#scripting).

Tests never establish world achievement history, actual Xbox achievement unlocks or another Endstone/BDS pair's
support. Keep `online-mode=true`, `allow-cheats=false`, no experiments, Beta APIs or GameTest, and the normal
Microsoft/Xbox authentication.
