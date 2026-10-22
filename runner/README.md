# Endbot Luau runner

The small C++ host runs official, unmodified Luau 0.743 in a child process for the
Endbot plugin. Operator bundles ship it prebuilt for Windows x64 and Linux x86_64;
scripting stays off until enabled in `endbot.toml`. See [scripting](../docs/SCRIPTING.md).

Source builds require Node >=24, CMake >=3.15, Ninja, a C/C++17 toolchain and
gzip-capable `tar`. On Windows use an MSVC developer shell (CI uses
`ilammy/msvc-dev-cmd` and `lukka/get-cmake`); on Ubuntu 22.04 GCC works.

```sh
cd runner
node build.mjs
node --test
node demo.mjs
# Or supply a source file and a JSON args object:
node demo.mjs examples/interleaved.luau '{"first":"One","second":"Two","ticks":3}'
```

From the repository root, stage a platform build for bundle assembly:

```sh
node runner/build.mjs --out build/runner-stage
```

The staging command above runs from the repository root; the earlier commands run
from `runner/`. `--out` (or `ENDBOT_RUNNER_OUT`) resolves against the caller's working
directory. Every build writes `runner/.build/native/bin/endbot-luau-runner[.exe]` and
`api.luau`. The staging directory also receives `LUAU_LICENSE.txt` from the verified
Luau archive and `NLOHMANN_JSON_LICENSE.txt` (MIT). Pass it to
`scripts/build_bundle.py --runner-dir build/runner-stage` on the same platform.

No npm dependencies or Python are required. Only the two URLs in `luau.lock.json`
are downloaded; cached bytes are SHA-256 verified on every build and mismatches
fail immediately. Extracted Luau sources are restored from the verified archive.
Source inputs are pinned; toolchains and byte-identical binary output are not.
`CMAKE` can select the CMake executable. Extra arguments go to CMake, for example
`-G "MinGW Makefiles"` for local MinGW. Use a fresh `.build/native` tree when
changing generators. MSVC links the host and Luau with the static CRT.

Downloads and native outputs stay in ignored `.deps/` and `.build/`; keep custom
staging directories under ignored `build/`. Never commit binaries or dependencies.
The [original research and protocol record](../docs/LUAU_RUNNER_SPIKE.md) is historical;
the [scripting guide](../docs/SCRIPTING.md) tracks deployment and validation limits.
