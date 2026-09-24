# Native Windows development and live smoke

This is a development and manual-smoke workflow for running the whole pinned baseline on one Windows machine:

```text
Minecraft for Windows  <->  native Windows patched Endstone + official BDS  <->  native Windows Endbot plugin/runtime
```

It exists so that WSL-hosted behavior (which needs `hostAddressLoopback` networking) can be A/B-tested against a
loopback-only native topology. It is not a release or packaging path, it does not change the pinned Endstone/BDS pair,
and every safety invariant in the README applies unchanged: `online-mode=true`, `allow-cheats=false`, no experiments,
Beta APIs, GameTest, or required packs, the normal Microsoft/Xbox path for people, and local bot auth disabled unless
the operator enables it with the runtime-generated owner public key.

Native Windows hosting of the runtime and server was designed for but was not part of the recorded M2 validation; see
[`COMPATIBILITY.md`](COMPATIBILITY.md).

Current status after PR #4: native Windows hosting has additionally been live-smoked successfully (patched Endstone
`0.11.11+endbot.2`, official Windows BDS `1.26.51.1` build `51061361`, protocol `2193`, Alice local Bot alongside a
normal Microsoft/Xbox human client). Linux nevertheless remains the CI/release-artifact baseline, and the PR #4 smoke
added no new Xbox-achievement observation for `+endbot.2`. The current package is `0.11.12+endbot.1` on upstream
`v0.11.12` (same BDS pair); that rebase awaits its own live regression and new achievement observation.

## Prerequisites

The pinned Endstone builds on Windows with its own supported toolchain, declared in `.conan2/profiles/default` of the
prepared tree: MSVC as the Conan compiler setting, `clang-cl` as the C/C++ compiler executable, `lld-link`, Ninja, and
MSVC `cl` only for `sentry-native`. Precisely:

| Requirement | Where it comes from |
| --- | --- |
| Visual Studio 2022 "Desktop development with C++" (MSVC v143, Windows 10/11 SDK) | Visual Studio Installer |
| `clang-cl` and `lld-link`, LLVM 18+ | Visual Studio Installer component `Microsoft.VisualStudio.Component.VC.Llvm.Clang` ("C++ Clang Compiler for Windows"; VS 17.14 ships LLVM 19). `Microsoft.VisualStudio.Component.VC.Llvm.ClangToolset` is optional. |
| CMake 3.29+, Ninja, Conan 2+ | `pip install cmake ninja conan` inside the project `.venv` |
| Python 3.10+ (python.org build, not the Microsoft Store build) | python.org |
| Node.js 24+ and Git | nodejs.org, Git for Windows |

`scripts/check_windows_toolchain.py` reports each item as `ok`, `not-on-PATH`, `too-old`, or `missing` with the exact
remedy, and never installs anything. `cl`, `clang-cl`, and `lld-link` are on `PATH` only inside a Visual Studio
"x64 Native Tools" developer prompt, so run the build steps from the Start-menu "x64 Native Tools Command Prompt for
VS 2022" or "Developer PowerShell for VS 2022" shortcut. (Invoking `Launch-VsDevShell.ps1` directly from a plain
PowerShell prints a benign `vswhere.exe` not-found message on some installations but still puts `cl` on `PATH`.)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip build hatchling packaging ruff cmake ninja conan
python scripts\check_windows_toolchain.py
```

Do not run `conan profile detect`; the prepared tree ships its own Jinja profile and the PEP 517 backend uses it.

## Fast tests

These run unmodified on Windows:

```powershell
python scripts\check_portability.py
ruff check scripts tests plugin\endbot
python -m unittest discover -s tests -v
$env:PYTHONPATH = "plugin\endbot\src"
python -m unittest discover -s plugin\endbot\tests -v
Remove-Item Env:PYTHONPATH
python -m build --wheel --outdir dist\plugin plugin\endbot
npm ci --prefix runtime --allow-remote=root
npm run check --prefix runtime
npm test --prefix runtime
```

`--allow-remote=root` is an npm 12 policy opt-in, not a Windows requirement: npm 12 refuses tarball URL dependencies
by default, and `root` permits only the URLs already written in `runtime/package.json` (the pinned
`prismarine-xbox-services` source tarball, which is not on the npm registry). Pass the flag on the npm 12 command line
only; no tracked `.npmrc` change is needed. No dependency install script is needed: the NetherNet transport is pure
JavaScript (`werift`), and `raknet-native`'s blocked install warning on npm 12 is expected and harmless because Endbot
never uses the RakNet transport.

## Prepare and build patched Endstone

```powershell
python scripts\prepare_endstone.py
```

This creates `build\endstone-patched` exactly as on Linux (bare cache under `.cache\`, `git am` of the patch series,
local tag `v0.11.12+endbot.1`). The script refuses to overwrite an existing output directory; to start over, run
`Remove-Item -Recurse -Force build\endstone-patched` first (the tree also accumulates a project-local Conan cache
under its ignored `.conan2\` entries, which `git -C build\endstone-patched clean -fdX` resets on its own). Then,
from a Visual Studio x64 developer prompt with `.venv` active:

```powershell
cd build\endstone-patched
python -m pip install -U . -C build-dir=./build
```

This is upstream's supported source install: the `conan-py-build` backend runs Conan with the shipped profile, builds
with Ninja/clang-cl, and installs the `endstone` package into the active environment. `build-dir` keeps the C++ build
tree inside the (git-ignored) `build\` directory of the prepared checkout so reinstalls are incremental. The first
build downloads and compiles Conan dependencies and takes a long time.

Verify the Endbot-local package contract before going further:

```powershell
python -c "import importlib.metadata as m, endstone; print(m.version('endstone'), endstone.__minecraft_version__)"
```

Expected: `0.11.12+endbot.1 26.51` (Endstone reports the Minecraft version without the leading `1.`; its bootstrap
prepends it, so this is BDS `1.26.51.x`). The prepared tree carries the local tag `v0.11.12+endbot.1` on the patched
commit (see `git -C build\endstone-patched tag --points-at HEAD`), which the build backend reads for the package
version. Do not install the official `endstone==0.11.12` wheel into this
environment; the plugin's exact pin exists to prevent running against an unpatched server.

## Install the plugin

With the same `.venv` active, from the repository root:

```powershell
python -m pip install -U plugin\endbot
python -c "import importlib.metadata as m; print(m.entry_points(group='endstone'))"
```

The `endstone` entry-point group must list `endbot = endstone_endbot.plugin:EndbotPlugin`. Endstone discovers
installed entry points itself; nothing needs to be copied into the server's `plugins\` folder.

## Run the runtime

Keep the configuration and its generated secrets outside the repository, for example in `C:\endbot-local`:

```powershell
New-Item -ItemType Directory C:\endbot-local
Copy-Item runtime\endbot-runtime.example.json C:\endbot-local\endbot-runtime.json
node runtime\src\cli.js --config C:\endbot-local\endbot-runtime.json
```

Relative paths in the file resolve against the file's own directory, so the example creates
`C:\endbot-local\secrets\control.token`, `owner-private.pem`, `owner-public.pem`, and `data\profiles\`. On Windows
the runtime relies on the user profile's NTFS ACLs instead of POSIX `0600` modes; keep the directory under the
operator's own profile. The runtime prints `{"event":"endbot_runtime_ready","host":"127.0.0.1","port":19142}` when
the loopback control socket is listening. A non-loopback `controlHost` is rejected at startup and must stay that way.

Start BDS before spawning a Bot. Observed on Windows, and not believed to be Windows-specific: a spawn issued while
BDS is unreachable stays `connecting`, a later `despawn` fails with `Timed out waiting for the Bedrock session to
close`, the profile is retained in `failed` state, and `despawn`/`forget` do not clear it. Restart the runtime with
BDS reachable before retrying.

## Start native BDS/Endstone

From a plain PowerShell with `.venv` active (no developer prompt is needed at run time):

```powershell
New-Item -ItemType Directory C:\endbot-local\server
endstone -s C:\endbot-local\server
```

Endstone's normal bootstrap downloads the official BDS that matches its pinned Minecraft version (`1.26.51`, BDS
`1.26.51.1` build `51061361` on Windows, protocol `2193`) into the server folder after confirmation; never copy a BDS binary into
the repository. (The Linux compatibility baseline records build `51061372` for the same release; see the
per-platform distinction below.) On Windows the bootstrap also runs `CheckNetIsolation LoopbackExempt -a` for the Minecraft for
Windows app SID through a UAC prompt so the same-machine client can reach a loopback server; if you decline, run that
command yourself from an elevated prompt, otherwise the client will not see the local server.

Stop the server after the first start, then configure:

1. `server.properties`: keep `online-mode=true` and `allow-cheats=false`; do not enable experiments or packs. Check
   with `python scripts\preflight.py C:\endbot-local\server\server.properties --acknowledge-world-state-unverified`.
   BDS `1.26.51` ships `allow-list=true`. It applies to humans only: a Bot accepted by the local-bot trust path joins
   without an `allowlist.json` entry (see `docs/OPERATIONS.md`). Add every human tester to `allowlist.json`, or set
   `allow-list=false` on a local smoke server (it is an access list, not an authentication or achievement invariant). Leave `server-udp-ports` unset: delete the shipped `server-udp-ports=19132` line (or leave it
   commented) and do not replace it with a range. Since upstream Endstone `v0.11.12`, Endstone no longer writes
   `server-udp-ports` on NetherNet servers, and the server shares a single UDP port (the signaling port) across
   sessions instead of allocating one port per client from that window. A bare single-port value narrows Bedrock's
   UDP allocation window so every session must bind the same port while each one asks for its own socket; that is
   what previously limited a NetherNet server to one client (second client got an SDP answer with no ICE candidates
   and the game reported `Kelp` / `InitialConnection-32` before any `Player connected:` line). The old
   `server-udp-ports=20000-20100` range workaround is unnecessary on this baseline and must not be applied: leaving
   the property unset lets Bedrock pick its own window again, and the second-connection regression check below
   verifies the fix rather than depending on the old accidental single-port configuration.
2. `endstone.toml`: set the `[local-bot-auth]` table from `config\endstone.local-auth.example.toml`, `enabled = true`,
   and `public-key-file` pointing at the runtime's `owner-public.pem` (copy it next to `endstone.toml` or use an
   absolute path). Only the public key is ever configured in Endstone. The `[network]` table now also carries
   `stun-servers` (default `stun:stun.l.google.com:19302`); Endstone's config migration adds it to an existing
   `endstone.toml` on upgrade. It is only used with `transport=nethernet` behind NAT so clients learn the server's
   public address, and loopback smoke needs no change there.
3. `plugins\endbot\config.toml` (written by the first start): set `[runtime].token-file` to the runtime's
   `control.token` (absolute path) and keep `host = "127.0.0.1"`, `port = 19142`. Add the human tester's UUID or
   decimal XUID to one `[authorization]` allowlist; do not make the tester an operator. To capture the XUID, let the
   tester join once and read `Player connected: <name>, xuid: <xuid>` from the server log; the plugin reads its
   allowlist at load, so restart the server after editing.

Restart the server. The log shows the Endbot plugin enabling, and `Local bot authentication is enabled for issuer
'endbot://local-bot'` on the first login attempt. The runtime must be running before any `/bot <name> spawn`.

BDS generates a new NetherNet DTLS identity on every start. The runtime does not pin it (see `docs/SECURITY.md`), so
Bots resume after a BDS restart without manual steps; an old `bds-nethernet.pin` from rc.1 can be deleted.

The Windows package of BDS `1.26.51.1` reports `Build ID: 51061361`; the Linux package of the same release, which the
compatibility baseline records, reports `51061372`. Both were downloaded through Endstone's pinned metadata with
integrity checks, and the public tracker for this release lists both IDs, so the difference is per-platform packaging
rather than a different Minecraft version.

`endbot.command.control` is attached only to allowlisted players, so the server console cannot run `/bot` (BDS answers
`Incorrect permission level for command: bot`). To confirm an accountless join before a human tests, drive the runtime
directly over its loopback control socket with the plugin's own client, for example
`RuntimeControlClient("127.0.0.1", 19142, Path(r"C:\endbot-local\secrets\control.token")).request("spawn", name="Alice")`
from `endstone_endbot.control`; the server log must then show `Accepted local bot 'Alice' (...)`, `Player Spawned:
Alice xuid:` with an empty XUID, and vanilla `list` counts the Bot. This bypasses only the command layer and spawn
placement, not the login path.

## Manual smoke

Join from Minecraft for Windows through normal Microsoft/Xbox sign-in and run, in order:

```text
/bot
/bot ping
/bot Alice spawn
/bot Alice status
/bot Alice jump
/bot Alice move forward
/bot Alice attack
/bot Alice interact <x> <y> <z> <face>
/bot Alice stop
/bot Alice despawn
```

`/bot ping` proves plugin-to-runtime loopback control; `spawn` proves the accountless local-bot login through the
patched validator, which the server log reports as `Accepted local bot 'Alice' (...) from issuer
'endbot://local-bot'`. Record the human jump behavior here before comparing with the WSL topology. A native smoke
that does not reproduce the previously observed WSL jump anomaly establishes only that this topology did not
reproduce it; it is not a root-cause finding for the WSL behavior.

## Cleanup and shutdown

Stop the client first, then despawn any Bot (`/bot Alice despawn`), stop the runtime with `Ctrl+C`, and stop BDS
with `stop` in its console. Leave the downloaded server, world, generated secrets, runtime profiles, and saved
NetherNet pin under the outside-the-repository directory (for example `C:\endbot-local`) or delete that directory
when the smoke machine no longer needs it. Never copy BDS binaries, worlds, secrets, pins, or
machine-specific addresses or identifiers into the repository. The prepared `build\endstone-patched` tree and the
project-local `.venv\`, `build\`, `dist\`, and `node_modules\` directories are git-ignored build state.

This guide does not publish Windows release artifacts and does not change the pinned compatibility pair in
`endstone.lock`: Linux remains the CI/release-artifact baseline even though native Windows hosting has been
live-smoked successfully after PR #4 (see [`COMPATIBILITY.md`](COMPATIBILITY.md)), and the Windows build-ID distinction above is
per-platform packaging, not a new baseline. The post-PR-#4 Windows smoke added no new Xbox-achievement observation
for `+endbot.2`, and the current `0.11.12+endbot.1` rebase likewise has none yet.
