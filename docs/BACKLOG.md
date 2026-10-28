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
[mining validation](MINING_VALIDATION.md#block-count-limits-re-validation-2026-10-17-jst-raknet-and-nethernet)).
Human Windows-client check (2026-10-17 JST, RakNet, on the 0.3.1 release bundle): `destroy count 3` and
`destroy count 20` ended on `block count reached`, `destroy for 40 count 100` ended on `duration elapsed`,
and the form-driven `destroy count` and the `count 0` range error behaved as expected.

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

Status: **Released in 0.4.0 behind `[scripting] enabled` (off by default) — human Windows-client gate passed on the
release files; a second-controller check remains open** (updated 2026-10-25 JST). Ownership, private/shared visibility, copy, a 10-revision history with
restore, the source view, line-by-line editing and book-and-quill import/export are
implemented (decisions below, #100–#101); the optional run/author permission split is not. Runner builds, bundle
self-tests, platform-matched OS confinement probes and the clean-consumer E2E on both platforms are required CI steps
(#97); the 2026-10-22 candidate run passed both platform jobs and all four consumer legs (source `e983358`), and only
its `assemble` job stopped, on the candidate version input, so no candidate artifact exists yet.
The 2026-10-25 Windows client check passed template creation and running, Copy and edit, revision history, rename,
sharing, book import/export, `runs`/`stop` (including the console) and runaway interruption. Two findings: a multi-line
paste is saved with its line breaks dropped, so the form is single-line and books carry multi-line authoring (see the
editing decision below); and a script with no parameters could not be started from the menu because its empty form
could not be submitted — it now starts the run directly. A second-controller check was not run. See
[scripting](SCRIPTING.md).

Controllers should be able to write and run their own Bot scripts without server-filesystem access. Use **Luau** as
the language baseline. Required use cases include loops, conditions, coordinate arithmetic, and placing multiple Bots
at offsets from a chosen origin before starting their actions. A plain command-list format is insufficient for these
use cases; keep expression evaluation and control flow in Luau rather than adding them to `/bot` command strings.

Design order: settle Group targeting and bulk-operation semantics (BOT-010) before the Luau operation API. Groups
remain a separate feature available when scripting is disabled. Groups (BOT-010) and inventory (BOT-011) are on `main`;
all three ship together in 0.4.0.

Decisions for the 0.4.0 MVP (2026-10-23 JST):

- **Minimal surface.** Server-stored scripts with list, view, run and stop; create/update by pasting source into a form
  text field; run arguments; a few templates (for example placing a group in a row). The numbered-line in-game editor
  is deferred and built only if pasting proves insufficient on real clients.
- **API, not command strings.** Scripts call typed functions covering every `/bot` operation, for a single Bot or a
  Group (`bot("Alice"):move("forward", {ticks = 20})`, `group("#miners"):destroy{...}`). There is no raw command-string
  escape hatch. The API is derived from one shared definition of operations and parameters that the `/bot` parser
  also uses, so new commands reach scripts without hand-written drift. Calls return acceptance results like commands;
  scripts wait explicitly (`wait(ticks)`, `waitOnline(bot)`). Authorization is rechecked before every operation.
- **Declared parameters, short invocation.** A script declares its parameters with types and defaults at the top
  (`params { group = param.group(), origin = param.pos("~ ~ ~"), spacing = param.number(2) }`). Commands pass them
  positionally and may omit defaulted ones (`/botscript run line #miners`); `~` is relative to the invoking player.
  The menu lists scripts and builds the input form from the declared parameters (group dropdown, position defaulting
  to the player, numbers with defaults), reachable from `/bot menu`. A short command alias is still to be decided.
- **Shared server-wide**, like Groups: every controller can list, run and edit; a run uses the invoker's authority.
- **Off by default**: `[scripting] enabled = false` in the plugin configuration, applied on restart.
- **Configurable limits**: concurrent runs per controller and server-wide, per-run instruction/time budget, operation
  rate and script size are configuration values with conservative defaults. Only limits that correctness depends on,
  such as packet bounds, stay fixed.
- **Runner selection by evidence.** Compare official Luau artifacts (prebuilt binaries, building the official source
  for the web/embedding at a pinned version, managed like `endstone.lock`) against community packages, by
  provenance, maintenance, license, embeddability (yielding for `wait` and host calls, interrupting runaway code),
  Windows/Linux bundling and sandboxing; then prototype the strongest option.
- **Runner chosen (2026-10-23):** a small C++ host process built from official Luau source at a pinned version and
  checksum (`runner/luau.lock.json`), started per run, talking NDJSON over stdio. It yields for `wait` and host calls,
  interrupts runaway code and removes io/os/require. Chosen over official CLI/web artifacts (no resumable host calls
  or interrupts) and community npm packages (forked or inconsistent provenance); see
  [the runner spike](LUAU_RUNNER_SPIKE.md). Linux build/bundling and OS-level resource limits are still to verify.
- **Later decisions (2026-10-23):** `params` is an ordered array (`param.group "group", param.pos("origin", "~ ~ ~")`)
  so positional arguments and forms follow declaration order. `wall-seconds` defaults to 0 (no total run time limit);
  runaway code is bounded by the per-slice budget, memory, operation rate and concurrency limits. `status()` returns
  structured data including the mining job state, and `waitMining{timeout}` (Bot or Group) waits only for mining jobs
  to end; a broader "idle" wait was deliberately not added.

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
- Select the concrete runner and plugin/runtime bridge after the prototype above. The existing
  Node.js runtime is retained for Bedrock protocol/client behavior. No
  runtime rewrite, additional Endstone core behavior patch or new compatibility claim is implied.

Decisions after the first human-client look (2026-10-24 JST):

- **Free authoring stays.** Every controller may write and run their own scripts; command-based control proved too
  limited. Finer permissions are acceptable if they protect this, but a template-only or operator-only source model is
  rejected. Scripting ships in 0.4.0.
- **Ownership instead of shared editing.** Shared editing let any controller break a working script by mistake.
  Each script records its creator's XUID as owner. Only the owner may edit, rename or delete it. Each script is
  `private` (owner runs it) or `shared` (every controller may run it, nobody else may edit). Others use **Copy and
  edit** to derive their own script. Each save keeps a bounded revision history (about ten) that the owner can
  restore from the menu. Runs keep the invoker's authority. Groups remain fully shared.
- **Optional permission split**: running (`endbot.script.run`) and authoring (`endbot.script.author`) may be granted
  separately; whether an administrator tier that can edit/stop others' scripts is needed is still open.
- **Editing in 0.4.0 stays paste-based.** The client's form text input has a fixed height of about two lines
  (seen 2026-10-24 on Windows). Add a read-only label above the input with the full source and line numbers;
  keep syntax-error line numbers. Neither Endstone 0.11.13 `TextInput` nor the form JSON offers a taller/multiline
  input; command-block UI needs operator/creative access and is not a server form, so it does not fit the
  `allow-cheats=false`, no-operator model. Book and quill is an unverified in-game multiline candidate.
  Implemented 2026-10-24 (#100): the Change dropdown offers replace all, append at end, replace lines, insert after
  line and delete lines, and `/botscript import|export` moves source through a held book and quill (export stays off
  unless `[scripting] book-export` is enabled). Which of these the real client makes comfortable remains a manual gate.
  **Correction (2026-10-25 Windows check):** the field does not keep pasted line breaks — a multi-line paste was saved
  as one line, so C2/C3 failed on the real client. The form now labels the limitation and remains for single-line edits
  and the line modes; multi-line authoring uses the book loop (create from template → Write into held book → edit →
  Replace from held book), and the game editor's own per-page paste limit is documented there. Paste-based multi-line
  editing is deferred until a control can hold line breaks (or the web editor, BOT-013).
- **Web editing** is the preferred long-term authoring experience; see BOT-013. An optional client resource pack with
  a multi-line form field is the in-game stopgap under evaluation; see BOT-018.

Decisions after the second human-client look (2026-10-24 JST):

- **Permissions stay as they are for 0.4.0.** Ownership/visibility, the invoker's authority and the console as
  operator are enough; the run/author split and an in-game administrator tier wait until a real need appears.
- **Editing:** the paste field starts empty and a Change dropdown offers replace all, append, and replace/insert/delete
  of numbered lines (the client's two-line field cannot select all, so a prefilled field could not be cleared).
- **Book and quill:** the client's book editor keeps line breaks and supports Ctrl+A/C/X/V on Windows (no visible
  selection). `/botscript import <name>` reads the held book; `/botscript export <name>` fills an empty held book and
  quill without creating an item, behind `[scripting] book-export` (default off; on since the 2026-10-25 review,
  once overwriting needed `overwrite` and a menu confirmation). One book is the size limit.
- **Syntax:** `name=value` run arguments after the positional ones, positions as Luau vectors accepted wherever an
  operation takes `x, y, z`, `param.choice`, `rename`, and templates using `assert(r.ok, r.reason)`.

Decisions after the 2026-10-25 Windows check (JST): the in-game path stays in 0.4.0 as the minimum (web editing,
BOT-013, is 0.5.0), but its menus were too hard to read and edit with.

- **Source view:** the script screen shows the whole source, with line numbers in gray so wrapped lines no longer
  blur into them; the separate paged Show source screen is gone (`/botscript show` stays for chat).
- **Fewer buttons:** Run, Stop runs, Edit lines, More (Rename, Share/Private, Earlier revisions, Copy and edit, Write
  into held book, Delete) and back. A template or copy asks only for a name.
- **Editing replaces the Change dropdown and paste field:** a list of line buttons; a line opens prefilled (a one-line
  field is easy to edit in place), with an optional line below that keeps the indentation and a delete toggle; lines
  can be added at the top and end. Saving returns to the list. Multi-line changes go through a book. Manual gate: the
  Windows client renders line buttons and the prefilled field comfortably, including on a 40-line page.
  Passed on the Windows client (2026-10-25): source view, menus, line editing, paging, new scripts, errors, Japanese.
- **Books act only on what the player saw:** the same check found that the menu's Replace from held book replaced
  the whole script in one tap, and that export required an empty book, so a book already used for a draft or an
  earlier export could not take a new export. Menu replace now shows the book's source and line counts and saves on
  Replace only if the book is unchanged; export into a book with writing shows its text and overwrites on Overwrite
  only if the pages are unchanged. As with Delete, a confirmation is the menu's addition to a command: Replace runs
  `import <name>` (which saves at once when typed; history keeps the old source) and Overwrite runs `export <name>
  overwrite` (plain `export` refuses a book with writing). Both confirmations, the typed `overwrite` and its
  command hint passed on the Windows client (2026-10-25).

Decisions on script files (2026-10-25 JST): writing source in a PC editor should not need the book loop, and
controllers still never name server paths.

- **The operator's folder is the source of truth.** `<name>.luau` files directly in the plugin data folder's
  `scripts/` are loaded at server start and again by `/botscript reload` (console or any controller); no file
  watching. Runs keep the source they started with.
- **File scripts are read-only in game** and shared with every controller: Run, Stop runs, Copy and edit and export
  work, editing does not (copying makes an ordinary stored script). They show as owned by `file` and have no history;
  the operator versions the folder.
- A stored script wins a name clash; the file is skipped with a warning. Only regular `.luau` files with a valid
  script name and within `max-script-bytes` load; symlinks are refused. `reload` also checks each file's declaration
  and reports problems; start-up only reads the files.
- Passed on the Windows client (2026-10-25): a file script listed as `file`, ran, refused edits, copied into an
  editable script, picked up a file change on reload, and reload reported a broken file and dropped it.

Decision on script logs (2026-10-25 JST): `log()` reached only the server console, so a controller saw nothing
of their own script. Log lines now also go to the chat of the player who started the run (not the script's owner),
tagged with the script name and run id, formatting codes removed, cut to 256 characters, at most 10 per 5 seconds per
run with the held-back count shown on the next line or the end message. The console keeps every line; a player who
is offline gets nothing later. `[scripting] chat-logs = false` turns chat delivery off.
The client check of this found two older faults: a script logging more than 8 lines at once ended "runner exited"
(the host failed on its full 8-event queue instead of waiting; it now waits, which also stalls the runner on its
pipe), and script menus echoed internal steps (`/botscript parameters hop`) or a run without its arguments. Menus now
echo only commands a player could type, including the run form's `name=value` arguments.
Rechecked on the Windows client (2026-10-25): a 15-line burst ran to the end with the held-back count, and the
menus echoed `/botscript run chatty` and `/botscript run hop name=Alice`. The same check found source previews
rendering `§` as formatting; previews now show it as `\u{a7}` (#106, rechecked).

The direction above is implemented as described in [scripting](SCRIPTING.md) and this item's status line; the
independent Group feature is tracked under BOT-010. Real-client editing, permission, cancellation, Bot behavior and
achievement validation are recorded separately and remain manual gates.

## BOT-010: Define Groups before the Luau operation API

Status: **Released in 0.4.0 (2026-10-25); its human-client gate passed — live mid-batch revocation remains a manual
gate** (2026-10-21 JST).

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
- Use `/botgroup` for group management, with the forms below. Commands and menus must resolve the same targets
  and operations. Per-Bot identity management such as rename/forget requires a single-Bot target rather than broadcast.
- Management references always use `#group`, including creation. `/botgroup list` shows groups and `/botgroup list #group`
  shows that group's members, supporting recall-and-append from command history. The former `members` subcommand is
  merged into `list`; bare management group names are rejected. Persisted bare names and membership UUIDs are unchanged.
  Keep operation-first management grammar; matching `/bot`'s target-first word order is not required.

```text
/bot Alice stop
/bot #miners stop
/bot #miners destroy count 64
/bot #miners[1] tp me
/botgroup list
/botgroup create #miners
/botgroup add #miners Alice
/botgroup list #miners
/botgroup remove #miners Alice
/botgroup delete #miners
```

These are chosen design forms, now implemented as the Group feature; see
[Group instructions and limits](GROUPS.md). `destroy count 64` applies its limit separately to each Bot.
The locked BDS/Endstone console parser and human-client target input have been checked as described below.
Completion/help usability remains unresolved; Bot-name validation excluding `#` alone was not parser evidence.

Windows RakNet console smoke (2026-10-21 JST, locked Endstone `0.11.13+endbot.1` / BDS `1.26.52.3`, build `51798905`):
the existing `string`/Id overload rejected `#`. A first-position `message` fallback accepted the full Group command
without a core patch. Create/add/members/delete, whole-group and one-based indexed actions, tailed commands, invalid
index diagnostics and offline-member partial failure passed. The fixture retained both Bot profiles after Group
deletion. This console run did not include an authenticated-human Xbox achievement check.

Human-client smoke (2026-10-21 JST, user report/screenshots, source `048e0aa`): the requested basic command, indexed
target, ordinary/indexed Bot-menu and offline-member checks behaved as expected. Alice/Bob membership order stayed
stable after Bob went offline; Alice's jump request succeeded while offline Bob's jump failed individually. Offline
`stop` can still succeed for a retained session; failure semantics depend on the operation. The user reported two issues
in that build: `/botgroup` advertised generic string/message parameters rather than helpful operation suggestions, and
whole-group `jump` visibly started sequentially. That initial implementation waited for each member's authorization/profile lookup
and operation before advancing.

Follow-up: whole-group immediate inputs (`move`, `look`, actions/flags, `stop`, `hotbar`, `drop`) now use one UUID-pinned
runtime batch, applied in one synchronous dispatch turn with per-member errors. A single profile snapshot and complete
preparation precede dispatch, with authorization rechecked immediately before submission. Revocation/shutdown during
preparation blocks the batch; once accepted it has no inter-member waits/callbacks, and later revocation does not undo
those inputs. Lifecycle/teleport/mining retain sequential paths and checks. This is not a same-BDS-tick guarantee.
`/botgroup` now includes one native enum hint per management operation with argument hints, matching `/bot`'s approach;
generic parseability branches and the baseline's dynamic-name/filtering limitations remain. The user confirmed that
completion was comparable to `/bot` and simultaneous execution worked on the real client (source `8a8f875`). The next
agreed revision unifies `list [#group]` and prefixed management references as above; recheck that revised syntax and
completion on the client. Earlier generic `members`/bare-name examples are superseded.
The user then confirmed all six revised management forms worked on the real client (source `905c924`). Menu coverage
and the result/log presentation of group-targeted `/bot` commands became the next design topics.

The user then agreed to and requested implementation of these rules:

- `/bot menu` lists Bots and direct Group operation targets; `/botgroup` or `/botgroup menu` lists/creates groups.
  `/bot #group menu` uses the shared Bot operation screen. `/botgroup menu #group` opens ordered member browsing
  and management, with a nested link to that operation screen. Members open the ordinary Bot menu.
  Group deletion asks for confirmation and retains Bot registrations. Forms run/echo the same typed commands.
- Every whole-group mutating-command acknowledgement and its normal audit entry is at most two lines, with accepted/failed/not-run
  counts plus up to three aggregated reason categories and an "other" count. No per-failure automatic line. Counts
  describe acceptance; a lost batch response has a separate unknown count. Logs include actor/target/operation in
  English; player summaries use their language. Read-only/menu/result requests do not add audit entries.
- `/botgroup result #group [history]` prints every member row for one operation: `1`/omitted selects latest, `2` the
  previous operation. The menu uses the same snapshots with older/newer navigation. Results are private per
  controller/group, capped at 20 per caller/group and 128 globally, evicting oldest first, and reset on restart. No replay.
- Old forms validate group/Bot UUIDs and membership order. History navigation pins result UUIDs so newly executed
  operations cannot shift a chosen destination. Recreating a group or reusing a Bot name must not retarget an old form.

These are implemented and covered by 221 plugin tests, including bounded 100-member failures and command/form routing
and stale-identity cases. The updated real-BDS probe passed summary/result commands and clean shutdown. Actual group
form rendering/input was subsequently reported as working, with two screenshots identifying blank deletion-confirmation
text and default Run buttons for membership edits. The revised UI names the group in deletion/add/remove forms,
uses Add/Remove labels and an action form for deletion. Bare `/botgroup` opens the menu. The user's chosen result-history
semantics replace member-row pagination; member-list browsing remains paginated. The user confirmed the revised
forms/history checks passed with source `f419241`. They next requested clearer separation of management and operations.
The shared-screen revision preserves management-first `/botgroup` menus and operation categories, adds direct Group
targets to `/bot`, and exposes missing command-backed controls including teleport, lifecycle, radius and interaction.
Back navigation preserves the entry context. Its 231 plugin tests pass; the shared-screen UI needs a client check.
Client feedback on source `278fe78` identified the oversized Group heading in `/bot` and requested the reverse link
from group operations to group management. The follow-up removes that heading and adds the specific-group link,
reusing the existing management-back button when appropriate. The link refreshes membership and pins the group UUID.
The user confirmed both checks passed for source `794882b`, with no issues in that scope. The subsequent agreed
revision distinguishes queries from mutation acknowledgements: `status`, argument-free `hotbar` and argument-free
`radius` display every member's actual data/error immediately, with index and name, and neither add audit entries nor
create/replace operation history. Setters retain the bounded summary/audit/history behavior. Its 236 plugin tests pass;
the user subsequently confirmed the latest query behavior worked with source `07f661e`.
Live mid-batch revocation remains a manual gate.
No actual Xbox achievement unlock was reported. See the
[Group evidence and follow-up](GROUPS.md) for scope and limits.

Remaining scope choices:

- Current names use 1-16 ASCII letters/digits/underscores, beginning with a letter/underscore. Groups have immutable
  UUIDs and atomic versioned persistence; group rename and creator metadata are deferred.
- Explicit removal compacts later indexes; re-addition appends. Offline/forgotten entries remain until removed.
  Reordering and stable numbered slots can be considered after use.
- Commands/menus use explicit targets and recheck authority. Offline/missing behavior follows each existing operation.
- Bulk teleport applies the single-Bot teleport independently, including through the shared operation menu.
  Offset formations, collision-safe placement and a frozen moving-player destination remain separate design work.

The later Luau API should reuse the chosen target-resolution/operation model; script-specific execution lifetime
remains under BOT-009. Automatic formations and complete mining tasks are not assumed to be Group features.

## BOT-011: Inspect and rearrange a Bot's inventory, and drop everything

Status: **Released in 0.4.0 — `inventory`, `hold` and `drop` (typed and forms) passed the human Windows-client gate on
the release files** (2026-10-25 JST).

`inventory`, `drop <slot>`, `drop <item> [count]`, `drop all`, `swap <a> <b>` and `hold <item>` are implemented. The
read-only observation now covers the full inventory, offhand and armor, and every change is sent as a legacy normal
`inventory_transaction` without stack IDs: the spike showed `item_stack_request` is unusable for Bots on the locked
pair (see [the spike](INVENTORY_STACK_SPIKE.md)). Live `raknet_spike.py --inventory` runs accepted every product case,
including the helmet and a 41-slot `drop all` (see [inventory validation](INVENTORY_VALIDATION.md)). Remaining gates:
human Windows-client forms, Japanese replies, held/armor rendering seen by others, group summaries and completion,
and NetherNet; they are planned for the combined 0.4.0 client check.

Before this item, a Bot could only select a hotbar slot and drop from the selected slot, and the runtime cached only the nine hotbar
slots. Emptying a Bot's inventory required killing it. The operator chose this scope ahead of Luau (BOT-009)
so that scripts can later wrap a stable set of inventory operations:

- **Inspect (read-only):** `/bot <target> inventory` shows the 36 main/hotbar slots, offhand and armor, with counts.
  The Inventory... form gets the same listing. A Group target is a query: every member's data is shown immediately
  with index and name, without audit entries or operation history, like `status`.
- **Hold an item:** `hold <item>` selects a hotbar slot containing the item, or moves it from the main inventory into
  the selected slot when the hotbar lacks it.
- **Swap slots:** `swap <from> <to>` exchanges two inventory slots. Slot numbering (one-based, hotbar first or main
  first) is an open design point; it must match the inventory listing.
- **Drop from anywhere:** `drop <slot>` and `drop <item> [count]` in addition to the existing selected-slot `drop`.
- **Drop everything:** `drop all` empties the Bot without killing it. The operator wants offhand and armor included,
  matching what a death would drop, if BDS accepts dropping from those slots; otherwise main/hotbar slots suffice.

Agreed conventions (2026-10-22 JST):

- Slots follow the game screen: hotbar `1`-`9`, main inventory `10`-`36`. Offhand and armor are shown and addressed
  by name (`offhand`, `head`, `chest`, `legs`, `feet`). The listing uses the same numbers and names.
- Items are named by their identifier without `minecraft:` (`diamond_pickaxe`), exact match only; no partial or
  display-name matching. When several slots match, the lowest slot number wins, so the hotbar is preferred.
- `drop all` runs immediately as a command; its form asks for confirmation first.
- Through a Group target, `inventory` is a query (each member's data shown immediately, no audit entry or history),
  while `hold`, `swap` and `drop` are mutations with the existing bounded summary, audit and history behavior.

Order of work (done): the `item_stack_request` spike first, then inspection; `hold`/`swap`/`drop` followed once the
spike showed which requests the locked BDS accepts (legacy transactions only).

Out of scope for this item: equipping/unequipping offhand and armor, containers (chests, furnaces), crafting, and
automatic refill or tool switching. Automation is to be decided with BOT-009.

Constraints and open questions:

- No item creation, assignment or removal by the plugin. Every change must be an ordinary inventory request that BDS
  validates; the plugin only observes. `online-mode=true`, `allow-cheats=false` and no experiments are unchanged.
- Observation grows from nine hotbar slots to the whole inventory, offhand and armor. Recheck the 60 KiB observation
  bound and the sync interval/cost (see BOT-007 and [inventory validation](INVENTORY_VALIDATION.md)).
- Moving items normally uses `item_stack_request` (server-authoritative inventory), while the existing drop sends a
  legacy `inventory_transaction`. Whether the locked BDS/protocol 2193 accepts the needed request actions is unverified;
  run a small real-BDS spike before committing to the command design.
- Mutations through a Group target follow the existing bounded summary/history rules; `inventory` is a query.
- The stale held-item rendering still tracked under BOT-007 touches the same path and may be addressed alongside.

Validation gates: unit/serializer regressions, a `scripts/raknet_spike.py --inventory` extension against real BDS
(actual inventory/loot changes, rejected requests converging to BDS state), and a human-client rendering check.
None of these may be inferred from a green unit build.

## BOT-012: Interact with entities — feeding, riding and dismounting

Status: **Released in 0.4.0 — `@` targets typed in chat and `interact @cow` passed the human Windows-client gate; boats/horses, other-client rendering and NetherNet remain open** (2026-10-25 JST). See [entities and vehicles](COMMANDS.md#entities-and-vehicles).

Operators could not put a Bot in a minecart or feed animals. Add entity interaction through the vanilla client path
(no server-side shortcuts), so achievements and BDS authority stay intact:

- **Track nearby entities** in the runtime from `add_entity`/`remove_entity`/movement packets, per Bot. A read-only
  `/bot <target> entities` lists nearby entities (type, runtime ID, distance); Group targets behave as a query like
  `status`.
- **Interact with an entity** using the held item: `/bot <target> interact <type|nearest|id>`. Feeding, breeding,
  shearing and name tags are the same operation.
- **Ride and dismount** minecarts, boats and horses; `dismount` leaves the vehicle.
- Commands, forms and the Luau API come from the shared operation definition, so scripts get the operations
  automatically (BOT-009).

Risks to settle in the spike: which packets BDS 1.26.52.3 expects for entity use (`inventory_transaction`
`item_use_on_entity` versus `interact`), how the Bot's predicted movement/`player_auth_input` must change while riding,
and the exact dismount packet. The spike covers feeding a cow (wheat), entering a minecart and dismounting on the locked
pair before product work.

## BOT-014: Read-only world and Bot queries for scripts

Status: **Released in 0.4.0 — Windows RakNet live check passed; `entities` passed the human Windows-client gate** (2026-10-25 JST).
Bot state, inventory/hotbar data and entity details (#86); look target, block lookup and exposed-block search.

Scripts could act but hardly observe: `status` carried no position, `inventory`/`hotbar` returned only text, and there
was no block information, so scripts such as "walk to the tree and cut it" could not be written. The operator chose to
add a broad set of read-only queries in 0.4.0, accepting the later release. Decisions (2026-10-24 JST):

- **Every query is also a `/bot` command** in the shared operation table, so scripts and chat stay one surface and
  queries can be tried in chat. Group targets print per-member results like `status`, with no history or audit.
- **Authoritative data**: Bot state, blocks and entity details come from BDS through the Endstone SDK on the server
  thread; hunger/saturation from the attributes BDS sends the Bot. Each query counts against the script rate limit.
- **No x-ray**: block search returns only blocks exposed to a passable neighbour (air, water, plants), as a player could
  see them. Looking up one named block position is not restricted.
- Scope: (A) `status` data with position, look, dimension, health, hunger, ground/water, selected and held item;
  `inventory`/`hotbar` slot data. (B) `target`: the block (position, face, type) or entity the Bot looks at, within
  reach. (C) `block <x> <y> <z>`: type and block states, `~` relative to the Bot. (D) `blocks <type> [radius] [limit]`:
  nearest exposed blocks, bounded radius and count. (E) entity details in `entities`: position, health, name tag.
- `use` stays air-use only; right-clicking what the Bot looks at stays an explicit `interact`.

## BOT-015: One target syntax for attack, destroy and interact

Status: **Released in 0.4.0 — `@` typed in Windows-client chat and `destroy oak_log count 3` on real terrain passed the human gate** (2026-10-25 JST).

The operator found that blocks could only be targeted by coordinates while entities had type/nearest/id selectors, and
that `interact` mixed blocks and entities. Decision (2026-10-24 JST, "plan B"): keep the separate left/right-click verbs
(`attack`/`destroy`, `use`/`interact`) and give them one target syntax: no argument is the crosshair target,
`<x> <y> <z> [face]` a block, a bare `<block type>` the nearest visible block of that type, and `@<type|id|nearest>` an
entity (vanilla's selector sign). The alternative of separate right-click verbs for blocks ("plan A") was rejected:
right-clicking a block opens, toggles, places or tills, so no verb name fits. Follow-up (2026-10-24 JST): `@` is
required for entities everywhere (no bare ids, also in Luau; listings print `@id` and data carries `selector`), and
targets combine with repetition: `attack`/`interact @… continuous|interval|for|count`, `destroy <type> continuous|for|count`.
See
[targets](COMMANDS.md#targets-crosshair-coordinates-block-types-and--entities).

## BOT-017: Cap Bot sessions so players can always join

Status: **Released in 0.4.0 — the human Windows-client gate saw a spawn refused for the reserved slot with a clear message** (2026-10-25 JST).

Bots are ordinary players, so a controller who spawns a Group or runs a script can fill `max-players` and lock every
human out until someone despawns Bots from the console. BDS has no reserved-slot setting, so Endbot keeps the reserve
itself.

Decisions (2026-10-25 JST):

- `[limits] reserved-player-slots` defaults to 1 and is subtracted from the server's `max-players`.
- `[limits] max-bots` defaults to 0 (no separate Bot cap); a positive value caps online or connecting Bots.
- `spawn`, `resume` and `reconnect` are checked; `despawn` and `forget` never are. Group and script calls fail per
  member with the current counts. Existing Bots are never despawned; the budget applies to new sessions only.
- Humans are the server's online players minus the profiles the runtime knows by UUID, so a server full of players
  also blocks new Bots.
- `endbot doctor` prints the resulting budget and warns when it cannot apply, or when both values are 0.
- `max-bots = 0` with `reserved-player-slots = 0` restores the previous unlimited behavior.

Manual gate: on a real client, spawn until the reserve refuses a Bot and confirm the message; a human must still be
able to join. Also confirm a large Group spawn reports refusals per member instead of failing the whole operation.

## BOT-016: Completion waits beyond mining

Status: **after 0.4.0 — not started** (2026-10-24 JST).

Scripts can wait for mining jobs (`waitMining`) and for Bots to come online, but a timed `move`, `use hold`, `attack`
or `interact` repetition, and a teleport's arrival, are only covered by guessing with `wait(ticks)`. Add waits that end
when those continuing inputs finish (or report why they stopped), with the same timeout, authority recheck and
Group/identity pinning as `waitMining`. This needs runtime-side completion state for each input channel.
Shape (agreed in the [interface guide](INTERFACE_GUIDE.md#d2-the-shape-of-waits-in-luau)): a continuing operation's
result carries a job, waited with `job:wait{timeout = ...}`; `waitMining` stays as an alias until 1.0.

## BOT-013: Web script editor

Status: **Superseded by the [product direction](DIRECTION.md)** (2026-10-28 JST).

The earlier localhost/LAN, plugin-served shape is replaced: the editor is part of the operator-deployed Cloudflare
Worker (BOT-021) and saves through endbotd with the same compile/declaration/ownership/revision path as in-game saves
(BOT-009). Highlighting, completion from `api.luau`, line-numbered errors and revision diffs remain the editor goals.

## BOT-018: Optional UI resource pack for forms and multi-line editing

Status: **Prototype on the branch, awaiting the manual client check** (2026-10-25 JST).

Question: can an optional resource pack make the menus easier to read and let script source be written over several
lines in a form, and how does that compare with the web editor (BOT-013)? Answer so far: yes, as a stopgap. A
client-side global pack can enlarge the form window, shrink buttons, and turn marked text inputs into the vanilla
multi-line, scrolling edit box that the book editor uses. It cannot add highlighting, completion or diffs, depends
on unofficial JSON UI internals, and does not reach consoles, so BOT-013 stays the long-term editor. Details,
findings and the check list: [UI_PACK.md](UI_PACK.md).

Done without a client: vanilla 1.26.50.4 UI analysis, which found the form input's `$max_text_edit_length: 100` and
no `enabled_newline`. Also done: `ui-pack/` (one redefined vanilla element, everything else `endbot_*`), a
reproducible `.mcpack` build with vanilla reference checking (`scripts/build_ui_pack.py`), the plugin marker
(`forms.MULTILINE_MARKER`) on multi-line inputs, and **Edit all at once (UI pack)...** behind
`[scripting] ui-pack-editor` (default false, also in `endbot.toml`), which refuses replies that lost their line
breaks. A reading of the locked Endstone path showed that a form reply's string content reaches the plugin
unchanged.

Manual gates: [UI_PACK.md checks 1–10](UI_PACK.md#manual-client-check), including whether the client cuts inputs at
100 characters (which would also affect the existing line editor for long lines) and a world achievement unlock with
the pack active. Do not enable `ui-pack-editor` by default or ship the pack in release bundles before those pass.

## BOT-019: One Operation API and principals

Status: **Planned — first step of the [product direction](DIRECTION.md); design agreed** (2026-10-28 JST).

Today the de facto API is `/bot` argv text: forms build command text, scripts turn typed operations back into argv
(`operation_argv`) to be parsed again, results are localized prose with `data` on some queries only, `view` couples the
service to forms, and `CommandSource` assumes a player or the console. Make every surface (chat, forms, books, scripts,
later the web) call `execute(Operation, Principal) -> Outcome`. No behavior change for players; no process split, web
or new operations here.

```text
Operation  { op, target: "Alice" | "#miners" | "#miners[2]", params: { typed } }
Principal  { kind: player | web | console | trigger, xuid?, name, grantRevision, language, origin? }
Outcome    { ok, code, args, data }        -- localized at the edge
Event      { to: Principal, kind, data }   -- chat, console log and later the web are sinks
```

Agreed decisions:

- The registry is language-neutral JSON, extended from `operations.json` with result shapes and error codes; Python,
  Luau and the Worker's TypeScript read or generate from it.
- Operations map 1:1 to commands (RPC), plus a few read models such as the Bot list and a status snapshot. No
  REST-style state resources.
- Sender-relative arguments are resolved at the entry: the in-game entry turns sender-relative `~` into absolute
  coordinates, so only Bot-relative `~` (destroy, block) remains in an Operation. A web spawn without a position uses
  the world spawn; the form offers coordinates. A script's start point becomes the principal's `origin`.
- Error codes start coarse, by what the user should do next: authorization, not found, invalid argument, temporary
  failure, conflict. Refine only when needed. During migration an Outcome carries both a code and the existing
  localized messages. The message catalog becomes exportable JSON.
- Authorization has two layers. Every principal except the console needs a valid XUID-based grant at the current
  grant revision. Then
  each channel adds its own condition: in-game players must be online with an unchanged authorization revision, web
  principals need a valid link and session, triggers and loops need their owner's grant, and the console is always
  allowed. This replaces the three current rules (command, loop, script).
- Web principals may use Groups and indexed members (`#group[1]`) under the same authorization.

Steps, each about one PR, none meant to change what players see:

1. Introduce `Principal` in place of `CommandSource`; resolving Endstone senders moves to the adapter; authorization
   follows the two-layer table.
2. Make `execute(Operation, Principal)` the entry. `execute(argv)` stays as parse-then-call, so existing tests keep
   working. Scripts send Operations directly.
3. Add an Operation-to-command formatter. Form echoes use it. Add a round-trip property test: parse(format(op)) == op.
4. Add `code` and `data` to every Outcome, starting with queries.
5. Remove `view` from the service; the in-game UI layer builds screens from Outcomes.
6. Turn notifications into Events.
7. Extend `operations.json` with a JSON Schema and validate or generate the Python and Luau sides from it.

## BOT-020: endbotd outside BDS

Status: **Planned, after BOT-019; release grouping open, see [delivery](DIRECTION.md#delivery)** (2026-10-28 JST).

Grow the supervisor into a long-running endbotd that owns authorization and controller policy, the script store,
Luau runner orchestration and the operation log. The plugin becomes a world adapter: it renders in-game UI, forwards
operations, and performs world observation, teleport and block reads. Keep all internal traffic loopback-only.

## BOT-021: Web on an operator-deployed Cloudflare Worker

Status: **Planned, after BOT-020; release grouping open, see [delivery](DIRECTION.md#delivery)** (2026-10-28 JST).

Worker + one Durable Object per server, deployed and paired by `endbot web deploy`; endbotd connects outbound over
WebSocket. Accounts are created only from a one-time code issued in game by `/botweb link`, with a passkey; endbotd
keeps the link table and re-authorizes every relayed operation. Cloudflare stores only XUID, GamerTag, passkey public
keys and sessions. Protocol versions are checked; `endbot update` redeploys the Worker. With BDS stopped, the web
still shows status and logs and edits scripts; BDS start/stop stays console-only. Update SECURITY.md with this boundary. Order: status and logs, then script editing (BOT-013),
then Bot control. Controller administration stays console-only. Main design work: pairing and key rotation, session
and CSRF handling, rate limits, revocation of web sessions when a controller is revoked, deployment documentation.
Microsoft sign-in is deferred (see the direction document).

## BOT-022: Watches and triggers for scripts

Status: **Planned** (2026-10-28 JST).

A script-facing "watch and wait" API for block changes (and later other non-Bot events). The plugin delivers matches
from Endstone events where available and from a batched server-thread check of registered positions otherwise. First
measure which block changes Endstone events cover on the locked pair. Document the minimum persistence in ticks that is
guaranteed to be delivered. Bound watches per controller and server. First version: a running script loops on `wait()`.
Second version: persistent triggers that survive restarts and run with their owner's grant. Scripts still never write
the world directly.

## BOT-023: Upstream login-verifier hook (optional)

Status: **Idea** (2026-10-28 JST).

Propose a narrow Endstone API that lets a plugin accept a login before BDS's Microsoft validator rejects it. If upstream
accepts it, drop `patches/endstone/` and patched builds. Until then the patch stays the source of truth.

## Deferred priorities

- Container/UI operations — crafting, villager trading, enchanting — use `item_stack_request` on current Bedrock, which
  the BOT-011 spike found unusable for Bots on the locked pair (no stack IDs outside hotbar/offhand). Low priority
  (2026-10-24 JST): even the follow-up spike (does opening a container deliver native stack IDs?) may wait. Do not
  substitute server-side inventory edits for vanilla crafting/trading.

- Inventory inspection and rearrangement are now scoped under BOT-011. Keep the menu rule: forms mirror commands and
  add no meaning of their own.
- `for <ticks>` duration and `use hold <ticks>` repeated charges shipped in 0.3.0; see
  [durations](COMMANDS.md#durations-for-and-charged-presses-hold). Real-server and human-client validation (bow/trident
  `hold` repeats, mining duration end, forms) remains a manual gate. The destroy block-count stop
  (`count <blocks>`, see BOT-005) shipped in 0.3.1 (2026-10-17, with its human check); item-count conditions for other inputs remain separate from duration
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
