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

`/bot`, `/bot help`, and `/bot help advanced` provide progressive help. `/bot list` summarizes every known profile and
its lifecycle state. `/bot Alice status` combines runtime state with authoritative dimension/position/rotation when the
BDS player entity is present.

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

The command declaration supplies native finite enums for roots, movement direction, action modes, on/off, dimension
aliases, block faces, and `stack`, while retaining string fallbacks required by the pinned Endstone/BDS overload parser
for zero-argument/default forms. The pinned Endstone public API does not expose supported dynamic completion for Bot
profile names, so names remain strings; use `/bot list` to discover them. Endbot does not use private registry or raw
soft-enum hooks.

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
