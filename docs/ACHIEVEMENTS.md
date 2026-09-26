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

## Completed 0.1.0 release gate

On 2026-09-27 JST, the exact draft `v0.1.0` artifacts were exercised by the operator. These artifacts were built once
from `main` `b51fc3c4e3ff1220a6c8b598d3ea0d6946155b33` by release-candidate run `36189148021`, and the same bytes
were then published without a rebuild.

| Artifact | SHA-256 |
| --- | --- |
| `endbot-0.1.0-windows-x86_64.zip` | `8e50ff30d6b63fc8c60a2f591e310c11f2b3e31e2c808cc56955b24126e28e22` |
| `endbot-0.1.0-linux-x86_64.tar.gz` | `e74c67cbfcf90e84e4a0536e8130fabe6c6d1975aa009ae98383a486038bbecb` |

The full per-asset list is the release's `SHA256SUMS`. It is covered by a build-provenance attestation, like both
bundles.

Pinned pair: patched Endstone `0.11.12+endbot.1` on official BDS `1.26.51.1` (protocol `2193`), obtained through
Endstone.

### What the operator did on the Windows bundle

1. Ran `endbot setup --fresh --controller <GamerTag> --apply` and then `endbot start`, using only the operator commands.
   No Python or Node.js was installed manually.
2. Joined from a normal Windows Bedrock client through Microsoft/Xbox authentication. No manual `allowlist add` was
   needed.
3. The GamerTag bound to the XUID on this first join. `doctor` recorded the binding as `2026-09-26T17:35:10Z` (UTC).
4. Exercised `/bot` from the client: `spawn`, `move`, `jump`, `look`, `look at`, `tp me`, `tp x y z`, nether `tp`,
   `reconnect`, `rename`, and `despawn`.
5. In the same session, unlocked the still-locked vanilla Survival Xbox achievement **Archer** ("Kill a Creeper with Bow
   and Arrows", 10 Gamerscore). The in-game notification was recorded on video. The Xbox achievement view shows it
   completed on 09/27/26.
6. Afterwards, briefly exercised `bot ...` from the server console.

The [unlisted YouTube recording](https://youtu.be/l8YEslnEBjY) is a convenience copy of that session.

### Post-session `endbot doctor`

- Every check passed: `online-mode=true`, `allow-cheats=false`, the level.dat creative/experiment history, and GameType
  0 (Survival).
- Local-bot auth uses a P-384 owner key pair.
- The controller is bound.
- `allow-list=true` with the controller listed.
- The installed Endstone and BDS versions match the lock.

### Checked by Claude on the same artifacts

- SHA256SUMS and the attestations verify.
- Negative local-bot auth on the Windows bundle against a real BDS: an owner-signed Bot is accepted. A wrong owner key,
  a wrong issuer, a wrong audience, and local-bot-auth disabled are each refused.
- The CI clean-consumer E2E, with update and rollback, passed for both platform bundles.

### Not exercised in this gate

The following were not run on the 0.1.0 artifacts:

- A second Xbox account.
- A second LAN device.
- Joining from outside the LAN.
- A human session on Linux; the Linux bundle is covered by CI E2E only.

Evidence for the allow-list behaviour comes from the rc.3 operator pre-check. It showed a non-allowlisted human being
rejected, then joining after being added, while Bots join regardless of the list. That a non-controller cannot use
`/bot` is covered by tests.

The build-time compatibility manifest inside the artifacts keeps its observation flags `false` by design; this section
is the observation record. No player identity, world, key, token, or machine-local path is committed.
