# Endbot

Endbot is a practical Fake Player foundation for the official Minecraft Bedrock Dedicated Server (BDS), using
[Endstone](https://github.com/EndstoneMC/endstone) as the extension platform. It preserves the normal Microsoft/Xbox
authentication path for people while adding a narrow, server-owner-controlled ES384 trust path for accountless bots.

Endbot is pre-alpha. It provides persistent multi-bot profiles, real NetherNet client sessions, lifecycle/reconnect,
server-API teleport, rename, movement/actions, hotbar selection, block interaction, and item drop behind the validated
`/bot` authorization surface. Groups, macros, work tasks, Discord, blueprints, web UI, and AI behavior are not
implemented.

## Safety invariants

- Official BDS and Endstone; no replacement server.
- `online-mode=true` and the vanilla Microsoft/Xbox validation path remain intact.
- `allow-cheats=false`; no experiments, Beta APIs, GameTest, or required packs.
- Local bot auth is disabled by default and accepts only the configured issuer, ES384 owner signature, valid lifetime
  and audience, unused token ID, valid UUID/name, and client-public-key binding.
- BDS is downloaded by Endstone's normal bootstrap and is never bundled here.

These settings preserve an achievement-compatible configuration and world state in the validated baseline. The recorded
M0 gate and the later M2 Windows-client session each observed an actual Xbox achievement unlock from a normally
authenticated Bedrock client against historical patched package `0.11.11+endbot.1`; the current `0.11.12+endbot.1`
artifact has no new Xbox-achievement observation. See [achievement validation](docs/ACHIEVEMENTS.md) for the exact scope and caveats.

## Compatibility

| Component | Pinned baseline |
| --- | --- |
| Endstone | `v0.11.12` / `1c71186cba896c5e0bc432384a8a8e72dfb2a626` |
| Patched Endstone package | `0.11.12+endbot.1` |
| BDS | `1.26.51.1` (build `51061372`) |
| Bedrock protocol | `2193` |

`endstone.lock` is authoritative. A green build alone does not establish support for another pair.

## Architecture

Endbot keeps the Endstone delta small and reproducible:

```text
pinned official Endstone + patches/endstone series -> disposable patched checkout
                                                      -> modified Endstone build

Endbot plugin  -> commands, human authorization, BDS observation and teleport
       | authenticated JSON request/response on loopback
Endbot runtime -> profiles, local identity, NetherNet sessions, reconnect and inputs
```

The core patch is necessary because BDS rejects an accountless login inside its login-validation path before a normal
plugin can authorize it. All non-local and invalid candidates continue through the original Microsoft validator.
Ordinary bot behavior belongs outside this patch.

Bots have one hidden immutable UUID for persistence and one unique user-visible Minecraft name as their command handle.
There is no second user-visible “Bot ID” and no hidden command-selection state.

## Prepare and test

Requirements for the foundation tests are Python 3.10+, Node.js 24+, and Git. From a fresh clone:

```bash
python3 scripts/prepare_endstone.py
PYTHONPATH=plugin/endbot/src python3 -m unittest discover -s plugin/endbot/tests -v
python3 -m unittest discover -s tests -v
npm test --prefix runtime
python3 scripts/check_portability.py
```

The first command verifies `v0.11.12` resolves to the exact locked commit, keeps a bare upstream cache under `.cache/`,
clones a disposable tree to `build/endstone-patched`, applies the ordered patch series with `git am`, and tags that
patched commit with the locked Endbot-local package version. It refuses to overwrite an existing output directory. To
reuse a populated cache without network access, choose a new output and add `--offline`.

To build and run all patched upstream tests on Linux:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip build cibuildwheel==3.4.1 conan ninja uv
cd build/endstone-patched
conan install . --build=missing
. build/RelWithDebInfo/generators/conanbuild.sh
cmake --preset conan-relwithdebinfo -DENDSTONE_GENERATE_STUBS=OFF
cmake --build --preset conan-relwithdebinfo
ctest --preset conan-relwithdebinfo --output-on-failure
```

Endstone's Linux build requires its documented Clang/libc++ toolchain. CI installs and tests that toolchain explicitly;
the repaired wheel command additionally requires Docker.
After the build, `python3 scripts/build_endstone_wheel.py` runs the pinned checkout's cibuildwheel configuration. Its
manylinux container invokes Endstone's `auditwheel` repair command, and only the repaired wheel is written under
`dist/endstone/`. CI inspects its ELF dependencies/loader paths and imports it in a clean Linux image.

Build the plugin wheel with `python -m build --wheel plugin/endbot`; install it together with the repaired patched
Endstone wheel. The plugin requires the exact Endbot-local package version, so pip cannot satisfy it with the official
unpatched `0.11.12` wheel. The plugin registers the `/bot` tree, including the original `/bot ping`, and uses
`endbot.command.control`. The permission defaults to false and is attached only to player UUIDs/XUIDs explicitly listed
in the generated plugin `config.toml`; it does not grant operator or vanilla command rights. See
[commands and operation](docs/COMMANDS.md).

Install runtime dependencies and create an operator-owned configuration outside source control:

```bash
npm ci --prefix runtime
cp runtime/endbot-runtime.example.json endbot-runtime.json
node runtime/src/cli.js --config endbot-runtime.json
```

The runtime creates its control token and owner key on first start. Configure the plugin's `[runtime].token-file` to
the same token file and configure patched Endstone with the exported owner public key. All control traffic is bound to
loopback; remote binds are rejected. Paths in the example are resolved relative to the copied configuration file.

Before starting a server, run the scoped property check:

```bash
python3 scripts/preflight.py /path/to/server.properties --acknowledge-world-state-unverified
```

The acknowledgement names the check's boundary; it does not certify world history or Xbox achievement behavior.

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Installing from release artifacts](docs/INSTALL.md)
- [Security model](docs/SECURITY.md)
- [Achievement safety and the M0 manual test](docs/ACHIEVEMENTS.md)
- [Compatibility and release gates](docs/COMPATIBILITY.md)
- [Commands and operation](docs/COMMANDS.md)
- [M2 validation record](docs/M2_VALIDATION.md)
- [Native Windows development and live smoke](docs/WINDOWS_DEV.md)
- [Third-party notices](THIRD_PARTY_NOTICES.md)

## Contributing

Read [AGENTS.md](AGENTS.md), keep the auth patch narrow, add tests with each behavior change, and run both fast test
suites plus the patch preparation check. Do not commit secrets, BDS binaries, generated worlds, or machine-local paths.
The project is licensed under Apache-2.0.
