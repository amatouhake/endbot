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
zero. That migration evidence does not prove a future pair and does not replace the outstanding Xbox unlock gate.

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
Linux is the only CI build target in M1; Windows is not claimed as validated.

## Release contents

A future release workflow may package the modified Endstone artifact, Endbot plugin wheel, Endbot runtime, example
configuration, checksums, generated compatibility manifest, Endbot license, and third-party notices. It must not package
the official BDS binary. `scripts/generate_compatibility_manifest.py` binds metadata to the current Endbot Git revision
and SHA-256 of the ordered patch series; no workflow in M1 publishes a production release.
