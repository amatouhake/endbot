# Endbot backlog

Open work and user-reported issues are tracked here. Implementation, automated checks, and real-server validation are
recorded separately; an implementation does not close the remaining manual gates.

## BOT-001: Reduce and configure the Bot loading range

Status: **Implemented — broader live validation pending** (updated 2026-10-16 JST).

Reduce the default chunk loading range requested by Bots and expose it in configuration so server operators can adjust
it for their workload.

Acceptance criteria:

- Choose and document a smaller default, including the unit, supported bounds, and behavior when the setting is omitted.
- Expose the setting in the runtime configuration, example configuration, and operator documentation; reject invalid
  values clearly.
- Verify that the configured range is requested on initial connection, reconnect, and dimension changes, subject to
  BDS limits.
- Validate loading behavior and normal Bot movement/actions on a real server, including multiple concurrent Bots.

Implemented: `[runtime].chunk-radius` in `endbot.toml`, carried into runtime JSON as `chunkRadius`; default 5 chunks,
accepted integer range 2..32. The upstream implicit default was 10. Initial connection, spawn, reconnect, and dimension
change requests use the configured value. BDS raised a live request for 3 chunks to 5, so the default matches that
observed effective range. Config validation, packet regression tests, and two-Bot connectivity smoke pass; sustained
multi-Bot load and chunk-loading effects remain to be measured. See [validation](BOT_BEHAVIOR_VALIDATION.md).

Per-Bot adjustment: `/bot <name> radius <2-32>` persists an override and applies it live. `radius` queries the requested
and server-confirmed values; `radius default` removes the override. Profiles without the optional field keep inheriting
the shared default. Rename/reconnect/restart preserve an override. Stationary and exploring Bots can therefore request
different ranges; BDS limits and server simulation distance remain independent.

## BOT-002: Restore physics, movement, and position synchronization after dimension changes

Status: **Fix implemented — partial real-server validation; manual gates open** (updated 2026-10-16 JST).

After a Bot crosses dimensions, the following symptoms have been reported:

- Knockback has no effect.
- Gravity has no effect.
- Different players see the Bot at different positions.
- The Bot does not jump.
- The Bot does not walk.

Track these symptoms together initially; investigation must establish whether they share a cause. Record the exact
Endstone/BDS pair, transport, transition method, and minimal reproduction when reproducing the issue. Compare behavior
before and after the transition, including a return trip, with BDS-authoritative position and observations from at least
two normally authenticated player clients.

Acceptance criteria:

- After a dimension change, the Bot responds to knockback, falls and lands under gravity, and can jump and walk.
- Observing players agree on the Bot's position and dimension, consistent with BDS-authoritative state.
- Exercise repeated transitions among Overworld, Nether, and End, covering both Endbot cross-dimension teleport and
  portal travel where applicable, on each transport for which a fix is claimed.
- Check same-dimension teleport and reconnect for regressions; add regression tests for the identified failure and
  record real-server validation separately.

Historical cross-dimension teleport checks in [M2 validation](M2_VALIDATION.md) and
[compatibility records](COMPATIBILITY.md) do not establish that these post-transition behaviors work. The documented
same-dimension airborne teleport fix is a separate case.

Implemented: dimension/loading acknowledgements, new-dimension prediction reset, stale target/interaction cleanup, and
server-motion prediction for knockback. Regression tests cover repeated transitions, gravity, walking, jumping, and
motion packets. A Windows RakNet live run confirmed falling and walking after transitions through all three dimensions,
followed by a jump after returning. Portal travel, live knockback, and agreement between two authenticated human clients
still require validation. See [validation](BOT_BEHAVIOR_VALIDATION.md).

## BOT-003: Fix held item use (right-click hold), including tridents and food

Status: **Fix implemented — food/trident live smoke passed; broader item matrix open** (updated 2026-10-16 JST).

Bots reportedly cannot perform item actions that require holding right-click, including trident use and eating food.
The intended `use continuous` / `use stop` interface exists, but its presence and the historical held-item use/release
smoke do not establish that these item-specific actions complete successfully.

Acceptance criteria:

- Reproduce with food and a trident, recording the exact Endstone/BDS pair, transport, selected item, inventory sync
  state, and command sequence. Check both before and after dimension changes rather than assume a connection to BOT-002.
- With hunger and food that permit eating, held use completes consumption and BDS updates the food count and hunger.
- With a throwable trident, held use charges it and release throws it through normal BDS item-use behavior.
- `use stop` and global `stop` release held use correctly; hotbar changes and reconnect do not leave stale use state.
- Distinguish held use from repeated short uses, check `once` and `interval` for regressions, and test a bow as another
  charge/release case. Add regression tests for the identified failure and record real-server results separately.

Investigation should distinguish missing or stale held-item information from use-start, hold, release, and completion
handling. A relationship to the separate known pickup inventory-sync limitation has not been established.

Implemented: air-use transactions packed into `PlayerAuthInput`, initial press versus held simulation ticks, explicit
release, release before hotbar changes, and cleanup when BDS consumes/replaces the selected item. A Windows RakNet live
run observed golden-apple consumption and a thrown-trident actor after release. Food with a hunger prerequisite, bow
use, and item use after dimension changes remain open. The separate world-pickup cache limitation is not fixed here.
See [validation](BOT_BEHAVIOR_VALIDATION.md).

## BOT-004: Change Bot control authorization while the server is running

Status: **Implemented — real human-client visibility and grant/revoke gate remains** (2026-10-16 JST).

`endbot controllers add|remove|list` submits the console-only `botcontrollers` command. The plugin persists grants,
pending authenticated enrollment, retained identity bindings and revocations in a separate atomic policy file. Online
permissions and command visibility are refreshed, and queued commands recheck authorization/revisions before execution.
Revocation overrides bootstrap bindings and legacy allowlists. Offline regrant preserves a known XUID, including after
revocation/restart. `reset` remains a stopped-only bootstrap binding reset; editing TOML does not hot-reload access.

Acceptance criteria:

- Define who may manage controllers separately from ordinary Bot operation; the server console is the existing trusted
  administration surface. Ordinary controllers must not gain the ability to grant themselves administrative rights.
- Apply grants/revocations to connected players and refresh their command visibility. Recheck authorization for queued
  commands so revocation cannot leave already-queued control operations authorized indefinitely.
- Preserve normal Microsoft/Xbox authentication and XUID-based binding, including renamed GamerTags and pending offline
  enrollment. Keep local Bots unable to enroll or grant control.
- Persist changes consistently with the operator configuration/bindings, avoid competing CLI/plugin file writers, and
  verify behavior after rejoin and restart. Specify how legacy UUID/XUID allowlists interact with revocation.
- Test unauthorized management, immediate revocation, failed writes, and live grant/revoke without granting operator or
  vanilla command permissions. Record real-client command visibility checks separately.

Automated tests cover these policy/queue paths with synthetic identities. The pinned Windows Endstone/BDS RakNet smoke
passed console registration, pending addition/removal, refusal to promote a local Bot, restart persistence, negative
authentication and world-history inspection. An actual authenticated human's live command visibility, execution after
grant/revocation, and reconnect still require manual E2E validation; no Xbox achievement gate is inferred.
See [operations](OPERATIONS.md#runtime-grants-and-revocations) and [validation](BOT_BEHAVIOR_VALIDATION.md).

CLI management now waits for correlated execution results and reports failures with a nonzero exit code. Effective
authorization/reasons are available through `controllers list`, saved-state `doctor`, and plugin-backed `doctor --live`.
Bot movement/action/look/stop/lifecycle replies identify the requested operation rather than only `online` state.

## Upstream update candidates — checked 2026-10-16 JST

Endbot is still locked to Endstone `0.11.13+endbot.1`. Upstream
[0.11.14](https://github.com/EndstoneMC/endstone/releases/tag/v0.11.14), released 2026-10-12, is a candidate for compatibility
evaluation: it improves packet/compression, chunk and terrain generation, multi-player performance, and several Windows
CPU/status issues. It also drops Python 3.10 and changes fresh-server view-distance/client-generation defaults. These
are upstream changes; Endbot has not adopted or validated this release and no support claim or lock is changed here.

The [Brigadier-style command tree API PR #509](https://github.com/EndstoneMC/endstone/pull/509) remains open and draft,
targeting 0.13. Its typed branches, soft-enum arguments, and permission-based branch visibility are relevant to Endbot's
completion redesign, but they are not available merely by upgrading to stable 0.11.14. Endbot would need to adopt the API
and validate real-client completion after it becomes available.

## BOT-005: Block breaking and mining

Status: **Implemented — Windows RakNet and NetherNet Survival matrix passed; broader gates open** (2026-10-16 JST).

`/bot <name> destroy` selects a block in the Bot's line of sight; explicit coordinates/optional face select a reachable,
unobstructed target. `destroy continuous` continuously selects targets along the same sight ray, waits when empty, and resumes
when a reachable block appears; `destroy stop` cancels both mining and waiting. Packed player block actions let BDS perform normal mining; no world edit,
instant-break transaction, OP, or authentication/core change is used. Status tracks the server's target updates.

Real-server checks covered bare-hand dirt/stone, iron-pickaxe stone with cobblestone drop and durability loss, three
cancellation paths, native event cancellation preserving the block/tool, consecutive targets and waiting/resume/stop.
The same automated matrix (with radius, controller management and abnormal-end notices) also passed over NetherNet, the
default transport. Pathfinding, automatic tool selection and area excavation are not included. A human client over
NetherNet and broader effects/enchantment/tool-break combinations remain validation work. See [mining validation](MINING_VALIDATION.md).

## Implementation boundary

Implementation must preserve BDS world-state authority and the repository safety invariants. Ordinary behavior fixes
belong in the Endbot plugin/runtime; keep the Endstone core delta limited to local bot authentication. World achievement
history and actual Xbox achievement unlocks remain explicit manual/E2E gates.
