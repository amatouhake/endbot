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

Status: **Held use and timed release implemented — broader item/manual gates open** (updated 2026-10-16 JST).

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
run observed golden-apple consumption and a thrown-trident actor after release. Food with a hunger prerequisite and
item use after dimension changes remain open; timed bow use is covered by the follow-up below. The separate
world-pickup cache limitation is addressed by BOT-007.
See [validation](BOT_BEHAVIOR_VALIDATION.md).

Human Windows-client follow-up (2026-10-16 JST): charging with `use continuous` and releasing with `use stop` throws
the trident, but its held-item image can remain until hotbar reselection; track that unresolved rendering issue under
BOT-007. The two-command charge/release sequence now has a one-command alternative: `use for <ticks>` holds for 1–72000
input ticks and releases. The form exposes `for` and a ticks field. Stop releases early (and can fire a charged item),
hotbar selection releases/cancels timed use, and death/dimension change/disconnect discard it. Existing modes remain.
The human Windows RakNet check on `29e782a` confirmed `for` through both commands and forms, a successful trident throw,
and clearing of the held trident image. See [item-use/sync validation](ITEM_USE_SYNC_VALIDATION.md) for scope and gates.

## BOT-004: Change Bot control authorization while the server is running

Status: **Implemented — human Windows client (RakNet) confirmed live revoke/regrant and command visibility**
(2026-10-16 JST; see [mining validation](MINING_VALIDATION.md#human-windows-client-check-2026-10-16-jst)).

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

Count limit (2026-10-17 JST): `destroy [continuous] count <blocks>` stops continuous mining after that many
BDS-completed targets. The runtime counts them on its target-update path, so waiting between blocks costs nothing and
the count is exact under load, unlike the wall-clock `for` budget; `for` and `count` combine and mining ends at
which comes first. `status` shows the blocks broken (and the limit when set), keeps the total of a finished loop,
and the end reports `block count reached` at info level. The count is "the watched target changed", so another
actor's change to that block counts too; exact Bot break ownership would need plugin-side `BlockBreakEvent`
counting and remains separate work. Real Windows RakNet and NetherNet runs passed the count end
and the `for`/`count` combination, and `scripts/mining_probe.py` now carries those cases (see
[mining validation](MINING_VALIDATION.md#block-count-limits-re-validation-2026-10-17-jst-raknet-and-nethernet)); a
human client remains a gate.

Real-server checks covered bare-hand dirt/stone, iron-pickaxe stone with cobblestone drop and durability loss, three
cancellation paths, native event cancellation preserving the block/tool, consecutive targets and waiting/resume/stop.
The same automated matrix (with radius, controller management and abnormal-end notices) also passed over NetherNet, the
default transport. Pathfinding, automatic tool selection and area excavation are not included. A human client over
NetherNet and broader effects/enchantment/tool-break combinations remain validation work. See [mining validation](MINING_VALIDATION.md).

## BOT-006: Make the control forms compact and readable

Status: **Implemented — revised layout awaits human-client rendering confirmation** (2026-10-16 JST).

The Bot list shows online Bots by name, with connecting/failed Bots prefixed by a dark-colored `JOIN`, `RETRY` or
`FAIL` and offline Bots behind one `Offline (n)...` button. The online Bot screen has Stop everything plus Movement,
Look, Teleport, Gameplay, Inventory and Bot, named after Minecraft's control settings so inventory features can grow
under Inventory. `/bot` now opens the list for players, while console `/bot` retains help; the explicit `/bot menu` and
`/bot <name> menu` paths remain. No resource pack is introduced. Automated response, navigation, authorization and
command-path tests pass; 1080p/default-scale rendering still needs a human check.

The colored `/bot help` and replies and the `/bot menu` forms were checked on the Windows client (1.26.52) over
RakNet on main `061bcc8`. Colors render as intended and the forms work, but three usability problems were reported:

- **Bot list hides the connection state.** The list button is `"<name>\n[<state>]"`, but the client shows only the
  first line, truncated as `RakNetTest...`, so online/offline is not visible. Put the state on the same line (for
  example `RakNetTest · online`, state colored) and keep it short enough not to be truncated.
- **Buttons are large, so the Bot screen needs a lot of scrolling.** Button size is fixed by the client. Reduce the
  number of buttons per screen instead. Options: (A) about six buttons on the Bot screen — Stop, Move/Look...,
  Action... (jump/attack/use/destroy and mode chosen in the input form), Teleport..., Settings... (hotbar, radius,
  sprint/sneak), Connection... (reconnect, despawn, resume); or (B) category buttons that each open a sub-screen of
  actions (one more tap per action). Implemented with category sub-screens and direct Stop on the main screen.
- **The form window itself is small.** Standard server forms cannot set their window size; the client UI decides it.
  Player-side GUI scale enlarges everything. A server-sent resource pack with custom form layouts could restyle them,
  but it adds a distribution path and must be checked manually against achievement eligibility; evaluate separately.

Acceptance criteria: the list shows each Bot's state without truncation; common actions on the Bot screen need little
or no scrolling on a 1080p Windows client at default GUI scale; behavior stays identical to the typed commands (forms
still submit the same `/bot` arguments); confirmed on a normally authenticated human client.

## BOT-007: Synchronize picked-up hotbar items and held-item rendering

Status: **Equipment update fix and configurable sync implemented — Windows RakNet trident rendering confirmed**
(2026-10-16 JST).

Previously, a Bot could visibly hold a newly picked-up item while its runtime cache still considered the slot empty;
`drop` then failed, and reconnect was needed. The older own-drop count/rendering fix did not address this pickup gap.

The plugin now reads the nine BDS hotbar slots at a configurable interval (four ticks by default) and publishes changed
bounded observations plus periodic unchanged corrections (20 ticks by default)
over each local Bot's existing connection. The runtime uses the server's item palette and preserves typed item NBT,
updates its transient cache and re-announces changed held equipment. Native BDS transactions still perform drops/use;
no item is inserted, reassigned or removed by synchronization. Endstone core patches and the lock are unchanged.

Acceptance: naturally picked-up blocks, food, merged stacks and named/enchanted/damaged tools can be selected and
dropped one/stack without reconnect; the hand clears after emptying; rejected drops converge back to BDS state; malformed
or wrong-identity observations do not overwrite good state. BDS inventory/loot and human-client rendering are distinct
checks. See [inventory validation](INVENTORY_VALIDATION.md) for tested scope and remaining gates.

Human Windows-client follow-up (2026-10-16 JST): after a trident is thrown, the observer can see both the projectile
and a trident still in the Bot's hand. Reselecting with `hotbar 2` or another slot clears the stale hand image. This
supports a missing equipment announcement; actual inventory duplication has not been established. An in-memory event
replay also emptied the runtime's selected slot and held item without sending an empty-hand equipment announcement.
The suspected path is a native inventory update changing the cache before the periodic snapshot: the snapshot then
sees no difference and skips its equipment announcement. At report time this was a hypothesis for the live symptom;
the implementation follow-up is below.

Implemented follow-up: native selected-slot updates and periodic observations share an equipment announcer that
compares against the last announcement, separately from the inventory cache. Regressions cover native-empty updates
followed by identical snapshots, counts, nonselected slots and explicit reselection. Configurable read/resend intervals,
change-only sending between corrections and cached block palette lookups reduce avoidable work. The human Windows
RakNet check on `29e782a` confirmed the thrown trident disappeared from the hand. Other item/transport rendering,
tool breakage and multi-Bot load remain gates. See [the new validation record](ITEM_USE_SYNC_VALIDATION.md)
and [the original report](INVENTORY_VALIDATION.md#human-windows-client-trident-follow-up-2026-10-16-jst).

## BOT-008: Experiment with a selected-Bot command context

Status: **Experiment closed — parked, not merged** (2026-10-16 JST). The work stays on the local
`feat/bot-selection` branch; `main` has no selected-Bot syntax, and every command still names its Bot.

The experiment added a separate `/botctl` root (to avoid Bot-name collisions with `/bot`) with player-scoped
`select <name>`, `select`, `deselect`, `move`, `jump` and `stop`:

```text
/botctl select Alice
/botctl move forward
/botctl jump
/botctl stop
```

Execution worked in automated tests, the live console battery and a human Windows Client: selection was bound to the
Bot UUID and the authenticated player, rechecked authorization per action and cleared on logout/revocation. Completion
was not satisfying, and the remaining limits are outside what the plugin can fix on Endstone `0.11.13`:

- A generic string/message overload swallows typed suggestions. Removing it and intercepting the exact root through
  public `PlayerCommandEvent` / `ServerCommandEvent` before native dispatch restored typed suggestions.
- The Windows Client advertises only the last overload among those sharing a first token. `jump` had to be a single
  `(once|continuous|stop|interval)` overload, so no `<ticks>` hint can follow `interval` alone.
- Bot names cannot be suggested without a runtime-updatable (soft) enum, which needs an upstream API rather than a
  core patch or private registry access.

Selection alone saves typing the name, but the menu already provides a practical repeated-control surface. Revisit
only when an upstream command-tree/soft-enum API (see the PR #509 note above) is available in a locked, validated pair;
reuse the event-intercept approach and the one-overload-per-first-token finding then.

## BOT-009: Controller-authored Luau scripts and a small in-game editor

Status: **Design direction agreed — not implemented; release assignment undecided** (2026-10-21 JST).

Controllers should be able to write and run their own Bot scripts without server-filesystem access. Use **Luau** as
the language baseline. Required use cases include loops, conditions, coordinate arithmetic, and placing multiple Bots
at offsets from a chosen origin before starting their actions. A plain command-list format is insufficient for these
use cases; keep expression evaluation and control flow in Luau rather than adding them to `/bot` command strings.

Design order: settle Group targeting and bulk-operation semantics (BOT-010) before the Luau operation API. Groups
remain a separate feature available when scripting is disabled. Luau remains at the design stage; BOT-010 now has a
command-first source prototype. Neither feature has a release assignment.

Initial authoring surface:

- Provide an in-game script list, creation, saving, syntax checking, execution and stopping.
- Prototype a small source editor: display numbered lines and allow selecting a line to edit, insert or delete.
  Offer ordinary Luau examples/templates, including placing a group in a row, which controllers can copy and modify.
- Support writing the same Luau source in an external editor. Copy/paste import is an initial candidate, subject to
  actual client/form behavior; automatic synchronization and a dedicated editor extension are deferred. The delivery
  path for a controller's externally edited source remains to be designed, without requiring server-file access.
- Keep script source distinct from run arguments such as the target group, origin, spacing and action limits, so
  controllers can reuse a script without rewriting coordinates or Bot names. Exact APIs and argument forms are open.

Runner and feature availability:

- Prefer a dedicated Luau runner process, separate from the existing Node.js Bedrock runtime. The concrete runner,
  process granularity and IPC are still subject to prototyping. Plan to bundle the runner so controllers do not need
  to install Luau separately.
- Make scripting an optional, operator-configured feature with an explicit on/off switch. Proposed initial behavior:
  read the setting at startup and apply changes on restart; default off is a proposal, not a settled default. Exact
  configuration naming and any future live-toggle behavior remain open.
- When disabled, do not start the runner, hide scripting menu entries, and reject direct scripting requests at the
  service boundary. Ordinary `/bot` operations remain available under their existing authorization. Preserve saved
  scripts when disabling the feature so they can be used after re-enabling it.
- Feature enablement does not grant controller access by itself; existing controller authorization and per-action
  checks still apply. Runner failure should fail/report the affected script runs without taking down Bot transport or
  ordinary control; define cleanup of inputs started by those runs. Process separation alone is not a sandbox.

Agreed script-run lifetime:

- Keep a script run active while its continuing Bot inputs/jobs are active; scripts should wait for work they need to
  keep running. Normal completion, explicit stop and failure stop the continuing inputs started by that run. Cleanup
  must not cancel a later operation issued by another controller. Completed placement and world changes remain; this
  is input cleanup, not rollback. A detached/background job API is not part of this initial model.
- A run continues when its initiating controller logs out. Permission revocation and server shutdown end the run and
  clean up its continuing inputs. Server restart does not automatically resume or replay script runs. Authority remains
  bound to the authenticated controller and its grant revision after logout.

Implementation decisions still open:

- Measure editing usability with roughly 10–20 lines on actual Bedrock clients, including multiline paste, text limits,
  closing/canceling forms, draft retention and error-line display. Multiline editing/paste support is not established.
- Reuse the Group membership/order and immutable Bot UUID model in BOT-010. The chosen command member selector is
  one-based, matching normal Luau sequence indexing; earlier zero-based selector sketches are superseded.
- Bind execution to the authenticated controller and recheck effective authorization after waits and before actions,
  consistent with the existing grant-revision model. Define source ownership/sharing separately from execution rights;
  scripts must not acquire console privileges or controller-administration access.
- Specify accepted versus completed operations, sequential versus parallel actions, connection/completion waits,
  timeouts and partial failures. Define run/job identifiers, stale-request rejection and bounded cleanup on runner loss
  to enforce the agreed lifetime. Bot disconnect/reconnect behavior and manual-operation conflict policy remain open.
- Select the concrete runner, isolation/resource limits and plugin/runtime bridge after a small prototype. The existing
  Node.js runtime is retained for Bedrock protocol/client behavior. No
  runtime rewrite, additional Endstone core behavior patch or new compatibility claim is implied.

Web editing remains a future extension to reconsider when the in-game/external-editor workflow demonstrates a need;
it is not part of the initial scripting scope. Reuse the same source and operation model if it is added. A reference is
[endstone-live-map](https://github.com/wingxia/endstone-live-map#readme): its documented local Node map service has
optional Cloudflare R2 tile mirroring/Worker delivery. That is a deployment reference, not a scripting-editor or
controller-authentication implementation to adopt. No Cloudflare service is required by this design.

This records the agreed scripting direction only. Scripts, a Luau API and editing/import behavior are not yet available;
the independent Group command prototype is tracked under BOT-010. Implementation and real-client editing, permission,
cancellation, Bot behavior and achievement validation must be recorded separately; no release version or completion is
promised here.

## BOT-010: Define Groups before the Luau operation API

Status: **Command-first prototype implemented — client/menu and broader live validation pending**
(2026-10-21 JST).

Settle Groups as reusable Bot targets for existing commands and menus before designing the scripting API that consumes
them. Group operations must also work with scripting disabled; iteration, coordinate calculations and orchestration
remain Luau concerns. This is a design dependency, not a promise that Group implementation must be released before
every scripting experiment.

Agreed basic behavior:

- Store membership by immutable Bot UUID so Bot rename preserves membership; a Bot may belong to multiple groups.
- Preserve registration order. Offline members retain their positions in the list and do not compact member indexes.
- Fix the member list when a bulk operation starts; later membership edits affect later operations.
- Continue applying an operation to other members when an individual member fails, and report per-Bot success/failure.
- Apply existing Bot operations through commands and equivalent menus. Coordinate calculations and multi-step
  orchestration belong in Luau; Groups do not require scripting to be enabled.

Shared scope and chosen command design:

- Groups are shared server-wide. Initially, all authorized controllers may create, edit, delete and use them. Creator
  information does not make a group private. Deleting a group removes its registration, not its Bots. Per-controller
  namespaces and finer group access rules are future extensions if needed.
- Keep `/bot <target> <operation>` for execution. A plain Bot name selects one Bot; `#<group>` selects the whole group;
  `#<group>[<index>]` selects one member using a one-based index. The `#` prefix distinguishes a group from a Bot name;
  offer one canonical prefix, without aliases, ranges or expression evaluation in the initial design.
- Use `/botgroup` for group management, with the proposed forms below. Commands and menus must resolve the same targets
  and operations. Per-Bot identity management such as rename/forget requires a single-Bot target rather than broadcast.

```text
/bot Alice stop
/bot #miners stop
/bot #miners destroy count 64
/bot #miners[1] tp me
/botgroup create miners
/botgroup add miners Alice
/botgroup members miners
/botgroup remove miners Alice
/botgroup delete miners
```

These are chosen design forms, now implemented in the command-first source prototype; see
[prototype instructions and limits](GROUP_PROTOTYPE.md). `destroy count 64` applies its limit separately to each Bot.
The locked BDS/Endstone console parser has been checked as described below. Actual client input and help/completion
paths still require validation. Current Bot-name validation excludes `#`; this alone does not establish client support
for the new target forms.

Windows RakNet console smoke (2026-10-21 JST, locked Endstone `0.11.13+endbot.1` / BDS `1.26.52.3`, build `51798905`):
the existing `string`/Id overload rejected `#`. A first-position `message` fallback accepted the full Group command
without a core patch. Create/add/members/delete, whole-group and one-based indexed actions, tailed commands, invalid
index diagnostics and offline-member partial failure passed. The fixture retained both Bot profiles after Group
deletion. Human-client command visibility/input/completion, Group menus and live mid-batch revocation remain open;
there was no authenticated-human Xbox achievement check. See the prototype document for reproducible scope and limits.

Questions to resolve first:

- Group naming rules, stable internal identity and persistence format, and group rename behavior.
- Behavior on Bot forget and group removal/re-addition, including whether those edits change later member indexes.
- The equivalent menu flow for bulk operations, individual members and management; avoid hidden selected-target state.
- Recheck controller authority before applying actions, and define the operation-specific result for an offline/missing
  member, including spawn versus actions that require an already connected Bot.
- Define what group teleport means, including shared versus offset positions, before assigning command syntax.
  Automatic formations and complete mining tasks are not assumed to be Group features.

The open questions are not settled defaults or available commands. The later Luau API should reuse the chosen
target-resolution/operation model; script-specific execution lifetime remains under BOT-009.

## Deferred priorities

- Inspecting a Bot's inventory and hotbar contents from commands and the menu, and a wider range of operations, are
  likely next wishes from the operator (noted 2026-10-16 after 0.3.0); not designed yet. Keep the menu rule: forms
  mirror commands and add no meaning of their own.
- `for <ticks>` duration and `use hold <ticks>` repeated charges shipped in 0.3.0; see
  [durations](COMMANDS.md#durations-for-and-charged-presses-hold). Real-server and human-client validation (bow/trident
  `hold` repeats, mining duration end, forms) remains a manual gate. The destroy block-count stop
  (`count <blocks>`, see BOT-005) is implemented; item-count conditions for other inputs remain separate from duration
  and not implemented. Live RakNet smoke (2026-10-16 JST, locked Endstone `0.11.13+endbot.1` / BDS
  `1.26.52.3`, `scripts/raknet_spike.py --mining --item-use`) passed through the native parser: `move forward for 20`
  moved 4.3 blocks then held still, `jump interval 10 for 30` expired, `use hold 25 interval 30 for 90` fired exactly
  three arrows, and `destroy for 40` aborted obsidian with the info notice.
  First human Windows Client check (2026-10-16 JST, RakNet, same locked pair): `jump interval 10 for 60` and
  `jump interval 20 for 60` each jumped three times. This is expected: a jump trigger while the Bot is still airborne
  (a jump lasts about 12 ticks) does not start a new jump, so interval 10 cannot jump more often than landing allows.
  The remaining command checks (bow/trident `hold`, `destroy for`, status) were not reached. The form check stopped
  when in-game night made the world hard to inspect, but the operator judged the added For/Hold fields and Move for
  form as visibly bolted on and hard to understand, so they were removed from that change.
- The redesigned action forms follow one rule agreed with the operator: menus make the command form approachable and
  carry no meaning of their own. The first screen offers the command's modes; a mode with parameters opens a form with
  only its clauses in ticks, blank optional clauses are omitted, and each run command is echoed in its typed form.
  Move is one `move <direction> [for]` form (quick taps stay typed). Sprint/sneak stays separate. See
  [control forms](COMMANDS.md#control-forms). Human Windows Client check (2026-10-16 JST, RakNet, locked pair):
  Move with for, Sprint/sneak, Jump interval/for, Use interval/hold/for (an invalid hold longer than interval was
  rejected with its reason), Use once hold and Destroy continuous for (with the duration notice) worked from the
  forms. The echo's leading arrow rendered as a missing glyph in the Bedrock chat font, so the echo is now plain
  ASCII `/bot ...` in gray; that change itself has not been rechecked on a client.
- Command completion redesign remains dependent on a suitable upstream Endstone API and is lower priority while the
  menu provides a practical control surface; revisit its need after menu feedback.
- Vanilla sneak edge protection remains deferred as client collision/edge simulation work; it is not part of the
  current form/inventory work.

## Implementation boundary

Implementation must preserve BDS world-state authority and the repository safety invariants. Ordinary behavior fixes
belong in the Endbot plugin/runtime; keep the Endstone core delta limited to local bot authentication. World achievement
history and actual Xbox achievement unlocks remain explicit manual/E2E gates.
