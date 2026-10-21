# Luau runner spike

Research checked **2026-10-22**. This is a runner selection experiment, not BOT-009 implementation or a new
Endstone/BDS compatibility claim. Findings below distinguish observed metadata/source from execution tests and
unverified behavior. Release/download figures are snapshots, not guarantees about future maintenance.

## Scope and decision inputs

Read BOT-009 on this branch and the decisions in local `docs/luau-decisions` at
`8638588476ecc50bc8cdc48f0ad483d731c486e1` (`docs/BACKLOG.md`, heading “Decisions for the 0.4.0 MVP”).
That heading is dated 2026-10-23; the research check date here remains 2026-10-22. Neither branch nor backlog was
modified. The agreed direction is a typed API covering every `/bot` operation, a shared operation definition,
declared parameters, explicit waits, host authorization before every operation, optional scripting off by default,
configurable limits and stoppable runs in a process separate from the Node Bedrock runtime.

This spike implements only parameter declaration, `wait`, `log`, single-Bot `move` and `look` against a **mock host**.
The full typed/generated operation API, Groups, editor, persistence, grant identities and production cleanup remain
future work. There is no plugin/runtime integration or scripting enablement setting added here; only explicitly
running the standalone harness launches this runner.

## Comparison

All source URLs in this section were checked on 2026-10-22. “Unverified” means no execution/build validation here;
an API in source is not evidence of working Endbot integration.

| Option | Provenance, maintenance and license | Embedding, suspension and interruption | Windows/Linux delivery cost and risks |
| --- | --- | --- | --- |
| Official release CLIs | `luau-lang/luau`, Roblox/Lua copyright, MIT. Latest API result: **0.743**, published **2026-10-16**. Releases 0.738–0.743 were published Sep 11, Sep 18, Sep 25, Oct 2, Oct 10 and Oct 16: roughly weekly in this sample, not a promised cadence. [Release API](https://api.github.com/repos/luau-lang/luau/releases?per_page=6), [license](https://github.com/luau-lang/luau/blob/0.743/LICENSE.txt). | CLI execution is available, but a command-line executable does not itself expose a host's C callbacks. A custom bridge for suspension, replies and interrupts would still be needed. This is an engineering inference from the [REPL host implementation](https://github.com/luau-lang/luau/blob/0.743/CLI/src/Repl.cpp). | Published assets are `luau-windows.zip`, `luau-ubuntu.zip`, `luau-macos.zip`, plus `Luau.Web.js`; there is no separate embedding SDK/library asset in 0.743's asset list. Windows and Ubuntu archives downloaded and inspected here each contain `luau`, `luau-analyze`, `luau-ast`, `luau-compile` (Windows `.exe`). Mac archive contents/CPU architectures were **not verified**. [0.743 asset metadata](https://api.github.com/repos/luau-lang/luau/releases/tags/0.743). |
| Official release web artifact | Same upstream and MIT license. `Luau.Web.js` is an official release asset, **4,214,342 bytes** in 0.743 metadata. [Release](https://github.com/luau-lang/luau/releases/tag/0.743), [asset metadata](https://api.github.com/repos/luau-lang/luau/releases/tags/0.743). | The pinned source exports `executeScript` and `checkScript`; `executeScript` creates a fresh state, runs synchronously and reports a yielded thread as an error. It does not expose resumable state, arbitrary host callbacks or an interrupt setter through these exports. [Web.cpp](https://github.com/luau-lang/luau/blob/0.743/CLI/src/Web.cpp). | One JS file embeds WASM (`SINGLE_FILE=1`), avoiding separate native OS builds; execution under Node here is **unverified**. The wrapper would need replacement/extension for this project's IPC and waits. [Pinned CMake web target](https://github.com/luau-lang/luau/blob/0.743/CMakeLists.txt). |
| Build official source, small C++ process (**recommended and prototyped**) | Exact upstream **0.743 / `9d7748a4f324213a18cb8283fa2a77c330225fa0`**, MIT. The [tag API](https://api.github.com/repos/luau-lang/luau/git/ref/tags/0.743) resolves directly to that commit. No Luau source patches. | Direct `lua_newthread`, `lua_resume`, `lua_yield`, `lua_callbacks()->interrupt`, sandbox helpers and a custom allocator are available in the [pinned public API](https://github.com/luau-lang/luau/blob/0.743/VM/include/lua.h) and [library API](https://github.com/luau-lang/luau/blob/0.743/VM/include/lualib.h). The prototype exercises these paths. Interrupt callbacks count safepoints, **not exact bytecode instructions**. | Requires CMake, C/C++17 compiler and `tar` at build time, with an executable per OS/architecture. Windows build/test passed; Linux build/bundling **unverified**. Static Luau linkage avoids installing Luau on controller machines. Native C++ remains a memory-safety/security maintenance responsibility; process separation is not OS confinement. |
| Build official source with an Emscripten custom host | Same pinned source can be used, with separately pinned Emscripten/compiler inputs. Upstream has `LUAU_BUILD_WEB` and a web build in [CI](https://github.com/luau-lang/luau/blob/0.743/.github/workflows/build.yml). | Custom exported coroutine and interrupt functions could use the same C API. This is a feasibility inference; a working asynchronous IPC/coroutine bridge was **not built or tested** here. The stock web wrapper is insufficient as described above. | Could share one WASM payload across Windows/Linux Node child processes; adds WASM glue, async suspension design and Emscripten toolchain pinning. Upstream's [release workflow](https://github.com/luau-lang/luau/blob/0.743/.github/workflows/release.yml) installs `emsdk latest`, so reproducing that workflow unchanged would not lock the toolchain. |
| npm `luau-web` | npm maintainer **`xnasuni`**; latest **1.5.1**, **2026-10-11**; MIT; 15 registry versions observed. It bundles bindings to the maintainer's `luau-interop` fork rather than an unmodified official artifact. [Registry](https://registry.npmjs.org/luau-web), [project README](https://github.com/xNasuni/luau-web/blob/main/README.md). | Custom environments and JS↔Luau calls are documented; fork source uses `EM_ASYNC_JS` and async calls. Asyncify/JSPI glue is shipped. A public interrupt/budget setter was **not found** in the inspected [TypeScript API](https://github.com/xNasuni/luau-web/blob/main/src/index.d.ts); safe interruption and exact coroutine behavior remain **unverified**. [Interop implementation](https://github.com/xNasuni/luau-interop/blob/master/CLI/src/Web.cpp). | npm unpacked size **2,136,308 bytes**; one JS/WASM distribution could serve both OSes plus Node (execution/bundling **unverified** here). More interop and fork code to audit. Exact upstream version of npm 1.5.1 **unverified**; no release-to-upstream mapping established. Registry integrity can pin the package, not establish source/build equivalence. |
| npm `rainsi-lua` | npm maintainer **`idiotbready`**; latest **1.10.3**, **2026-10-17**; MIT; 8 registry versions observed. Registry README advertises **Luau 0.743** and WASI. [Registry](https://registry.npmjs.org/rainsi-lua), [version metadata](https://registry.npmjs.org/rainsi-lua/1.10.3). | README advertises JS function registration, globals and synchronous/async calls. Resumable Luau coroutines and an exposed Luau interrupt/budget API are **unverified**. Its advertised upstream version is a publisher claim, not independently validated source provenance. | npm unpacked size **6,222,655 bytes** includes multiple interpreters. A WASM payload could avoid native OS builds. Public [repository README](https://github.com/lalipa2003-arch/rainsi/blob/main/README.md) still describes PUC-Rio Lua only, unlike npm's Luau claims; this discrepancy needs resolution before adoption. Registry artifacts were not executed. |
| Rust `mlua` with `luau` | Maintained binding project; latest GitHub release **v0.12.2**, **2026-10-03**, previous v0.12.1 Aug 29; MIT. [Releases API](https://api.github.com/repos/mlua-rs/mlua/releases?per_page=2), [pinned manifest](https://github.com/mlua-rs/mlua/blob/v0.12.2/Cargo.toml). | `set_interrupt`, `sandbox`, `set_memory_limit`, threads and async functions are documented. This materially offers higher-level lifetime/error handling around the C API. [Lua API](https://docs.rs/mlua/0.12.2/mlua/struct.Lua.html), [thread API](https://docs.rs/mlua/0.12.2/mlua/struct.Thread.html). | A Rust host still needs native Luau/C++ compilation and per-platform binaries; adds Cargo/Rust to this repository. `Cargo.lock` can pin dependencies; `mlua-sys`'s `luau` feature depends on `luau0-src` (manifest constraint **0.22.0**), another source distribution to trace. [Sys manifest](https://github.com/mlua-rs/mlua/blob/v0.12.2/mlua-sys/Cargo.toml). No Rust build or exact embedded upstream revision verified here. |

Weekly npm API snapshots (returned range **2026-10-12 through 2026-10-18**, checked 2026-10-22):

| Package | Downloads in returned week | Source |
| --- | ---: | --- |
| `luau-web` | 1,782 | [npm downloads API](https://api.npmjs.org/downloads/point/2026-10-12:2026-10-18/luau-web) |
| `rainsi-lua` | 180 | [npm downloads API](https://api.npmjs.org/downloads/point/2026-10-12:2026-10-18/rainsi-lua) |

Download counts are maintenance/context signals, not security evidence. Search also found npm `luau` **1.0.1**
(2016-09-16), whose description is Redis Lua script loading, not this language. `wasmoon` describes a Lua VM rather
than Luau. Neither substitutes for a Luau implementation. [luau registry](https://registry.npmjs.org/luau),
[Wasmoon project](https://github.com/ceifa/wasmoon).

## Sandbox and provenance details

Official Luau removes `io` and `package`; it retains a restricted `os` library. Removing all of `os` is an embedder
choice, not an upstream default. Freezing standard libraries/globals and isolating script globals requires the
embedding sandbox helpers. The native prototype additionally removes `os`, loader functions, environment/debug access,
`print` and script-managed coroutines; only its own scheduler can yield. Community JS/WASI wrappers need equivalent
library/global auditing, and any supplied host object/imports extend their authority. Their complete exposed-global
sets were **not verified** here. [Official sandbox guidance](https://luau.org/sandbox/).

For the official CLI specifically, its host registers a `require` implementation and `loadstring` before freezing
the environment; its default surface therefore differs from this spike. For the official web artifact, `openlibs`
includes restricted `os`, and its wrapper does not offer a global-removal configuration. A custom native/WASM host
can remove globals before freezing and omit `require` registration, as done here. [CLI setup](https://github.com/luau-lang/luau/blob/0.743/CLI/src/Repl.cpp),
[default libraries](https://github.com/luau-lang/luau/blob/0.743/VM/src/linit.cpp).
`mlua::Lua::new_with` accepts standard-library flags, enabling an embedder to select libraries and withhold host
loaders; the exact `io`/`os`/`require` surface of a configured Luau-backed Rust host is **unverified** here.
[mlua library selection](https://docs.rs/mlua/0.12.2/mlua/struct.Lua.html#method.new_with).

The interrupt callback runs at VM safepoints; elapsed-time checks there are cooperative. A long native builtin or
compilation is not governed by an exact instruction quota. Therefore the harness also owns a wall-clock deadline
and can kill the child without waiting for IPC or a VM callback. The VM allocator limit does not include compiler,
C++ JSON or other process allocations. A production service still needs OS process memory/CPU limits and restricted
process permissions. [Callback documentation](https://luau.org/api/#callbacks),
[pinned compiler implementation](https://github.com/luau-lang/luau/blob/0.743/Compiler/src/Compiler.cpp).

`runner/luau.lock.json` pins version, commit, public download URL, SHA-256 and license for Luau, plus
[`nlohmann/json` 3.12.0](https://github.com/nlohmann/json/releases/tag/v3.12.0) for the small C++ host's JSON handling.
The checksums were **computed from the fetched bytes**, not invented or represented as author-signed attestations:

| Input | Measured SHA-256 |
| --- | --- |
| [Luau commit archive](https://codeload.github.com/luau-lang/luau/tar.gz/9d7748a4f324213a18cb8283fa2a77c330225fa0) | `35c10aa3860fd69f465d79a40263ae28f88c5d64ec598010af29ab4df7ad97e3` |
| [JSON single header](https://raw.githubusercontent.com/nlohmann/json/v3.12.0/single_include/nlohmann/json.hpp) | `aaf127c04cb31c406e5b04a63f1ae89369fccde6d8fa7cdda1ed4f32dfc5de63` |

`build.mjs` verifies each cached/downloaded input before use, restores upstream files from the archive and builds
only the host, compiler and VM dependency targets. `.deps/` and `.build/` are gitignored. This follows the
repository's lock-and-source approach without a persistent Luau fork. It pins **source inputs**, not byte-identical
build output: compiler, SDK, CMake and system-library versions are not locked yet. Hermetic builds, signed delivery,
toolchain pinning and cross-platform CI are **unverified/future work**. Both dependencies use MIT licenses;
distribution must preserve their notices, including Luau's Lua notice. [Luau notices](https://github.com/luau-lang/luau/tree/0.743),
[JSON license](https://github.com/nlohmann/json/blob/v3.12.0/LICENSE.MIT).

## Prototype layout and use

The new top-level `runner/` keeps this native build and standalone harness out of `runtime/`, so existing
`npm test` does not discover build-dependent tests or gain dependencies. No npm install is required for the spike.
`main.cpp` owns the VM; `api.luau` is trusted bootstrap; `host.mjs` owns the child, mock authorization and scheduling;
`demo.mjs` and `runner.test.mjs` use real Luau, not a simulated interpreter. The executable is generated at
`runner/.build/native/bin/endbot-luau-spike[.exe]`.

From a standalone clone with Node >=24, CMake >=3.15, `tar` (gzip support) and a C/C++17 toolchain on PATH:

```sh
cd runner
node build.mjs
node --test
node demo.mjs
# Or provide a source file and a JSON args object:
node demo.mjs examples/interleaved.luau '{"first":"One","second":"Two","ticks":3}'
```

Equivalent npm entry points are `npm run build`, `npm test` and `npm run demo` **inside `runner/`**. Building needs
network access initially to the two locked public URLs; cached verified inputs permit subsequent offline builds.
`CMAKE` may name an installed CMake executable. Extra arguments after `build.mjs` are forwarded to CMake configure.
For example, Windows MinGW with `gmake` on PATH:

```powershell
node build.mjs -G "MinGW Makefiles" -DCMAKE_MAKE_PROGRAM=gmake
```

Keep the same generator for subsequent builds of that build directory. The build requests static MSVC CRT linkage;
MinGW additionally links its compiler runtimes statically. Linux uses the selected system toolchain; its minimum
libc/architecture compatibility must be established before packaging. No binaries are tracked or bundled by this spike.

The example uses this deliberately small declaration format (not the final `param.*` API):

```lua
local args = params {
    name = {type = "string", default = "Alice"},
    ticks = {type = "number", default = 2},
}
bot(args.name):move("forward", {ticks = args.ticks})
wait(args.ticks)
bot(args.name):look(90, 10)
log("finished")
```

`params` must be called once before host-facing work. The runner evaluates the declaration, sends its schema and
suspends before returning arguments to the script. The harness validates scalar string/number/boolean arguments and
defaults. `pauseBeforeStart: true` lets a caller inspect `run.ready` before `run.begin()`. This is **execution up to
a declaration**, not static extraction: code before `params` is subject to budgets and cannot use the Bot/log/wait API.
Source is compiled by Luau with syntax locations; static type analysis and API declaration generation are not implemented.

### Protocol and lifetime

Stdio is UTF-8 newline-delimited JSON. Each child handles **one run**; its pipe identifies the run. Production
multiplexing would require explicit run IDs. Stdout contains only protocol messages; logs are JSON events.

| Direction | Message | Meaning |
| --- | --- | --- |
| host → runner | `{"type":"load","source":"...","bootstrap":"...","limits":{...}}` | Load one source; bootstrap is supplied by the trusted harness, never script-controlled. Run args remain host-side until declaration. |
| runner → host | `{"type":"params","id":1,"schema":{...}}` | Suspend; no body operations before acceptance. |
| host → runner | `{"type":"reply","id":1,"accepted":true,"args":{"name":"Alice"}}` | JSON args object returned from `params`. |
| runner → host | `{"type":"operation","id":2,"bot":"Alice","operation":"move","direction":"forward","ticks":2}` | Suspend until host accepts/rejects. `look` carries `yaw`/`pitch` instead. |
| runner → host | `{"type":"wait","id":3,"ticks":2}` | Suspend until host tick timer replies. |
| host → runner | `{"type":"reply","id":2,"accepted":true}` / `{"type":"reply","id":2,"accepted":false,"error":"reason"}` | Matching request only; rejection raises a script error. |
| host → runner | `{"type":"stop","id":"host-stop"}` | Waiting runner exits with a matching `stopped` event. CPU-bound runner is forcibly killed if needed. |
| runner → host | `{"type":"log","text":"..."}` / `{"type":"done"}` / `{"type":"error","phase":"compile","message":"script:3: ..."}` | Unacknowledged events/terminal state. |

Only one request is outstanding. IDs increase per run; stale replies and host EOF fail closed. `move`/`look` resume on
**acceptance**, not completion. The example interleaves Alice/Bob operations with explicit waits. The mock host does
not simulate physics or actually keep inputs running; overlaps are scheduled requests, not validated Bot concurrency.
The host validates operation fields independently and calls its authorization callback immediately before applying
each mock operation, including after waits. The default authorizer is permissive **only for synthetic harness use**.

Configurable harness limits (`startRun` option `limits`):

| Key | Default | Scope |
| --- | ---: | --- |
| `sliceMs` | 100 ms | Elapsed VM time per resume, tested at interrupt safepoints. |
| `interruptBudget` | 100,000 | Total non-GC callback checks across resumes, not an instruction count. |
| `memoryBytes` | 33,554,432 | VM allocator bytes, excluding compiler/JSON/process overhead. |
| `wallMs` | 5,000 ms | Entire run, including declarations, host latency and waits; host kills on expiry. |
| `requestMs` | 1,000 ms | Host-response deadline; scheduled wait duration is added for wait requests. |
| `maxMessages` | 1,000 | Total output/request cap; a coarse bound, not a per-second operation limiter. |
| `maxTicks` | 2,000 | Move/wait bound. |
| `tickMs` | 50 ms | Mock tick duration; tests can use 1 ms. No server tick coupling. |

Fixed framing/value bounds: 256 KiB NDJSON frame, 64 KiB source, 16 KiB bootstrap, 4 KiB log text, conversion depth
8 and at most 128 keys per Luau object. These are spike guards; the final configurable script-size/rate/concurrency
policy is not implemented. The native runner also validates its accepted VM limits. At stop, no further reply resumes
the script; the harness waits for child close and clears timers. A 100 ms fallback kills a child that cannot service
stop. Terminal events and deadlines prevent indefinite host waits.

## Validation recorded on 2026-10-22

| Check | Exact outcome |
| --- | --- |
| Initial Visual Studio configure | Failed: MSBuild SDK discovery could not access the sandbox-denied user SDK directory. No Python or BDS invoked. |
| CMake **3.31.6-msvc6**, MinGW GCC **8.3.0**, Release native build | **Exit 0**. Upstream compiler warnings included visibility attributes, format checks and enum bitfields; not a warning-clean/toolchain support claim. |
| `cd runner; node --test` with Node **v24.17.0** | **13 passed, 0 failed, 0 cancelled, 0 skipped**; recorded duration **981.5846 ms**. |
| `cd runner; node demo.mjs` | **Exit 0**, terminal `done`, child exit 0; declaration id 1, moves Alice/Bob/Alice at ids 2/4/6, waits at 3/5/8, Bob look at 7, log event. |
| Windows executable inspection | **4,083,403 bytes**, `objdump -p` exit 0; imports only `KERNEL32.dll`, `msvcrt.dll`. Clean-machine Windows execution is **unverified**. |
| Existing `cd runtime; node --test` | **208 passed, 0 failed, 0 cancelled, 0 skipped**; recorded duration **2459.0067 ms**. |
| `node --check` on build, host, demo and runner test modules | All **exit 0**. |

The 13 tests exercise: declarations before body and defaults/JSON args; interleaved Bot requests and waits; blocking
until host acceptance; authorization recheck/revocation after wait; interrupt budget against plain infinite loops and
`pcall` attempts to swallow budget errors; elapsed slice limit; stop during wait; independent wall-time kill; stop
during CPU execution; unavailable `io`/`os`/`require`/loaders/private bridge; syntax error at source line 3; bad args and
host rejection; host reply timeout, VM memory exhaustion and bounded output; stale reply IDs and host EOF.

Unverified gates: Linux compilation/run/package, other CPU architectures, macOS runtime, Windows without this build
environment, byte-identical builds, adversarial sandbox/security review, community-package execution and WASM builds.
No Python tests, BDS, plugin integration or real players were run. Production authorization identities/revisions,
bounded cleanup of run-owned continuing inputs, cross-controller conflicts, disconnect behavior, operation rate and
concurrency limits, script editing and full operation/type generation need implementation and separate validation.
World achievement history and actual Xbox achievement unlocks remain explicit manual/E2E gates.

## Recommendation

**Choose a small C++ runner built from pinned official Luau source**, with one process per run initially. This is an
engineering recommendation based on the observations above: direct upstream provenance, tested coroutine suspension
and interruption, minimal native API surface, and a lock-plus-build workflow without maintaining a Luau fork. The
prototype demonstrates the key runner mechanics while keeping the Bedrock runtime independent.

The tradeoff is owning native builds and a small amount of C++ interop. `mlua` is a credible alternative if Rust
becomes a repository toolchain, but adds a binding/source-distribution layer today. Community WASM packages reduce
platform build cost but leave interrupt/provenance gaps unverified in this investigation. An official-source custom
WASM host remains a reasonable later experiment if eliminating per-platform binaries outweighs the extra glue.
Before product adoption, lock the release toolchains, validate Windows/Linux delivery and implement the agreed host
authorization/cleanup and resource-policy gates. This recommendation does not claim that BOT-009 is ready to ship.
