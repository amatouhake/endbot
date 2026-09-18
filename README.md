# Endbot

Endbot is a practical Fake Player foundation for the official Minecraft Bedrock Dedicated Server (BDS), using
[Endstone](https://github.com/EndstoneMC/endstone) as the extension platform. It preserves the normal Microsoft/Xbox
authentication path for people while adding a narrow, server-owner-controlled ES384 trust path for accountless bots.

Endbot is pre-alpha. M0/M1 establishes reproducible patched Endstone source, a minimal `/bot ping` plugin probe, local
identity signing, safety preflight, tests, and release metadata. Movement, groups, teleportation, macros, work tasks,
Discord, blueprints, web UI, and AI behavior are not implemented.

## Safety invariants

- Official BDS and Endstone; no replacement server.
- `online-mode=true` and the vanilla Microsoft/Xbox validation path remain intact.
- `allow-cheats=false`; no experiments, Beta APIs, GameTest, or required packs.
- Local bot auth is disabled by default and accepts only the configured issuer, ES384 owner signature, valid lifetime
  and audience, unused token ID, valid UUID/name, and client-public-key binding.
- BDS is downloaded by Endstone's normal bootstrap and is never bundled here.

These settings preserve an achievement-compatible configuration and world state in the validated baseline. An actual
Xbox achievement unlock has **not** yet been observed as an Endbot release gate; see [achievement validation](docs/ACHIEVEMENTS.md).

## Compatibility

| Component | Pinned baseline |
| --- | --- |
| Endstone | `v0.11.11` / `37b395378d91d6d20f1c52bf9d79dbd20e152458` |
| Patched Endstone package | `0.11.11+endbot.1` |
| BDS | `1.26.51.1` (build `51061372`) |
| Bedrock protocol | `2193` |

`endstone.lock` is authoritative. A green build alone does not establish support for another pair.

## Architecture

Endbot keeps the Endstone delta small and reproducible:

```text
pinned official Endstone + patches/endstone series -> disposable patched checkout
                                                      -> modified Endstone build

Endbot plugin  -> commands, permissions, registry, future control plane
Endbot runtime -> local bot identity, future NetherNet client/controller
```

The core patch is necessary because BDS rejects an accountless login inside its login-validation path before a normal
plugin can authorize it. All non-local and invalid candidates continue through the original Microsoft validator.
Ordinary bot behavior belongs outside this patch.

Bots have one hidden immutable UUID for persistence and one unique user-visible Minecraft name as their command handle.
There is no second user-visible “Bot ID” and no hidden command-selection state.

## Prepare and test

Requirements for the foundation tests are Python 3.10+, Node.js 22+, and Git. From a fresh clone:

```bash
python3 scripts/prepare_endstone.py
PYTHONPATH=plugin/endbot/src python3 -m unittest discover -s plugin/endbot/tests -v
python3 -m unittest discover -s tests -v
npm test --prefix runtime
python3 scripts/check_portability.py
```

The first command verifies `v0.11.11` resolves to the exact locked commit, keeps a bare upstream cache under `.cache/`,
clones a disposable tree to `build/endstone-patched`, applies the ordered patch series with `git am`, and tags that
patched commit with the locked Endbot-local package version. It refuses to overwrite an existing output directory. To
reuse a populated cache without network access, choose a new output and add `--offline`.

To build and run all patched upstream tests on Linux:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip conan
cd build/endstone-patched
conan install . --build=missing
. build/RelWithDebInfo/generators/conanbuild.sh
cmake --preset conan-relwithdebinfo -DENDSTONE_GENERATE_STUBS=OFF
cmake --build --preset conan-relwithdebinfo
ctest --preset conan-relwithdebinfo --output-on-failure
```

Endstone's Linux build requires its documented Clang/libc++ toolchain. CI installs and tests that toolchain explicitly.

Build the plugin wheel with `python -m build --wheel plugin/endbot`; install it together with the patched Endstone
wheel. The plugin requires the exact Endbot-local package version, so pip cannot satisfy it with the official unpatched
`0.11.11` wheel. The plugin registers `/bot ping`, returns `Endbot: pong`, and uses
`endbot.command.control`. The permission defaults to false and is attached only to player UUIDs/XUIDs explicitly listed
in the generated plugin `config.toml`; it does not grant operator or vanilla command rights.

Before starting a server, run the scoped property check:

```bash
python3 scripts/preflight.py /path/to/server.properties --acknowledge-world-state-unverified
```

The acknowledgement names the check's boundary; it does not certify world history or Xbox achievement behavior.

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Security model](docs/SECURITY.md)
- [Achievement safety and the M0 manual test](docs/ACHIEVEMENTS.md)
- [Compatibility and release gates](docs/COMPATIBILITY.md)
- [Third-party notices](THIRD_PARTY_NOTICES.md)

## Contributing

Read [AGENTS.md](AGENTS.md), keep the auth patch narrow, add tests with each behavior change, and run both fast test
suites plus the patch preparation check. Do not commit secrets, BDS binaries, generated worlds, or machine-local paths.
The project is licensed under Apache-2.0.
