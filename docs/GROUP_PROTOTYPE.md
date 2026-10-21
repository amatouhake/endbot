# Shared Group command prototype

This source-tree prototype exercises the Group design before the Luau API. It is not a published release; Group menus
and result presentation still need a human-client check. Use the plugin and runtime from the same checkout; an older
runtime without the required UUID pinning/input-batch capabilities is rejected. No Luau runner, script editor, web
service or Endstone core change is involved.

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

`/bot menu` now includes **Groups...**. `/botgroup menu` opens the group list/create screen; `/bot #miners menu` (also
`/botgroup menu #miners`) opens the group screen. It offers ordered members with connection state, group operations,
add/remove membership, group deletion with confirmation, and the initiating controller's recent operation result.
Members are paginated ten at a time (`/botgroup menu #miners 2`). Selecting an existing member opens its ordinary Bot
menu. Teleport and other individual-only controls remain there; there is no formation or group teleport button.
Group inputs and mining controls reuse the ordinary forms, with duration/count limits described per Bot.

Every mutation submitted by a form uses the same `/bot` or `/botgroup` command/permission path and echoes the actual
root/arguments in chat. Group/Bot UUIDs and membership order are checked on submission; changing membership or
deleting/recreating a group requires reopening old forms. A member's single-Bot form also retains a UUID guard through
navigation, so a replacement using the old name is not accidentally controlled. Closing/canceling management forms
does not perform the operation; deleting a group leaves its Bots registered.

Whole-group operation acknowledgements are at most **two lines**, regardless of failure count: accepted/failed/not-run
counts and, if needed, failure reasons aggregated by category. At most three categories are shown, with excess entries
counted under "other". They confirm acceptance, not successful world outcomes. Missing responses after sending a batch
are counted as **acceptance unknown**, never silently described as not executed and never automatically retried.
Indexed targets retain the group index and Bot name in their ordinary single-member reply.

Mutating group operations write the same bounded English summary to the server log, including the controller name.
Player replies follow their language. Menus, result inspection and read-only queries do not add audit entries.
The bound applies to immediate command acknowledgements; BDS join/quit events and existing asynchronous mining notices
are separate events. Per-member details do not get dumped into the normal acknowledgement or audit log.

`/botgroup result #miners [page]` retrieves the caller's latest result for that group, with ten member rows per page,
operation/target, counts, and per-member outcomes/reasons. It is an inspection of saved request results, not a replay or
fresh status query. The menu's recent-result screen uses the same snapshot (`/botgroup menu #miners result [page]`).
Snapshots describe group-targeted `/bot` requests; management confirmations and menu navigation do not replace them.
Each controller/group keeps only its latest result, with a global cap of 128 snapshots; these are in memory and cleared
on restart. Console/CLI share the console result slot. Another controller's operation cannot overwrite your result.
Details retain their request-time language. Editing membership/renaming a Bot does not change historical result rows;
deleting/recreating a group does not expose the previous group's result. An open result page detects replacement of
its snapshot before navigating to another page.

## What the prototype implements

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
- Whole-group rename/forget are rejected. Use one member or a Bot name for those operations. Whole-group `menu` opens
  the group screen; indexed `menu` opens the existing single-Bot screen.
- `/botgroup` advertises separate native hints for `list`, `help`, `create`, `delete`, `add`, `remove`, `menu` and `result`,
  with optional group scope for `list` and group/Bot/member argument hints. It uses the same generic-parseability plus
  typed-enum approach as `/bot`. Generic branches remain for the locked native parser (including indexed removal);
  dynamic group/Bot name suggestions are unavailable on this baseline. Native client filtering can still be weak.

## Provisional choices to review while trying it

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
python scripts/raknet_spike.py --groups --output build/group-prototype-live
```

Use a dedicated Python environment containing the locked patched Endstone plus this plugin/CLI, and the repository's
pinned runtime dependencies. See [Windows development](WINDOWS_DEV.md) for the existing setup. Raw logs, keys, profiles
and world files stay under the ignored output directory; never add them to Git.

The first live probe established that Bedrock's `string`/Id target parameter rejects a leading `#`. The prototype adds a
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
operations were raised as the next design topics; their behavior is not settled or implemented by this validation.

Follow-up implementation added the menu/result behavior above. All 221 plugin tests passed, including 100 failed
members producing only two acknowledgement/audit lines, controller-private result retrieval, pagination, lost-response
classification, command/form result parity, management-form submit/cancel, stale membership/group/result rejection,
and old-name reuse protection for member controls/addition choices. Python lint, portability and the plugin-wheel build
passed. The updated real-BDS probe passed native menu/result declarations, bounded partial-failure logs and paged result retrieval, the
broader existing checks, and clean shutdown. Native form rendering itself requires the human client.

Remaining gates include completion rendering for the new hints, group forms/result presentation and live revocation.
Group execution has a user-reported visual timing pass, without a same-tick measurement or guarantee.
No Xbox achievement unlock was reported. These checks do not establish other transports/platforms or support for another
Endstone/BDS pair.
