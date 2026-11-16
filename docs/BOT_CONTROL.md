# How Bot control works

This document is for developers. It explains how the player commands in [COMMANDS.md](COMMANDS.md) and the Group
operations in [GROUPS.md](GROUPS.md) are carried out: which path each input takes to BDS, what the runtime predicts
and what it waits for, and why the command declaration has its shape. The sections follow the headings of
COMMANDS.md. What was checked on real servers is in [COMPATIBILITY.md](COMPATIBILITY.md#bot-operations).

## Control forms

Forms are only another way to type commands. Every button or form submission becomes the equivalent `/bot` arguments
(for example `/bot Alice attack interval 40`) and goes through the same permission check, command queue, parser and
replies, so a typed command and a form submission always behave identically. A controller whose permission is revoked
while a form is open gets the usual permission reply on submit, because the check runs again at submission.

## Mining blocks

BDS performs ordinary Survival breaking, including hardness/tool speed, harvest drops, durability and cancellable
`BlockBreakEvent` handling. The plugin never sets the mined block to air, dispatches `/setblock`, or grants operator
permissions. The pinned baseline uses server-authoritative block breaking; other modes are rejected explicitly.

`destroy continuous` selects the next reachable block along the same ray after each BDS target update. The `count`
budget counts BDS-completed targets. Single-block mining ends when BDS sends a changed target block. The runtime
waits for server updates instead of predicting instant destruction or loot.

Single-block `destroy` and coordinates keep the 10-minute safety timeout: 12000 input ticks (about ten minutes at 20
TPS). An expiring runtime lease also ends mining if the plugin/control worker stops responding (200 input ticks,
about ten seconds).

Start and stop are logged on the server console at info level. Any other ending is recorded at warning level with the
same message the starting controller receives, also for controllers who are offline. The log entry of a job notice
stays English even when the controller's reply is Japanese.

## Lifecycle

`visibility`, `forget` and `rename` stay with the Bot's owner or the console (decision S12 in
[PERMISSIONS.md](PERMISSIONS.md)).

The reserved Bot names (`help`, `list`, `menu`, `ping`) live in the operation registry (`operations.json`,
`reservedBotNames`), the one list every surface reads.

`resume` reconnects without relocation, allowing BDS native player persistence to win. `reconnect` tears down the
transport and reconnects without changing identity or location.

The plugin rejects names of online real players. Endstone does not expose a reliable complete offline GamerTag-history
index, so an operator must avoid known offline real-player names.

## Teleport

Teleport uses `Actor.teleport`; it never dispatches vanilla `/tp` and does not require cheat-command permission.
Relative destination and `facing` coordinates are both resolved from the same command-source snapshot.

After a same-dimension server teleport, the runtime acknowledges the correction and resumes normal client-predicted
vertical movement. A Bot teleported into unsupported air therefore falls and lands instead of continually resending
the teleport height.

Dimension changes reset old movement prediction and entity targets, acknowledge the dimension/loading transition,
and request the configured chunk radius. Server motion is incorporated into movement prediction for knockback.

## Chunk loading range

Setting a radius sends a new chunk-radius request immediately while the Bot is online, without reconnecting. The reply
shows the requested value, its source and the radius BDS answered with.

## Inputs and actions

Continuous actions run each 20 Hz runtime input tick. Movement behaves as held input.

Sprint and sneak emit Bedrock's one-tick start/stop transitions as well as steady held-state flags; reconnect input reset
does not replay transitions from the previous transport.

`use` sends the held item's normal Bedrock interaction. `use continuous` holds right-click: its first input tick sends
the initial air-use interaction, and later ticks send simulation-tick interactions in the same authoritative input
stream. `use once` and `use interval` are short press/release actions. `use for <ticks>` counts from the first air-use
transaction; if a pending block interaction occupies the input tick, use waits without consuming its hold duration.
`hold <ticks>` holds use for that many use transactions per press and then releases.

### Inventory and hotbar

`inventory` reads the authoritative inventory through Endstone on the server thread. This query changes no items and
needs no runtime inventory cache or request for a named online Bot. The server rejects ordinary Xbox players as Bot
inventory targets.

`hotbar` selection is sent through the normal Bedrock equipment path and inventory updates continue to come from BDS;
Endbot does not keep a second inventory.

BDS does not push every pickup's inventory delta to the picker. The Endbot plugin therefore reads all 36 actual BDS
inventory slots plus offhand and armor at a configurable interval (four server ticks by default) and sends changed
read-only observations over that Bot's existing connection, plus periodic unchanged corrections (20 server ticks by
default). The runtime updates its transient cache, including counts and typed NBT, and re-announces a changed held
item through normal Bedrock equipment packets, including offhand changes. This makes same-session pickups usable by
`hotbar`, `drop` and held-item actions without reconnecting, normally within four server ticks (about 0.2 seconds at
20 TPS). No item is added, removed or reassigned by the observer.

Fresh observations also correct an optimistic drop if BDS rejected it; an unchanged correction can take about one
second with default settings. Native inventory updates also announce changed held equipment, independent of periodic
observations. Unknown/invalid observations are ignored as a whole; an unknown or empty selected slot still fails with
`Selected hotbar slot is empty`. Without the matching plugin, reconnect remains the recovery for missing native
deltas.

The versioned `endbot:inventory-v2` observation carries exact typed item/NBT data in bounded fragments, applied only
after a full valid snapshot arrives. The `hotbar-sync-interval-ticks` and `hotbar-sync-resend-ticks` settings cover the
entire inventory. Size bounds are described in the validation record linked from
[COMPATIBILITY.md](COMPATIBILITY.md#bot-operations).

### Swap and hold

Both mutations use a legacy normal transaction with two window-0 container actions, carrying exact item data without
stack IDs. The runtime predicts both slots until the next authoritative observation corrects them, and announces
selected equipment after a changed selection or held stack. Group swap/hold use the existing UUID-pinned batch input
path with bounded request replies, audit and saved history.

The native `EndbotSwap`/`EndbotHold` typed overloads provide hints; enum-free required message tails continue to carry
execution under the pinned BDS declaration rules (see [command declaration](#command-declaration)).

### Dropping items

`drop` drops one selected item and `drop stack` requests the selected stack through normal inventory transactions;
wider selectors use the same legacy container-to-world path with exact observed item data. `drop all` sends one
transaction per non-empty slot, and BDS validates each. Group drops remain mutations with bounded summaries, audit and
history.

### Interacting with blocks

`interact` sends the protocol-2193 press, server-authoritative item-interaction tick, and release sequence; BDS decides
reach, legality, collision, placement, and inventory decrement. A placeable selected item can therefore place a block
in Survival without the plugin editing the world.

### Targets

The shared target syntax for the left- and right-click verbs (crosshair, coordinates, block type, `@` entity) was
decided on 2026-10-24.

After each entity click, the next waits until BDS reports the held item it changed, or at most 10 ticks, because BDS
rejects a click carrying a stale held item.

### Looking around

`target`, `block`, `blocks`, `entity` and `entities` read BDS through the Endstone SDK and never change anything.
`blocks` stops reading once `limit` matches are certain.

### Entities and vehicles

`interact @...` follows the vanilla client path (`item_use_on_entity` with action `interact`).

While riding, the Bot follows the vehicle position BDS sends and ignores its own movement prediction. `dismount`
sends the vanilla leave-vehicle action. As in the vanilla client, the rider chooses its exit spot: the plugin checks
the neighbouring blocks for a solid floor and two passable blocks above it, at the vehicle's level, then one above,
then one below. Other rideables use the minecart ring, matching Bedrock's default "valid ground position around the
entity". Block passability is a conservative name list (unknown blocks are solid). The exit point stays inside its
block, 0.05 clear of the vehicle's box and the block edges, so the Bot never rests on a boat's edge or a neighbouring
block.

BDS ignores a rider's reported position but corrects a sideways jump right after the link ends, so the Bot reports the
exit spot for two ticks while still riding and then sends the leave action; its first free input then does not move.
If BDS never confirms the leave within 40 ticks, the Bot stops counting as riding.

The runtime has no world collision: it starts falling when BDS corrects its position. BDS skips that correction while
a boat (or another standable entity) is nearby, so a Bot whose floor was removed next to a boat stood in the air. The
plugin checks every hotbar-sync interval whether a Bot standing on a block top has a solid block under its feet box
and, if not, tells the runtime to start falling; BDS then corrects the landing. Fractional heights (slabs, carpets),
water, flight and standing on a boat or shulker are never reported.

### Command declaration

The command declaration separates parseability from completion because live BDS overload matching proved three
rules (verified with a probe plugin dispatching 42 documented forms through the real `compileCommand` path):
trailing optional parameters never consume input; `string` rejects numeric tokens; and parameters positioned
after an enum never match. A tail consumer must therefore be a required `message` with no enum before it, while
suggestions need enums at the same positions in separate overloads:

```text
/bot [command: string] [topic: string]
/bot (ping|list|help|menu)<command: EndbotRoot>
/bot <command: string> <page: int>
/bot <name: string> <operation: string>
/bot <name: string> <operation: string> <arguments: message>
```

The `<name>` string overloads are the parseability layer: they accept every 2-token and every longer named
form (numeric help pages use the required-int overload instead, since strings reject numbers). The typed enum
overloads below them — operations, movement direction, action modes, on/off, dimension aliases, block faces, and
`stack` — exist so completion has something to advertise, but real Minecraft-client smoke showed they do not
cleanly narrow the UI while a compatible generic string branch remains: generic overloads stay visible and
dominate filtering, for both the name-first `/bot` surface and the selection-model `/botprobe` (an ad-hoc
diagnostic probe used during the command-parser investigation, not a shipped Endbot command). Numeric tokens
narrow better only because they eliminate the generic `string` branch. A broad `<operation: message>` fallback
was already rejected because it absorbs all named-operation suggestions.

On this pinned Endstone 0.11 baseline, execution is therefore prioritized over completion: every documented form
parses, while autocomplete stays degraded. Deeper command declaration redesign is deferred until upstream Endstone
command API work (Brigadier-style typed command tree, upstream PR #509) matures; this layout is intentionally a
compatibility stopgap, not the final command API. The pinned Endstone public API does not expose supported dynamic
completion for Bot profile names, so names remain strings; players use `/bot list` to discover them. Endbot does not
use private registry or raw soft-enum hooks.

The full declaration is `COMMAND_USAGES` in `plugin/endbot/src/endstone_endbot/plugin.py`, and
`plugin/endbot/tests/test_command_declarations.py` models the three rules as a regression check.

## Shared Group menus and results

Group forms echo and submit the same `/bot` or `/botgroup` commands and authorization checks, with UUID/membership
checks to reject stale forms.

Whole-group mutating-command acknowledgements and their normal audit entries are capped at two lines: counts plus
grouped failure categories, independent of group/failure size. Saved results are kept in memory, up to 20 operations
per caller/group and 128 globally, evicting oldest first. Group queries create no audit entry. A private Group
resolves as not found for other players (decision S4 in [PERMISSIONS.md](PERMISSIONS.md)).

## Operational states

One Bot's generation/reconnect timer and inputs are independent of every other Bot. An unexpected disconnect retains
the profile UUID and retries with exponential backoff capped at 30 seconds, stopping after the configured attempt limit.
Explicit reconnect and live rename wait for the old transport to close before replacement, so a new session cannot race
the old one.

## Groups

How Groups are implemented. The player-facing behavior is in [GROUPS.md](GROUPS.md); this section uses the same
headings.

### Scale

Member lookups read the runtime's light `profiles` list (name, identity and state only), and the control protocol
bounds each request and response at 1 MiB. That fits a group input batch for several hundred members and a profile
list for thousands of Bots. The earlier 64 KiB bound made every Group operation fail at roughly 100 to 200 Bot
profiles.

### Managing Groups

- Membership persists as immutable Bot UUIDs in `state/groups.json` (the plugin data directory's `groups.json`
  before endbotd), written atomically. Rename keeps membership; a replacement Bot with the old name is not
  automatically a member.
- The former `members` subcommand is merged into `list`; `members` and bare group arguments are no longer accepted.
  The stored names/UUIDs are unchanged, so existing groups required no migration.
- `/botgroup` advertises separate native hints for `list`, `help`, `create`, `delete`, `add`, `remove`, `menu` and
  `result`, with optional group scope for `list` and group/Bot/member argument hints. It uses the same
  generic-parseability plus typed-enum approach as `/bot`. Generic branches remain for the locked native parser
  (including indexed removal); dynamic group/Bot name suggestions are unavailable on this baseline. Native client
  filtering can still be weak.
- Bedrock's `string` target parameter rejects a leading `#`, so `/bot` and `/botgroup` take the whole target and tail
  as one message and parse it themselves.

### Operating a Group

- Shared groups use the existing controller permission. `/botgroup` and `/bot` go through the same authorization,
  grant-revision check and ordered worker. Sequential operations recheck authority before requests and between
  members; batched inputs recheck throughout preparation and immediately before submitting the batch.
- The member list is fixed when a bulk operation starts. Member order stays stable while members go offline. A
  missing profile keeps its entry and produces an individual failure; `spawn` does not create a replacement for that
  entry.
- Whole-group `move`, `look` (including `look at`), `jump`/`attack`/`use`, `sprint`/`sneak`, `stop`, `hotbar` and
  `drop` take one profile snapshot and send one UUID-pinned input batch. The runtime applies them in one synchronous
  dispatch turn, with individual Bot errors and replies in membership order. Per-Bot coordinates for `look at` are
  resolved before dispatch. Membership/report order does not require staggered starts. Independent network/input
  ticks still mean this is not a guarantee of the same BDS tick or simultaneous observed motion.
- Lifecycle operations, teleport, mining and other operations retain their existing sequential execution paths.
- A failed member does not skip the remaining Bots. Revocation or plugin shutdown during batch preparation prevents
  the whole input batch; during sequential work it cancels remaining requests. A submitted immediate-input batch runs
  to completion in one runtime turn, without a per-member permission callback, so revocation after its acceptance
  does not undo those inputs. Existing continuous-mining revocation/lease behavior still applies.
- Runtime requests carry the expected UUID and reject a stale/reused Bot name before applying an operation. This
  supplements the plugin's UUID lookup; it does not change the Microsoft/Xbox or local-bot login paths.

### Replies

- Missing responses after sending a batch are counted as acceptance unknown, never described as not executed and
  never automatically retried.
- Mutating group operations write the same bounded English summary to the server log, including the controller
  name; player replies follow their language. The two-line bound applies to immediate command acknowledgements; BDS
  join/quit events and asynchronous mining notices are separate events. Per-member details are not dumped into the
  normal acknowledgement or audit log.

### Queries

Read-only `status`, `hotbar` and `radius` on a Group print every member's actual query result. Query data is not
truncated to the saved operation-detail length, adds no audit entry, and does not create, replace or advance
operation history.

### Operation results

Result snapshots are kept per controller and group (up to 20) under a global cap of 128, evicting oldest first, in
memory only. Console and CLI share the console result slot. Details retain their request-time language. Menu
navigation pins result UUIDs, so a new operation cannot shift the intended older/newer destination; evicted results
require reopening history.

### Menus

- Group targets in `/bot` use ordinary buttons with the `#` prefix and member count; there is no oversized group
  heading. When the operation screen is entered from management, the existing **Back to group management** button
  serves as the link back, without a duplicate **Group management...** link.
- Group deletion uses an action form with the group name in its title, explanation and confirmation button. The
  action-form layout replaced the message-form layout whose title and content appeared blank on a client.
- Every mutation submitted by a form uses the same `/bot` or `/botgroup` command/permission path and echoes the
  actual root and arguments in chat. Group/Bot UUIDs and membership order are checked on submission, so changing
  membership or deleting/recreating a group requires reopening old forms. A member's single-Bot form retains a UUID
  guard through navigation, so a replacement Bot using the old name is not accidentally controlled.

### Probes

To run the live Group probe, see [WINDOWS_DEV.md](WINDOWS_DEV.md#live-scripting-and-group-probes).
