# M2 validation record

This record separates the recorded human achievement gate from autonomous Linux smoke and later remediation. All runs
used official BDS `1.26.51.1` build `51061372`, protocol `2193`, and patched Endstone `0.11.11+endbot.1`. No server,
world, identity, key, token, diagnostic capture, or machine-local path is retained in the repository.

## Recorded Windows-client gate — 2026-09-19 JST

Confirmed in the recorded human session:

- An unmodified Windows Bedrock client joined normally through Microsoft/Xbox authentication. The human identity was
  explicitly allowlisted by UUID/XUID and was not made an operator or granted vanilla command permissions.
- The world remained Survival with `online-mode=true`, `allow-cheats=false`, experiments disabled, and no required
  behavior/resource packs.
- Representative M2 control and lifecycle commands were exercised, including accountless Bot operation and a
  cross-dimension teleport through Endstone's API rather than vanilla `/tp`.
- The signed-in human unlocked a previously locked vanilla Survival Xbox achievement after those M2 operations. The
  in-game achievement notification was captured on video.

This completes the M2 achievement-compatibility gate for this exact pinned pair. It does not establish compatibility
for another Endstone/BDS pair or claim that every parser variant and primitive appeared in the recording.

## Additional observations outside the recording

The earlier 2026-09-18 Linux smoke established persistent UUID reuse, ten concurrent profiles, independently targeted
movement/actions, server-observed movement and attack, held-item use/release, bounded reconnect, lifecycle isolation,
rename/forget semantics, BDS-native position/inventory persistence, and cross-dimension teleport.

Post-session remediation smoke on 2026-09-19 separately established:

- `/bot`, `/bot ping`, `/bot list`, `/bot help`, `/bot help advanced`, and named default-mode commands were accepted by
  the real Bedrock command parser after the overload declarations were corrected.
- A grounded Bot teleported within the same dimension to unsupported air fell from Y 75 to the terrain at Y 63,
  landed, and could jump afterward.
- Hotbar query/set used Bedrock's equipment path and reported human-facing slots 1–9.
- `drop` and `drop stack` changed the selected BDS inventory through normal inventory transactions.
- `interact` stayed connected and performed legal Survival block placement. BDS decremented a four-cobblestone stack
  to two after two legal placements; rejected/illegal clicks did not decrement it.
- The interaction used a protocol-2193 start action, packed server-authoritative item-interaction tick, and next-tick
  stop action. The plugin never edited a block directly.

## Defects discovered and remediation status

- **Same-dimension airborne teleport suspension — fixed.** The runtime now acknowledges the server correction, seeds
  downward motion when unsupported, and yields to authoritative landing updates.
- **Top-level command rejection — fixed.** The pinned Endstone/BDS parser mishandles optional enum overloads. Required
  typed overloads plus narrow string fallbacks preserve `/bot <name> ...` and make zero/default forms parse.
- **Autocomplete gaps — fixed where supported.** Finite choices use native enums. Bot profile names remain strings
  because the pinned Endstone public API has no supported dynamic-completion surface; `/bot list` is the discovery path.
- **Missing hotbar, block interaction, and selected-item drop — fixed.** Inventory/world authority remains in BDS.
- **Disconnected async command sender lifetime — fixed.** Workers retain only a player UUID and re-resolve it on the
  server thread, so a diagnostic client disconnect cannot leave a stale native sender wrapper.
- **Server-wide Low-ping to High-ping/stutter observation — unattributed.** It is tracked as an orchestration A/B
  question, not asserted to be an Endbot defect. Machine-local supervisor evidence is intentionally not committed.

Block breaking/mining remains out of scope. Its future implementation must follow the server-authoritative
`PlayerAuthInput` block-action/prediction path rather than mutate blocks through the plugin.

## Persistence and safety boundary

BDS restored the same UUID's dimension, position, rotation, and inventory across reconnect/resume. Endbot therefore
persists identity, name, and desired lifecycle only; it does not maintain a competing location or inventory database.

The live server reported Survival and an empty pack stack. Scoped preflight passed `online-mode=true` and
`allow-cheats=false`; no experiment, Beta API, GameTest, operator grant, vanilla cheat command, or direct world-edit
shortcut was introduced. The human Xbox unlock is the achievement observation. Repository automation still does not
pretend to be a version-independent `level.dat` proof.

Linux/WSL is the validated runtime/server host. A Windows Bedrock client is validated as the normal human operator;
native Windows hosting of the Endbot runtime remains designed for but has not been claimed as tested.
