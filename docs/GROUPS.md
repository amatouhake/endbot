# Shared Bot groups

A Group is a named, ordered list of Bots that `/bot` can address as one target: `#miners` for all members,
`#miners[2]` for one. Groups have an owner and are shared or private ([PERMISSIONS.md](PERMISSIONS.md#ownership)).

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

## How Groups behave

- Shared groups use the existing controller permission. `/botgroup` and `/bot` go through the same authorization,
  grant-revision check and ordered worker. Sequential operations recheck authority before requests/between members;
  batched inputs recheck throughout preparation and immediately before submitting the batch.
- Membership persists as immutable Bot UUIDs in `state/groups.json` (the plugin data directory's `groups.json` before endbotd), written atomically.
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

## Rules and limits

- Group names are case-insensitive, start with an ASCII letter/underscore, and contain at most 16 ASCII letters,
  digits or underscores. The stored spelling is retained for display.
- Removing a member compacts later indexes; re-adding appends it. Offline and forgotten entries do not compact
  automatically. There is no explicit reordering.
- A Group cannot be renamed.
- Bulk teleport applies the existing single-Bot teleport independently. There is no automatic formation, collision-safe
  scatter or frozen shared destination for a moving player. Prefer indexed commands where placement matters.
- Group forms, result summaries and ordinary action replies follow the controller's language. Some Group
  management diagnostics remain English.

## Known limits

- `/botgroup` shows generic `<action: string>` / `<arguments: message>` usage instead of useful suggestions. Commands
  are accepted; completion is weak, as for `/bot` ([#283](https://github.com/amatouhake/endbot/issues/283)).
- Bedrock's `string` target parameter rejects a leading `#`, so the commands take the whole target and tail as one
  message and parse it themselves.
- A whole-Group input reaches every member in one runtime turn, but each Bot has its own network and input ticks, so
  members are not guaranteed to act on the same BDS tick.
- Whether an operation works on an offline member depends on the operation: `stop` is accepted, an online-only input
  such as `jump` fails for that member alone.

What was checked on a server and on a client is under [validation status](COMPATIBILITY.md#bot-operations)
([record](https://github.com/amatouhake/endbot/blob/cad9d962b49dedabb60d6dd37641c67d2e31df84/docs/GROUPS.md#verification)); to run the Group probe see
[WINDOWS_DEV.md](WINDOWS_DEV.md#live-scripting-and-group-probes).
