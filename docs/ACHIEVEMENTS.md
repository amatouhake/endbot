# Achievement safety and M0 validation

## What is and is not established

Two claims must remain separate:

- **Achievement-compatible configuration/state:** `online-mode=true`, `allow-cheats=false`, no experiments/Beta
  APIs/GameTest, no required packs, and no recorded creative/experiment history. This configuration and zeroed history
  were preserved in the validated baseline with ten concurrent external bots.
- **Observed Xbox achievement unlock:** a signed-in Bedrock client actually unlocks a previously unearned Xbox
  achievement in the test world. The recorded M0 observation was completed against patched Endstone
  `0.11.11+endbot.1`; the current `0.11.12+endbot.1` artifact has no new achievement observation.

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

## Completed M0 record

On 2026-09-18 JST, Endbot revision `f509ac4e8677d9bc870b341f001b8e42f512df97` was tested with patched Endstone
`0.11.11+endbot.1` on official BDS `1.26.51.1` build `51061372`, protocol `2193`.

- A Windows Bedrock client joined through normal Microsoft/Xbox authentication with the explicitly allowlisted XUID.
  Server logs contained the populated Xbox identity and no local-ownerbot acceptance entry.
- `/bot` appeared in client slash-command completion and `/bot ping` returned exactly `Endbot: pong` without granting
  operator or vanilla command permissions.
- The world settings UI showed no achievement-disabled warning. In the same world/session, the previously locked
  vanilla Xbox achievement `「毛刈り日和」` unlocked successfully.
- A post-session save still reported survival game type, commands disabled, no creative history, no experiment history,
  no locked behavior/resource packs, and no required texture packs. The server pack stack was empty.
- An [unlisted YouTube recording](https://youtu.be/QfCVA7I9Y1w) provides a convenient view of `/bot ping` returning
  `Endbot: pong` and the achievement unlocking in the same session. The retained source recording is the integrity
  reference: 96,830,900 bytes, 76.966016 seconds, created 2026-09-18 20:11:35 JST, SHA-256
  `9b9790db2835674f96505f1b7353f85c03cf26c1d2ade50fff8d13a569c94925`. The upload is a transcoded convenience copy,
  not a replacement for that source digest.
- No video, player identity, world, private key, token, credential, or machine-local path is committed as evidence.

This establishes the M0 result for the exact baseline above. It does not establish compatibility for a later
Endstone/BDS pair, prove every Xbox achievement, or replace the repeatable release gate for future compatibility bumps.

## Completed M2 gate

On 2026-09-19 JST, the same Survival world and pinned BDS/Endstone pair were exercised from a normal Windows Bedrock
client after representative M2 lifecycle, input, and dimension-teleport operations. The human joined through the
Microsoft/Xbox path, remained non-operator, and unlocked a still-locked vanilla Survival Xbox achievement; its in-game
notification was captured on video. `online-mode=true`, `allow-cheats=false`, no experiments, and the empty pack stack
were preserved. This completes the M2 achievement-compatibility gate for this exact pinned pair.

The session also exposed ordinary correctness defects, later repaired without changing the authentication patch or
safety settings. Post-remediation Linux smoke is recorded separately in [`M2_VALIDATION.md`](M2_VALIDATION.md); it does
not replace or retroactively alter the recorded human achievement observation. Future revisions and compatibility-pair
updates still require the explicit release gate rather than inheriting this result automatically.
