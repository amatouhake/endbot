# Compatibility and releases

## Current candidate (0.5.0)

`endstone.lock` now pins Endstone `v0.11.15` at `b4aaa87d2c5305e7d340168f824ac2e2ea722e44`, with BDS `1.26.60.6`
(Linux build `52886190`, Windows build `52886189`) and protocol `2225`, with `support_status: "candidate"`. It is not a
supported pair until the compatibility gate below passes and the lock is set back to `validated-baseline`.

- Minecraft 26.60 clients use protocol `2225` and cannot join the `1.26.52` pair, which is why 0.5.0 moves.
- BDS 1.26.60 refuses players on RakNet ("NetherNet is the only supported transport type", checked on the Linux build
  2026-10-30), so Endbot drops its opt-in RakNet transport.
- The Endbot patch series needed only a three-way context rebase (`runtime/CMakeLists.txt`,
  `server_network_handler.cpp`, `tests/CMakeLists.txt`).
- The runtime moves to `bedrock-protocol` 3.61.0 and `minecraft-data` 3.119.0, which ship the 1.26.60 / protocol-2225
  schema; the runtime unit suite passes against it.
- Upstream 0.11.15 binds event cancellation per event class; the plugin uses none of that API.

### 0.11.15 / 1.26.60.6 compatibility gate — 2026-10-30

- Release-candidate runs 41023533108 and 41024591616 cold-built and upstream-tested the patched Endstone on Linux and
  Windows. The bundle consumer E2E (fresh setup acquiring BDS `1.26.60.6` through Endstone, a Bot joining through
  local-bot trust, console, `doctor --live`, update and rollback) passed on both platforms, with LAN discovery and
  with direct signalling; the Linux rollback start then exposed the server-port TIME-WAIT wait now in `endbot start`
  (BOT-026).
- Windows live smoke (`scripts/raknet_spike.py --mining --inventory --item-use --entity --groups --scripting`, direct
  signalling, BDS build `52886189`) passed: movement, jump, chunk radius, reconnect, mining, pickups and drops, timed
  item use, feeding and riding, Groups, Luau scripts, controller management across a BDS restart, and the wrong-key,
  wrong-issuer, wrong-audience and auth-disabled refusals. Three fixture fixes came out of it (a wider scripting
  platform, a retry while a fed cow wanders off the crosshair, waiting for the plugin to relink after a restart); one
  run also failed once on a pickup/drop check that passed on the other runs.
- Still open, for people: a 26.60 client joining alongside a Bot on the same PC, and an achievement observation.
  The lock stays `candidate` until they pass.

## Previous baseline (0.2.1 to 0.4.0)

Endstone `v0.11.13` at `3491c609ddfde392cee2b062e3063e39aae87274`, with BDS `1.26.52.3` (Linux build `51798919`,
Windows build `51798905`) and protocol `2193`.

Upstream `v0.11.13` adds BDS `1.26.52` support with the same protocol `2193`, so the runtime schema is unchanged. The
Endbot patch series needed only a context rebase (the new upstream `stun-servers` default line beside
`[local-bot-auth]`). Upstream also marks every command as not requiring cheats: operators can run `/give` and similar
commands with `allow-cheats=false`, the world keeps achievements enabled, and `level.dat` records no cheat or command
history. See `SECURITY.md` and the `operators` doctor check.

### 0.11.13 / 1.26.52.3 compatibility gate — 2026-10-09

Candidate run 37835410128 (branch `feat/endstone-0.11.13`) built and upstream-tested the patched Endstone on Linux and
Windows and passed the consumer E2E for NetherNet and RakNet on both platforms. On the Windows candidate bundle:

- Fresh setup acquired BDS `1.26.52.3` through Endstone (`version.txt` `26.52`), and a Bot joined over NetherNet and
  over RakNet.
- Against real BDS over RakNet, a wrong owner key, a wrong issuer, a wrong audience, and a valid token with
  `[local-bot-auth] enabled = false` (after a real BDS restart) each sent Login, were kicked, never spawned, and
  produced no accepted-local-bot entry. Replay, expiry, and `cpk` binding remain covered by the patch's unit tests.
- An official vanilla `1.26.52.3` server (no `version.txt`) with a world was adopted with `setup --existing`: the plan
  backed up 2885 files with a SHA-256 manifest, re-acquired the same BDS through Endstone, kept the world and
  settings, and doctor passed the world and version checks.
- On that migrated server an Xbox-authenticated human joined alongside the Bot, bound as controller, and ran
  `/bot ping` and `/bot Alice tp me`. As an operator they then ran `/give` and `/effect`, saw no
  achievements-disabled notice, and unlocked the previously locked vanilla Xbox achievement **Rabbit Season** in that
  session. After a clean stop, every `level.dat` history flag was still false with `GameType=0`.

That unlock shows actual Xbox achievement unlocks work on this pair. Because operator cheat commands were used in the
same session, it is also direct evidence that upstream operator commands do not disable achievements; it is not
evidence of a cheat-free session.

### Previous baseline (`v0.11.12` / BDS `1.26.51.1`)

Endstone `v0.11.12` at `1c71186cba896c5e0bc432384a8a8e72dfb2a626`, with BDS `1.26.51.1` build `51061372` and protocol
`2193`, was the baseline for Endbot 0.1.0 through 0.2.0.

Upstream `v0.11.12` keeps the same BDS `1.26.51.1` / protocol `2193` pair as `v0.11.11`. It stops narrowing
NetherNet to a single UDP port, fixes cross-dimension Actor/Player teleport, adds `network.stun-servers`, and fixes
further map/scoreboard regressions. The upstream Brigadier-style command tree work is still an open PR and is not
part of this baseline.

The modified wheel uses the separate package version `0.11.12+endbot.1`. Preparation creates that exact local tag on
the disposable patched commit for `setuptools_scm`; the plugin requires the same exact version. This prevents pip from
substituting official unpatched `0.11.12` while leaving the upstream compatibility baseline unambiguous.
`endstone.lock` `support_status: "validated-baseline"` refers to the pinned upstream Endstone/BDS pair above, not to
human validation of the exact patched package; exact patched-package human validation is tracked below and in the
compatibility manifest.

The following live evidence was obtained against historical patched package `0.11.11+endbot.1` (same BDS pair,
older Endstone `v0.11.11`), not the current `0.11.12+endbot.1` artifact. The local-auth behavior was live-tested with ten concurrent external bots while online mode remained
enabled, cheats and commands remained disabled, experiments/packs were absent, and creative/experiment history stayed
zero. M0 was subsequently completed at Endbot revision `f509ac4e8677d9bc870b341f001b8e42f512df97`: a normally
Microsoft/Xbox-authenticated client executed `/bot ping`, received `Endbot: pong`, and unlocked a previously locked
vanilla Xbox achievement in the same survival world. Post-session world-history fields remained zero. This evidence
does not prove a future Endstone/BDS pair.

M2 runtime behavior was live-smoked on Linux against the same pinned pair, followed on 2026-09-19 by the authenticated
Windows Bedrock command/achievement gate. The human session covered representative M2 lifecycle, input, and dimension
teleport behavior and ended with a new locked vanilla Survival Xbox achievement unlock. Later Linux remediation smoke
separately verified command roots, same-dimension fall physics, hotbar selection, BDS-authoritative block placement and
inventory decrement, and item drop. See [`M2_VALIDATION.md`](M2_VALIDATION.md) for the evidence boundary. At the time of
those `0.11.11+endbot.1` observations, native Windows hosting of the Endbot runtime was designed for but not
claimed as validated; the Windows game client was the human operator in that gate. These M2 observations were also
against `0.11.11+endbot.1`; see [`M2_VALIDATION.md`](M2_VALIDATION.md).

Current status after PR #4: native Windows hosting has been live-smoked successfully (Windows host, patched Endstone
`0.11.11+endbot.2`, official Windows BDS `1.26.51.1` build `51061361`, protocol `2193`, Endbot plugin/runtime, Alice
local Bot with `iss = endbot://local-bot` and `aud = endstone://local-bot` via `owner-public.pem`). Alice stayed
online while a normal Microsoft/Xbox human client joined with a populated XUID, with no human `Accepted local bot`
entry, no Kelp/Timeout/stutter, and normal Survival with no achievement-disabled warning. Linux nevertheless remains
the CI/release-artifact baseline: the Windows smoke does not give Windows identical CI/release coverage. The PR #4
smoke added no new Xbox-achievement observation; the then-current `+endbot.2` manifest observation remained false.

Current patched package `0.11.12+endbot.1` is the `v0.11.12` rebase and has NOT yet received
a new human/Xbox achievement observation. A new exact package does not inherit historical observations; see the
compatibility manifest and the release gate below.

### Local-bot auth defaults migration

Old defaults:

```text
iss: ownerbot://local
aud: endstone://local-ownerbot
Endstone default public key filename: ownerbot-public.pem
```

New defaults:

```text
iss: endbot://local-bot
aud: endstone://local-bot
Endstone default public key filename: owner-public.pem
```

Runtime and Endstone issuer/audience must match exactly; update both sides together. No legacy alias was added.
Explicit custom values remain supported if both sides use the same values. Owner-key terminology and
`owner-private.pem` / `owner-public.pem` are intentional: the key pair remains the server-owner-controlled trust root.

## Compatibility change gate

A proposed Endstone/BDS update is not supported merely because it compiles. It must pass:

1. exact lock resolution and clean patch application;
2. patched Endstone build and upstream tests;
3. Endbot plugin, runtime, auth-policy, portability, and preflight tests;
4. real BDS normal-player Microsoft login;
5. valid local-bot login plus wrong-key/signature/expiry/issuer/`cpk`, disabled-mode, and replay negative controls;
6. concurrent-bot smoke testing and reconnect behavior appropriate to that release;
7. configuration and version-aware world-history inspection;
8. the manual `/bot ping` and actual Xbox achievement observation in `ACHIEVEMENTS.md`.

Only after reviewed evidence should `endstone.lock`, the compatibility manifest, and support documentation change.
Linux remains the CI/release-artifact baseline. The runtime uses Node and Python filesystem/socket APIs that are portable to
Windows and avoids POSIX-only runtime requirements. Native Windows hosting has additionally been live-smoked
successfully after PR #4 (see above), but that smoke does not give Linux and Windows identical CI/release coverage.

## Release contents

`.github/workflows/release-candidate.yml` exists and assembles a release candidate on manual dispatch: it clean-builds
the patched Endstone tree, runs its upstream tests, builds and inspects the Linux Endstone wheel, builds the Endbot
plugin wheel, packages the Endbot runtime and example configuration, generates the compatibility manifest and
SHA-256 checksums as currently implemented, and uploads the `dist/` candidate as a workflow artifact. It does not
publish a production release automatically. It must not package the official BDS binary. `scripts/generate_compatibility_manifest.py` binds metadata to the current Endbot Git revision
and SHA-256 of the ordered patch series. The Linux Endstone artifact is repaired with pinned upstream Endstone's
configured cibuildwheel manylinux image and `auditwheel` repair command, then inspected and installed with the plugin in
a clean consumer image that has no LLVM runtime. Raw build-host wheels remain inside cibuildwheel's disposable container
and are never uploaded. No workflow publishes a production release.
