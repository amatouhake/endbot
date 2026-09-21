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
- **Autocomplete gaps — fixed where supported (historical assessment at the time).** Finite choices used native enums. Bot
  profile names remained strings
  because the pinned Endstone public API had no supported dynamic-completion surface; `/bot list` was the discovery path.

  > Current status after the later command-compatibility work: execution is complete, but native client completion
  > remains degraded on the pinned Endstone 0.11 API. See `COMMANDS.md`.
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

Linux/WSL was the validated runtime/server host at the time of the recorded gates above. A Windows Bedrock client was
validated as the normal human operator; native Windows hosting of the Endbot runtime remained designed for but had not
been claimed as tested at that point.

## Native-Windows host smoke (post-PR-#4 current status)

After PR #4, native Windows hosting was live-smoked successfully: Windows host, patched Endstone `0.11.11+endbot.2`,
official Windows BDS `1.26.51.1` build `51061361`, protocol `2193`, Endbot plugin/runtime, Alice local Bot
(`iss = endbot://local-bot`, `aud = endstone://local-bot`, `owner-public.pem`). Alice stayed online while a normal
Microsoft/Xbox human client joined with a populated XUID, with no human `Accepted local bot` entry, no
Kelp/Timeout/stutter, and normal Survival with no achievement-disabled warning. This smoke did not add a new
Xbox-achievement observation; the current `+endbot.2` manifest observation remains false. Linux remains the
CI/release-artifact baseline, so this smoke does not claim identical Linux/Windows CI/release coverage.

## Native-Windows runtime-correctness matrix (M2 primitives, 2026-09-21/22 JST)

Fresh native-Windows session on the same pinned pair (patched Endstone `0.11.11+endbot.2`, official Windows BDS
`1.26.51.1`, protocol `2193`, fresh vanilla Survival world, `online-mode=true`, `allow-cheats=false`, fresh runtime
secrets on current local-bot defaults). One human client (normal Microsoft/Xbox login, single-XUID allowlist)
established the control baseline first: normal human movement/jumping throughout, no short/stuttery human-jump
anomaly in this session. One Bot (`Alice`), then a temporary fresh-UUID Bot for the drop matrix. Each primitive was
observed live from the human client, one at a time.

- `jump` / `jump once` — initially FAIL (repeated bunny-hop from one command), PASS after fix. The runtime held the
  cooked `jumping` flag for the whole predicted airborne phase, so BDS re-jumped on every landing; a consumed
  `once` action left `jump stop` nothing to clear. Fix: only the trigger tick presses (`cb1074d`), with session-level
  regression tests. Live retest: exactly one settled jump for both forms; height judged a normal single jump by eye
  (not measured).
- `jump continuous` → `jump stop` — PASS after the same fix (continuous holds by re-triggering every tick; stop
  deletes the action).
- `jump continuous` → global `stop` — PASS (continuous jumping ceases, bot stays online).
- `sneak on` → `sneak off` — state semantics PASS after two fixes (`10683a7` press edge, `0dd3a0c` held
  `sneak_down` + raw current): sustained visible crouch, slower movement while sneaking, clean release. Cooked
  `_down` flags are held state, not edges; the server diffs consecutive ticks. Packet-level multi-tick regression
  tests pin the sequence.
- Sneak edge avoidance (not walking off a ledge while sneaking) — FAIL, investigated and DEFERRED as
  client-movement-simulation scope, not an input defect. The runtime predictor integrates motion with no
  collision/world awareness and BDS (lenient authority: `server-authoritative-movement-strict=false`, acceptance
  threshold 0.5) accepts the positions; no `PlayerAuthInput` mechanism requests edge-stop. Reproducing vanilla
  edge protection would require Endbot-side collision/edge simulation, deliberately not built here.
- Hotbar selection + `drop` — PASS **when the selected inventory state is known to the runtime** (fresh-UUID bot
  plus login full-sync, slots identified visually in-hand): exactly one selected item ejected; slot independence
  confirmed. Same-session world pickups alone do not qualify: see the limitation below.
- Hotbar selection + `drop stack` — PASS under the same known-state condition: remaining stack ejected; hand
  emptied live with no reselect.
- Drop-sync fix (`8cc405e`, session-level regression tests): BDS applies our drop silently and sends no inventory
  deltas for pickups (proven under full packet trace: zero slot/content/equipment/stack-response/actor signals to
  the picker), so the session now predicts its own drop mutations exactly like the queued transaction and
  re-announces the held item.
- World pickup → same-session runtime inventory synchronization: KNOWN LIMITATION, deferred, confirmed end-to-end
  after the fix. One controlled item: BDS inventory gains it, runtime cache stays stale, explicit hotbar selection
  can render it without populating the cache, and `drop` fails closed with `Selected hotbar slot is empty`; a real
  Bot reconnect repopulates via login full-sync, after which `drop` and `drop stack` work normally. A bounded
  check of the pinned 1.26.50 protocol (246 packets) found no vanilla client→server inventory-resync request:
  `container_open`, `item_stack_request`, and `block_pick_request` are not sync requests, and provoking an
  error-correction is not a normal mechanism. Pickup awareness without server deltas would require entity-magnet
  simulation and stays deferred. Reconnect is the documented recovery path; `resume` on an already-online bot is
  a no-op and is not a resync mechanism.

Sprint/move directions were exercised but not carefully observed; they remain unvalidated (not implied PASS). No
new Xbox-achievement observation was made or claimed in this session.
