# M2 commands and operation

## Quick start

Every command names its Bot; there is no selected-target state.

```text
/bot Alice spawn
/bot Alice tp me
/bot Alice jump
/bot Alice attack continuous
/bot Alice stop
/bot Alice despawn
```

`/bot` and `/bot help` show help page 1. `/bot help <page>` (1-3) shows a paginated command summary modeled on
vanilla Bedrock `/help`, and `/bot help <operation>` (for example `/bot help move`) shows every supported
syntax for that operation with a concise explanation. `/bot list` summarizes every known profile and
its lifecycle state. `/bot Alice status` combines runtime state with authoritative dimension/position/rotation when the
BDS player entity is present.

```text
--- Endbot help page 1 of 3: lifecycle, status and teleport ---
/bot ping | /bot list | /bot help - connectivity, profiles, this help
/bot <name> spawn - create or connect a Bot
/bot <name> resume - reconnect a profile without moving it
...
Next page: /bot help 2 - detail: /bot help <command>
```

```text
Endbot help: move
/bot <name> move forward|backward|left|right|stop
Movement acts as held input; stop halts it.
```

Page 1 covers lifecycle, status, and teleport; page 2 covers movement and actions; page 3 covers
inventory, world interaction, and advanced lifecycle. Each page is one chat message per line so it
reads in Bedrock chat without dense pipe-separated one-liners.

## Lifecycle

```text
/bot <name> spawn
/bot <name> spawn at <x> <y> <z>
/bot <name> spawn at <x> <y> <z> facing <yaw> <pitch>
/bot <name> spawn at <x> <y> <z> in <dimension>
/bot <name> resume
/bot <name> reconnect
/bot <name> despawn
/bot <name> forget
/bot <name> rename <new-name>
```

`spawn` creates a profile if necessary and places the arriving UUID at the caller by default. Console sources must give
an explicit position and dimension. Spawning an existing offline profile reuses its UUID but applies the requested
placement. `resume` reconnects an existing profile without relocation, allowing BDS native player persistence to win.
`reconnect` tears down and reconnects without changing identity or location. `despawn` is intentional offline state and
cancels automatic reconnect. `forget` requires an offline profile and removes only Endbot registration; it does not
purge BDS player data. A live rename preserves UUID and reconnects automatically so the login name changes.

If a requested spawn never reaches the world, its pending placement is discarded by resume, reconnect, despawn,
forget, or rename. Those operations therefore never consume an abandoned spawn placement later.

Spawn resolves coordinates and verifies the destination dimension before creating or starting a profile. A first spawn
also rejects an active real-player name; an existing Bot may reuse its own UUID/name. Invalid placement therefore has no
lifecycle side effect.

Names use 1–16 ASCII letters, digits, or underscores. Collisions are case-insensitive. The plugin rejects names of
online real players. Endstone does not expose a reliable complete offline GamerTag-history index, so an operator must
avoid known offline real-player names.

## Teleport

```text
/bot Alice tp me
/bot Alice tp Steve
/bot Alice tp 100 64 -20
/bot Alice tp 100 64 -20 90 0
/bot Alice tp 100 64 -20 facing Steve
/bot Alice tp 100 64 -20 facing 100 70 -10
/bot Alice tp in nether 100 64 -20
```

`overworld`, `nether`, and `end` map to their Minecraft namespaced IDs; a raw namespaced ID is also accepted. `~`
relative destination and `facing` coordinates are both resolved from the same command-source snapshot. Teleport uses
`Actor.teleport`; it never dispatches vanilla `/tp` and does not require cheat-command permission.

After a same-dimension server teleport, the runtime acknowledges the correction and resumes normal client-predicted
vertical movement. A Bot teleported into unsupported air therefore falls and lands instead of continually resending
the teleport height.

## Inputs and actions

```text
/bot Alice move forward|backward|left|right|stop
/bot Alice look <yaw> <pitch>
/bot Alice look at <x> <y> <z>
/bot Alice jump|attack|use [once|continuous|interval <ticks>|stop]
/bot Alice sprint on|off
/bot Alice sneak on|off
/bot Alice hotbar
/bot Alice hotbar <1-9>
/bot Alice interact <x> <y> <z> <down|up|north|south|west|east>
/bot Alice drop
/bot Alice drop stack
/bot Alice stop
```

Omitting an action mode means `once`. Continuous actions run each 20 Hz input tick; interval actions accept 1–72000
ticks. Movement behaves as held input. Global `stop` clears movement, scheduled actions, sprint, and sneak while keeping
the session connected. `look` remains at its last rotation after stop. `use` sends the held item's normal Bedrock
interaction; with an empty hand it is a safe no-op. Endbot does not invent an item or issue a server command.
Sprint and sneak emit Bedrock's one-tick start/stop transitions as well as steady held-state flags; reconnect input reset
does not replay transitions from the previous transport.

`hotbar` reports or changes the selected slot using human-facing 1–9 numbering. Selection is sent through the normal
Bedrock equipment path and inventory updates continue to come from BDS; Endbot does not keep a second inventory.

`use` means using or holding the selected item in the air, such as food, a bow, or a trident. `interact` is a normal
right-click on the named block face. It sends the protocol-2193 press, server-authoritative item-interaction tick, and
release sequence; BDS decides reach, legality, collision, placement, and inventory decrement. A placeable selected
item can therefore place a block in Survival without the plugin editing the world. `drop` drops one selected item and
`drop stack` drops the selected stack through normal inventory transactions.

The command declaration separates parseability from completion because live BDS overload matching proved three
rules (verified with a probe plugin dispatching 42 documented forms through the real `compileCommand` path):
trailing optional parameters never consume input; `string` rejects numeric tokens; and parameters positioned
after an enum never match. A tail consumer must therefore be a required `message` with no enum before it, while
suggestions need enums at the same positions in separate overloads:

```text
/bot [command: string] [topic: string]
/bot (ping|list|help)<command: EndbotRoot>
/bot <command: string> <page: int>
/bot <name: string> <operation: string>
/bot <name: string> <operation: string> <arguments: message>
```

The two `<name>` string overloads are the parseability layer: they accept every 2-token and every longer named
form (numeric help pages use the required-int overload instead, since strings reject numbers). The typed enum
overloads below them — operations, movement direction, action modes, on/off, dimension aliases, block faces, and
`stack` — are the completion layer: they advertise native suggestions but never match a tailed input on real
BDS, so the string layer always catches those forms for the Python parser. A broad `<operation: message>`
fallback was rejected as the final shape because live smoke confirmed it absorbs all named-operation
suggestions; the string layers keep the operation position suggestible. Second-level completion (for example
directions after `/bot Alice move `) depends on client-side merging of the enum and message branches and still
needs in-client confirmation. The pinned Endstone public API does not expose supported dynamic completion for
Bot profile names, so names remain strings; use `/bot list` to discover them. Endbot does not use private
registry or raw soft-enum hooks.

## Live parser smoke (real BDS required)

Repository unit tests prove the Python parser and the declaration coverage model, but they cannot execute the
closed-source BDS overload matcher. After any change to `COMMAND_USAGES`, run each of these on the pinned
native BDS build and confirm it reaches the Python layer (a Bot response or an `Endbot:` usage message, never a
Bedrock syntax error):

```text
/bot
/bot ping
/bot help
/bot help 2
/bot help 3
/bot help move
/bot help teleport
/bot help dance
/bot Alice spawn
/bot Alice status
/bot Alice move forward
/bot Alice tp me
/bot Alice tp 100 64 -20 facing Steve
/bot Alice look at 1 64 2
/bot Alice attack interval 20
/bot Alice interact 1 64 2 up
/bot Alice hotbar 9
/bot Alice drop stack
/bot Alice stop
/bot Alice despawn
```

Also confirm native autocomplete still offers operation names after `/bot Alice ` and direction names after
`/bot Alice move `; if a fallback ever hides completion, parseability still wins and the tradeoff is recorded
here rather than fixed by removing the fallback.

Block breaking/mining is not part of this remediation. A future implementation must use the server-authoritative
`PlayerAuthInput` block-action/prediction sequence and BDS inventory/tool rules, not direct plugin block mutation.

## Operational states

- `connecting`: a requested session has not spawned yet;
- `online`: the protocol session is active;
- `reconnecting`: an explicit reconnect or bounded automatic retry is in progress;
- `offline`: desired state is intentionally offline;
- `failed`: retries were exhausted; status includes the last error.

One Bot's generation/reconnect timer and inputs are independent of every other Bot. An unexpected disconnect retains
the profile UUID and retries with exponential backoff capped at 30 seconds, stopping after the configured attempt limit.
Explicit reconnect and live rename wait for the old transport to close before replacement, so a new session cannot race
the old one. A changed NetherNet server identity fails closed and is reported in status; an operator must deliberately
remove the saved pin after verifying an intentional BDS identity rotation.
