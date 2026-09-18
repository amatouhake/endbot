# Achievement safety and M0 validation

## What is and is not established

Two claims must remain separate:

- **Achievement-compatible configuration/state:** `online-mode=true`, `allow-cheats=false`, no experiments/Beta
  APIs/GameTest, no required packs, and no recorded creative/experiment history. This configuration and zeroed history
  were preserved in the validated baseline with ten concurrent external bots.
- **Observed Xbox achievement unlock:** a signed-in Bedrock client actually unlocks a previously unearned Xbox
  achievement in the test world. This has not yet been completed as an Endbot release gate.

`scripts/preflight.py` robustly checks only the two relevant `server.properties` values. M1 deliberately does not ship a
partial `level.dat` parser: format/version ambiguity would turn unknown state into a false safety claim. Without the
explicit `--acknowledge-world-state-unverified` scope flag, the script exits incomplete.

## M0 manual `/bot ping` procedure

Use an expendable fresh survival world and a Microsoft/Xbox account for which the chosen simple achievement is still
locked. Record the Endbot revision and the generated compatibility manifest.

1. Prepare and build the exact patched Endstone revision documented in the README. Install its Python package and the
   `endstone-endbot` wheel into one clean virtual environment. Do not substitute an unpinned Endstone package.
2. Let Endstone obtain the official pinned BDS through its normal bootstrap. Do not add a BDS binary to the Endbot
   checkout or release bundle.
3. Before the validation world is created, set `online-mode=true` and `allow-cheats=false` in `server.properties`.
   Leave experiments, Beta APIs, GameTest, behavior packs, and resource packs unused.
4. Keep `[local-bot-auth].enabled = false` for the command-only M0 probe. This isolates slash-command behavior from bot
   login behavior.
5. Start once with the plugin installed so it writes `plugins/endbot/config.toml`, then stop cleanly. Add the real
   tester's UUID or decimal XUID to exactly one `[authorization]` allowlist. Do not make the tester an operator and do
   not grant vanilla command permissions. Restart the server.
6. Run `python3 scripts/preflight.py <server-dir>/server.properties --acknowledge-world-state-unverified` and save its
   output with the test record.
7. Join using an unmodified Bedrock client through normal Microsoft/Xbox authentication. Confirm the client is in
   survival mode and the world UI does not warn that achievements are unavailable.
8. Enter `/bot ping`. Expected chat response: `Endbot: pong`. Confirm an unlisted normal player cannot run it. Confirm
   `allow-cheats` remains false and no operator/vanilla command permission was granted.
9. With a version-aware world inspection tool, verify and record the world creative-history and experiment-history
   fields. If the tool cannot decode this BDS version confidently, record the result as unknown and stop the gate.
10. Complete the prerequisite for the selected still-locked Xbox achievement. Record the in-client notification and
    Xbox profile/account result, including client/BDS versions and timestamps. A command response or green CI is not a
    substitute for this observation.

Until steps 7–10 are completed and reviewed, report `/bot ping` as automatically registered/unit-tested but manually
unvalidated under the full achievement requirement.

