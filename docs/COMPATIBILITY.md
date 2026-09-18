# Compatibility and releases

## Current baseline

The only pinned baseline is Endstone `v0.11.11` at
`37b395378d91d6d20f1c52bf9d79dbd20e152458`, with BDS `1.26.51.1` build `51061372` and protocol `2193`.
`endstone.lock` is the machine-readable authority.

The modified wheel uses the separate package version `0.11.11+endbot.1`. Preparation creates that exact local tag on
the disposable patched commit for `setuptools_scm`; the plugin requires the same exact version. This prevents pip from
substituting official unpatched `0.11.11` while leaving the upstream compatibility baseline unambiguous.

The local-auth behavior was live-tested at this baseline with ten concurrent external bots while online mode remained
enabled, cheats and commands remained disabled, experiments/packs were absent, and creative/experiment history stayed
zero. M0 was subsequently completed at Endbot revision `f509ac4e8677d9bc870b341f001b8e42f512df97`: a normally
Microsoft/Xbox-authenticated client executed `/bot ping`, received `Endbot: pong`, and unlocked a previously locked
vanilla Xbox achievement in the same survival world. Post-session world-history fields remained zero. This evidence
does not prove a future Endstone/BDS pair.

M2 runtime behavior was live-smoked on Linux against the same pinned pair. The record covers ten simultaneous profiles,
independent input, server-observed movement and attack, held-item use accepted by BDS, server-API teleport including a
dimension change, same-UUID reconnect/resume/native persistence, lifecycle isolation, and rename/forget semantics. See
[`M2_VALIDATION.md`](M2_VALIDATION.md) for the evidence boundary. Native Windows M2 operation, the full M2 command UX
from an authenticated human client, post-M2 world-history inspection, and a new Xbox achievement observation remain
manual gates; the completed M0 result is not silently generalized to M2.

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
