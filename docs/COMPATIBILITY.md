# Compatibility and releases

## Current baseline

The only pinned baseline is Endstone `v0.11.11` at
`37b395378d91d6d20f1c52bf9d79dbd20e152458`, with BDS `1.26.51.1` build `51061372` and protocol `2193`.
`endstone.lock` is the machine-readable authority.

The modified wheel uses the separate package version `0.11.11+endbot.2`. Preparation creates that exact local tag on
the disposable patched commit for `setuptools_scm`; the plugin requires the same exact version. This prevents pip from
substituting official unpatched `0.11.11` while leaving the upstream compatibility baseline unambiguous.
`endstone.lock` `support_status: "validated-baseline"` refers to the pinned upstream Endstone/BDS pair above, not to
human validation of the exact patched package; exact patched-package human validation is tracked below and in the
compatibility manifest.

The following live evidence was obtained against historical patched package `0.11.11+endbot.1` (same upstream pair),
not the current `+endbot.2` artifact. The local-auth behavior was live-tested with ten concurrent external bots while online mode remained
enabled, cheats and commands remained disabled, experiments/packs were absent, and creative/experiment history stayed
zero. M0 was subsequently completed at Endbot revision `f509ac4e8677d9bc870b341f001b8e42f512df97`: a normally
Microsoft/Xbox-authenticated client executed `/bot ping`, received `Endbot: pong`, and unlocked a previously locked
vanilla Xbox achievement in the same survival world. Post-session world-history fields remained zero. This evidence
does not prove a future Endstone/BDS pair.

M2 runtime behavior was live-smoked on Linux against the same pinned pair, followed on 2026-09-19 by the authenticated
Windows Bedrock command/achievement gate. The human session covered representative M2 lifecycle, input, and dimension
teleport behavior and ended with a new locked vanilla Survival Xbox achievement unlock. Later Linux remediation smoke
separately verified command roots, same-dimension fall physics, hotbar selection, BDS-authoritative block placement and
inventory decrement, and item drop. See [`M2_VALIDATION.md`](M2_VALIDATION.md) for the evidence boundary. Native Windows
hosting of the Endbot runtime is designed for but is not claimed as validated; the Windows game client was the human
operator in this gate. These M2 observations were also against `0.11.11+endbot.1`; see [`M2_VALIDATION.md`](M2_VALIDATION.md).

Current patched package `0.11.11+endbot.2` is this terminology/default-identifier migration and has NOT yet received
a new human/Xbox achievement observation.

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
Linux is the validated CI/build/runtime target. M2 uses Node and Python filesystem/socket APIs that are portable to
Windows, avoids POSIX-only runtime requirements, and preserves Windows as a supported design target, but native Windows
M2 operation is not yet claimed as validated.

## Release contents

A future release workflow may package the modified Endstone artifact, Endbot plugin wheel, Endbot runtime, example
configuration, checksums, generated compatibility manifest, Endbot license, and third-party notices. It must not package
the official BDS binary. `scripts/generate_compatibility_manifest.py` binds metadata to the current Endbot Git revision
and SHA-256 of the ordered patch series. The Linux Endstone artifact is repaired with pinned upstream Endstone's
configured cibuildwheel manylinux image and `auditwheel` repair command, then inspected and installed with the plugin in
a clean consumer image that has no LLVM runtime. Raw build-host wheels remain inside cibuildwheel's disposable container
and are never uploaded. No workflow in M1 publishes a production release.
