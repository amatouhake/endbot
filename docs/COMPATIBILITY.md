# Compatibility and releases

## Current baseline

The only pinned baseline is Endstone `v0.11.12` at
`1c71186cba896c5e0bc432384a8a8e72dfb2a626`, with BDS `1.26.51.1` build `51061372` and protocol `2193`.
`endstone.lock` is the machine-readable authority.

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
