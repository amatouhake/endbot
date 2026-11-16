# Achievement safety and M0 validation

## What is and is not established

Two claims must remain separate:

- **Achievement-compatible configuration/state:** `online-mode=true`, `allow-cheats=false`, no experiments/Beta
  APIs/GameTest, no required packs, and no recorded creative/experiment history. This configuration and zeroed history
  were preserved in the validated baseline with ten concurrent external bots.
- **Observed Xbox achievement unlock:** a signed-in Bedrock client actually unlocks a previously unearned Xbox
  achievement in the test world. The recorded M0 observation was completed against patched Endstone
  `0.11.11+endbot.1`. On 2026-10-09 the `0.11.13+endbot.1` candidate (BDS `1.26.52.3`) observed an unlock of
  **Rabbit Season** in a session that also used operator `/give` (`COMPATIBILITY.md`).

`scripts/preflight.py` robustly checks only the two relevant `server.properties` values. Endbot deliberately does not ship a
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
4. For a command-only probe, keep `[local-bot-auth].enabled = false`. For an integrated M0/M1 probe with local auth
   enabled, record its exact configuration and prove that the human login used the original Microsoft/Xbox path rather
   than the local identity path.
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

Until steps 7–10 are completed and reviewed for a baseline, report `/bot ping` as automatically registered/unit-tested
but manually unvalidated under the full achievement requirement.

## Recorded observations

Each observation belongs to the exact patched package it was made on. A later package or Endstone/BDS pair does not
inherit it, and each compatibility change needs the gate again.

| Date | Patched Endstone / BDS | Observation |
| --- | --- | --- |
| 2026-09-18 (M0) | `0.11.11+endbot.1` / `1.26.51.1` | `/bot ping` returned `Endbot: pong` and a locked vanilla achievement unlocked in the same session, with no operator or cheat permissions |
| 2026-09-19 (M2) | `0.11.11+endbot.1` / `1.26.51.1` | An unlock after lifecycle, input and dimension-teleport operations |
| 2026-09-27 (0.1.0) | `0.11.12+endbot.1` / `1.26.51.1` | **Archer**, on the exact release files, after driving Bots with `/bot` |
| 2026-10-09 | `0.11.13+endbot.1` / `1.26.52.3` | **Rabbit Season**, in a session that also used operator `/give` and `/effect` |
| 2026-10-30 | `0.11.15+endbot.1` / `1.26.60.6` | **Body Guard**, on an instance updated from 0.4.0 |

After each session `level.dat` still showed Survival and no creative or experiment history. The full records, with the
recording digests and what each gate did not exercise, are kept [as they stood at 0.7.0](https://github.com/amatouhake/endbot/blob/a02eaf7c63dbeba08c5e6c7ca638a8f3eab70926/docs/ACHIEVEMENTS.md);
the current pair's gate is in [COMPATIBILITY.md](COMPATIBILITY.md#current-baseline-050-to-070).
