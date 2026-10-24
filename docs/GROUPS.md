# Shared Bot groups

Groups are part of main and planned for 0.4.0. Use the plugin and runtime from the same version; an older runtime
without the required UUID pinning/input-batch capabilities is rejected. No Endstone core change is involved.

Scale: member lookups read the runtime's light `profiles` list (name, identity and state only), and the control
protocol bounds each request and response at 1 MiB. That fits a group input batch for several hundred members
and a profile list for thousands of Bots; the 64 KiB bound before this made every Group operation fail at roughly
100 to 200 Bot profiles.

## Try the command flow

From an authorized controller or the server console (omit the leading slash at the console):

```text
/bot Alice spawn
/bot Bob spawn
/botgroup create #miners
/botgroup add #miners Alice
/botgroup add #miners Bob
/botgroup list
/botgroup list #miners
/bot #miners stop
/bot #miners[1] look 0 90
/bot #miners destroy count 64
/bot #miners destroy stop
/botgroup result #miners
/botgroup result #miners 2
```

Wait for the Bots to be online and prepare their tools/targets normally before gameplay actions. Replies confirm
operation acceptance, not successful world outcomes. `destroy count 64` sets the existing completion limit for each Bot
individually; it does not distribute a shared total across the group.

`/botgroup list` lists shared groups; append `#miners` to show that group's members. `/botgroup help` shows management
syntax. Group references always use `#`, including creation. The former `members` subcommand is merged into `list`;
`members` and bare group arguments are no longer accepted. The stored names/UUIDs are unchanged, so existing groups
require no migration. Remove a member with `/botgroup remove #miners Alice`, or use
`/botgroup remove #miners #miners[1]` to remove a missing profile's entry.
`/botgroup delete #miners` deletes the group registration and leaves Bot profiles intact.
When using the OS CLI, quote the whole command, for example `endbot console "bot #miners stop"`.

## Menus and operation results

`/bot` or `/bot menu` lists individual Bots and direct `#group (count)` operation targets. Selecting a group opens
`/bot #miners menu`, which uses the same operation screen builder and categories as an individual Bot: stop, movement,
look, teleport, gameplay, inventory and Bot settings. It also offers the caller's operation history. Group controls remain
available for mixed online/offline and empty groups; execution reports operation-specific failures normally.
Group targets use ordinary buttons with the `#` prefix and member count; there is no oversized group heading.
**Group management...** opens the selected group's `/botgroup menu #group` screen. When entered from management,
the existing **Back to group management** button serves the same purpose without a duplicate link.

`/botgroup` alone (or `/botgroup menu`) opens the group list/create screen. `/botgroup menu #miners` opens management:
ordered members with connection state, add/remove membership and group deletion with confirmation. **Group operations...**
opens the same shared operation screen, with **Back to group management** returning to this management screen.
Members are paginated ten at a time (`/botgroup menu #miners 2`); selecting one opens its ordinary Bot menu and retains
the management return path through nested navigation. Direct `/bot #group menu` returns to the Bot/target list.

Teleport, resume/spawn (including coordinates), reconnect/despawn, chunk radius, status and block interaction are
available from the shared operation menus. Whole-group rename/forget remain rejected by the command and omitted from
the group menu; individual Bot profile controls offer them. Input forms use the existing command semantics: teleport,
spawn and interaction `~` coordinates are relative to the controller; look-at/mining coordinates are relative to each
Bot. Group settings use form defaults rather than displaying one member's value as a group-wide current value.
There is no formation/scatter feature. Duration/count limits apply per Bot.

Every mutation submitted by a form uses the same `/bot` or `/botgroup` command/permission path and echoes the actual
root/arguments in chat. Group/Bot UUIDs and membership order are checked on submission; changing membership or
deleting/recreating a group requires reopening old forms. A member's single-Bot form also retains a UUID guard through
navigation, so a replacement using the old name is not accidentally controlled. Closing/canceling management forms
does not perform the operation; deleting a group leaves its Bots registered.

Whole-group mutating-command acknowledgements are at most **two lines**, regardless of failure count: accepted/failed/not-run
counts and, if needed, failure reasons aggregated by category. At most three categories are shown, with excess entries
counted under "other". They confirm acceptance, not successful world outcomes. Missing responses after sending a batch
are counted as **acceptance unknown**, never silently described as not executed and never automatically retried.
Indexed targets retain the group index and Bot name in their ordinary single-member reply.

Mutating group operations write the same bounded English summary to the server log, including the controller name.
Player replies follow their language. Menus, result inspection and read-only queries do not add audit entries.
The bound applies to immediate command acknowledgements; BDS join/quit events and existing asynchronous mining notices
are separate events. Per-member details do not get dumped into the normal acknowledgement or audit log.

Read-only `/bot #miners status`, `/bot #miners hotbar` and `/bot #miners radius` print every member's actual query
result immediately, including `#group[index]`, the Bot name and any per-member error. Indexed queries use the same
format. Explicit queries have no ten-row page or two-line summary limit; query data is not truncated to the saved
operation-detail length. They add no audit entry and do not create, replace or advance operation history. For example,
after `jump`, checking `status` still leaves that `jump` result as history 1. Supplying a hotbar slot or radius value
(including `default`) is a mutation and retains the bounded acknowledgement/history/audit behavior.

`/botgroup result #miners [history]` prints **all member rows** of one saved operation, including its target/operation,
counts and per-member outcomes/reasons. Omit the number (or use `1`) for the latest operation; `2` selects the previous
operation, `3` the one before that. It is an inspection of saved request results, not a replay or fresh status query.
The menu uses the same snapshots (`/botgroup menu #miners result [history]`) and offers older/newer result buttons.
Snapshots describe mutating group-targeted `/bot` requests; queries, management confirmations and menu navigation do not replace them.
Each controller/group retains up to 20 results, subject to a global cap of 128 snapshots, evicting oldest first. These are in memory and cleared
on restart. Console/CLI share the console result slot. Another controller's operation cannot overwrite your result.
Details retain their request-time language. Editing membership/renaming a Bot does not change historical result rows;
deleting/recreating a group does not expose the previous group's result. Menu navigation pins result UUIDs so a new
operation cannot shift the intended older/newer destination; evicted results require reopening history.

Add/remove forms name the target group and use **Add**/**Remove** submit labels. Group deletion uses an action form
with the group name in its title, explanation and confirmation button; the registered Bots remain. The action-form
layout replaces the message-form layout whose title/content appeared blank in the user's client.

## Implemented behavior

- Shared groups use the existing controller permission. `/botgroup` and `/bot` go through the same authorization,
  grant-revision check and ordered worker. Sequential operations recheck authority before requests/between members;
  batched inputs recheck throughout preparation and immediately before submitting the batch.
- Membership persists as immutable Bot UUIDs in the plugin data directory's `groups.json`, written atomically.
  Rename keeps membership; a replacement Bot with the old name is not automatically a member.
- The member list is fixed when a bulk operation starts. Member order stays stable while members go offline. A missing
  profile keeps its entry and produces an individual failure; `spawn` does not create a replacement for that entry.
- Whole-group `move`, `look` (including `look at`), `jump`/`attack`/`use`, `sprint`/`sneak`, `stop`, `hotbar` and `drop`
  take one profile snapshot and send one UUID-pinned input batch. The runtime applies them in one synchronous dispatch
  turn, with individual Bot errors and replies in membership order. Per-Bot coordinates for `look at` are resolved
  before dispatch. Membership/report order does not require staggered starts. Independent network/input ticks still
  mean this is not a guarantee of the same BDS tick or simultaneous observed motion.
- Lifecycle operations, teleport, mining and other operations retain their existing sequential execution paths.
- A failed member does not skip the remaining Bots. Revocation/plugin shutdown during batch preparation prevents the
  whole input batch; during sequential work it cancels remaining requests. A submitted immediate-input batch runs to
  completion in one runtime turn, without a per-member permission callback. Revocation after its acceptance does not
  undo those inputs. Existing continuous-mining revocation/lease behavior still applies.
- Runtime requests carry the expected UUID and reject a stale/reused Bot name before applying an operation. This
  supplements the plugin's UUID lookup; it does not change the Microsoft/Xbox or local-bot login paths.
- Whole-group rename/forget are rejected. Use one member or a Bot name for those operations. Whole-group `/bot ... menu`
  opens the shared operation screen; indexed `menu` opens the existing single-Bot screen. `/botgroup menu #group` opens
  the distinct management screen, with a nested link to operations.
- `/botgroup` advertises separate native hints for `list`, `help`, `create`, `delete`, `add`, `remove`, `menu` and `result`,
  with optional group scope for `list` and group/Bot/member argument hints. It uses the same generic-parseability plus
  typed-enum approach as `/bot`. Generic branches remain for the locked native parser (including indexed removal);
  dynamic group/Bot name suggestions are unavailable on this baseline. Native client filtering can still be weak.

## Scope and follow-up choices

- Group names are case-insensitive, start with an ASCII letter/underscore, and contain at most 16 ASCII letters,
  digits or underscores. The stored spelling is retained for display.
- Removing a member compacts later indexes; re-adding appends it. Offline and forgotten entries do not compact
  automatically. Decide whether explicit reordering or stable numbered slots are needed after actual usage.
- Group creator metadata, group rename and richer permissions are not implemented yet.
- Bulk teleport applies the existing single-Bot teleport independently. There is no automatic formation, collision-safe
  scatter or frozen shared destination for a moving player. Prefer indexed commands while reviewing placement semantics.
- Group forms, new result summaries and ordinary action replies follow the controller's language. Some earlier Group
  management diagnostics remain English. Group diagnostics/forms need the usual Japanese-client review before release.

## Verification

Unit tests cover persistence/write failure, rename, missing profiles, fixed membership during a batch, partial failure,
revocation, shutdown and rejection of an older runtime. Runtime control tests check stale/reused-name rejection.

The opt-in real-BDS probe uses a fresh ignored directory, normal Endstone acquisition and the unchanged locked pair:

```text
python scripts/raknet_spike.py --groups --output build/group-feature-live
```

Use a dedicated Python environment containing the locked patched Endstone plus this plugin/CLI, and the repository's
pinned runtime dependencies. See [Windows development](WINDOWS_DEV.md) for the existing setup. Raw logs, keys, profiles
and world files stay under the ignored output directory; never add them to Git.

The first live probe established that Bedrock's `string`/Id target parameter rejects a leading `#`. The feature uses a
first-position `message` overload to accept the full target and command tail through public Endstone declarations.
Human-client smoke confirmed that execution works, while native completion remains weak as described below.

On 2026-10-21 JST, the Windows RakNet console probe passed with locked Endstone `0.11.13+endbot.1` and BDS `1.26.52.3`
(observed Windows build `51798905`). It exercised group creation/membership/deletion, whole-group stop, one-based indexed
look, tailed timed movement, invalid-index diagnostics, offline-member partial failure and retention of Bot profiles
after Group deletion. The broader existing probe also passed controller-policy and invalid-login controls and clean
shutdown. Post-session world-history inspection found the required flags unchanged/false; no human Xbox login or
achievement unlock was observed. This evidence covers console execution on that transport/platform, not client UI.

Automated checks passed: 199 plugin tests, 134 runtime tests, 47 repository tests, 291 CLI tests (2 skipped), Python lint,
runtime syntax checks and repository portability. The final capabilities/UUID-guard addition was covered by the eight
control-server tests plus the final real-BDS probe.

Human-client smoke (2026-10-21 JST, user report and three screenshots, source commit `048e0aa`): the user reported that
all four requested checks behaved as expected: creation/addition/member listing, whole-group and indexed command
input, ordinary/indexed single-Bot menus, and offline-member behavior. The screenshots show ordered Alice/Bob entries,
indexed teleport, whole-group stop/jump requests, and Bob retaining index 2 while offline. An online-only jump rejected
offline Bob individually while Alice's request succeeded. The retained offline Bob session accepted `stop`; offline
failure is operation-specific, so the earlier manual instruction expecting every offline `stop` to fail was too broad.

Two limitations were observed in that initial build:

- `/botgroup` shows generic `<action: string>` / `<arguments: message>` usage instead of useful management-operation
  suggestions. Command acceptance passed; completion usability did not. The locked baseline's generic-overload and
  dynamic-name completion limits also apply (see BOT-008 in [the backlog](BACKLOG.md)).
- `/bot #miners jump` starts the Bots visibly one after another. This matched the original sequential implementation;
  it does not validate synchronized group actions. Ordered chat replies alone are not timing evidence; the user's
  observation of the Bot actions establishes the visible skew. No timing measurement was taken.

Follow-up implementation (2026-10-21 JST): immediate inputs now use the synchronous batch above and `/botgroup` has
operation-specific enum hints like `/bot`. An older runtime without the `groupInputBatch` capability is rejected rather
than silently reverting to staggered inputs. Runtime tests demonstrate that a queued microtask cannot interleave
between members, an offline/replaced member does not prevent later members, invalid batch envelopes cause no effects,
and response size does not grow with unnecessary full Bot status. Plugin tests cover one lookup/batch, UUIDs/order,
localized ordinary reply rendering, pre-dispatch revocation/shutdown and preserved sequential cancellation semantics.
The follow-up Windows RakNet console probe passed through real BDS with the same locked pair, including both-online
group jump, timed movement and offline-member jump failure. The 204 plugin tests, 135 runtime tests, lint, syntax,
portability and plugin-wheel build passed. Client-rendered completion and visible timing remain separate checks.

The user subsequently confirmed that management completion was comparable to `/bot` and simultaneous group execution
worked on the real client (source `8a8f875`). The next agreed syntax revision merged `members` into `list [#group]` and
required `#` in every management group argument, while retaining operation-first management grammar. Tests cover scoped
listing, unchanged persisted identities/order, bare/indexed whole-group rejection without mutation, and removal/addition.
The syntax revision passed all 207 plugin tests, lint, portability and the wheel build. Real-BDS Group checks passed
for prefixed create/add/delete, unscoped/scoped `list`, indexed removal/re-addition, help, and rejection of bare names
and the retired `members` command. The first full probe subsequently failed the older timed-movement check (zero
observed distance); a fresh-world rerun passed the full probe, including movement and clean shutdown. The syntax checks
passed in both worlds.

The user confirmed the six revised management forms (list all, list one group, create, add, remove and delete) worked
on the real client with source `905c924`. Group-menu coverage and the chat/server-log presentation of group-targeted
operations were raised as the next design topics; that smoke did not cover the subsequent menu implementation.

Follow-up implementation added the menu/result behavior above. All 221 plugin tests passed, including 100 failed
members producing only two acknowledgement/audit lines, controller-private result retrieval, pagination, lost-response
classification, command/form result parity, management-form submit/cancel, stale membership/group/result rejection,
and old-name reuse protection for member controls/addition choices. Python lint, portability and the plugin-wheel build
passed. The updated real-BDS probe passed native menu/result declarations, bounded partial-failure logs and paged result retrieval, the
broader existing checks, and clean shutdown. Native form rendering itself requires the human client.

Human-client follow-up (source `616f3e6`): the user reported the group menus/results looked good overall. Two screenshots
identified a deletion confirmation with blank title/content and membership forms with a default Run submit label. The
user requested bare `/botgroup` as the menu shortcut, named deletion/add/remove forms, and result numbers selecting
previous operations with all member rows displayed. Those changes are implemented on `feat/groups`; the earlier
prototype commits remain in its history. The former `codex/group-prototype` branch is retained, without a merge or release.
The revised implementation passes 224 plugin tests, including full 100-row inspection, operation-history selection,
bounded retention, private ownership, eviction and UUID-pinned navigation while new operations arrive.
Lint, repository portability and the plugin-wheel build passed. A fresh Windows RakNet real-BDS run with the unchanged
locked pair passed native bare-command handling, latest/previous operation selection, all group smoke checks, the
broader movement/controller/authentication checks and clean shutdown. This console run does not validate client form
appearance or actual Xbox achievement unlocks.

The user subsequently confirmed the game-client scope for source `f419241` passed, including revised forms/history.
The next agreed revision keeps `/botgroup` management-first, adds direct group targets to `/bot`, and shares the Bot
operation screen between single and group targets. It retains operation categories and adds the missing command-backed
controls rather than flattening the hierarchy. Back navigation preserves the management/operation entry. Common
despawn/profile confirmations now use the visible action-form presentation as group deletion does.
The shared-screen revision passes 231 plugin tests, including direct selection, management/member/result return paths,
mixed/empty groups, UUID-pinned teleport, group radius changes and stale menu rejection.
Native construction of 132 screens in English/Japanese passed on the locked Endstone environment; this checks the
form API payloads, not client appearance. Lint, portability and the plugin-wheel build passed. The fresh Windows
RakNet BDS probe passed all group/history checks, the broader controller/movement/authentication checks and clean
shutdown with the unchanged locked pair.

At that revision, shared operation screens/return paths on the real client and live revocation remained manual gates.
The user's screenshot of source `278fe78` showed an oversized native Group heading in the target list. The follow-up
removes that heading and adds the requested reverse link from group operations to that specific group's management.
Navigation refreshes the current membership but pins the group UUID, rejecting deletion/recreation under the same name.
All 232 plugin tests, lint and repository portability checks passed for this follow-up.
The user confirmed both client checks for source `794882b` passed (heading removal and the reverse management link),
with no issues found in that scope. They then requested data-oriented query replies: status/hotbar/radius queries now
print all member results immediately, without adding audit entries or changing operation history. The revision passes
236 plugin tests covering whole/indexed query payloads, retained history, empty history, member failures, full long
query data, 100-member output and preserved setter behavior. The user subsequently confirmed the latest query behavior
worked on the game client with source `07f661e`.
PR-readiness verification reran the current branch: all 47 repository tests, 236 plugin tests, 135 runtime tests and
291 CLI tests (2 skipped) passed, alongside runtime syntax, Python lint and repository portability. Windows runtime
startup/UDP-owner tests needed ordinary OS access outside the restricted test sandbox; they passed there. This is
local verification, not a GitHub Actions result. Remote `main` still matched the branch base `99ba05e` at this check.
Lint, portability and the plugin-wheel build passed. The fresh Windows RakNet BDS probe passed all three query
payloads, absence of query audit entries and preserved latest/previous operation results, plus the existing group,
controller/movement/authentication checks and clean shutdown on the unchanged locked pair.
Group execution has a user-reported visual timing pass, without a same-tick measurement or guarantee.
No Xbox achievement unlock was reported. These checks do not establish other transports/platforms or support for another
Endstone/BDS pair.
Live controller revocation during an in-flight group operation and actual Xbox achievement unlocks remain explicit
manual/E2E gates for merge/release assessment; passing unit builds or world-history flags does not satisfy them.
