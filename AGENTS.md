# Endbot repository guidance

- Keep every tracked file usable from a standalone clone. Never depend on parent directories, personal absolute paths, or private research artifacts.
- Never commit credentials, private signing keys, live tokens, server identity pins, or player data.
- Preserve `online-mode=true`, `allow-cheats=false`, no experiments/Beta APIs/GameTest, and the normal Microsoft/Xbox authentication path.
- Keep the Endstone core delta narrowly limited to local bot authentication. Put ordinary product behavior in the Endbot plugin or runtime.
- The reproducible source of truth is `endstone.lock` plus `patches/endstone/`, not a persistent Endstone fork.
- Do not vendor or redistribute the official BDS binary. Use the upstream Endstone/BDS acquisition path.
- Treat world achievement history and actual Xbox achievement unlocks as explicit manual/E2E validation gates; never infer them from a green unit build.
- Do not claim a new Endstone/BDS pair is supported until it passes real compatibility validation and the lock/manifest are deliberately updated.

