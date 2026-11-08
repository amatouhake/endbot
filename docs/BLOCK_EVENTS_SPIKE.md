# Block change coverage on the locked pair (BOT-022 step 1)

Measured 2026-11-08 JST on Endstone `0.11.15+endbot.1` / BDS `1.26.60.6` (protocol 2225), Windows 11, one PC,
NetherNet with direct signalling, in the disposable smoke world. **Measured with Bots, not a human**
([EVENTS.md S9](EVENTS.md#settled-points)): a Bot's break and place are real player actions on BDS. Human break and
place, public chat and a human `/tell` are checked in the release pass.

Driver: `scripts/raknet_spike.py --block-events` with the opt-in probe `scripts/block_events_probe.py` (never
installed in the product). The probe registers MONITOR listeners for every block, explosion, chat, command, death,
join and quit event the locked Endstone has, reads every watched position once per tick (about 1700 positions),
runs a timed scene, and logs both streams with the tick. Raw logs stay in the ignored output directory. The evidence
below comes from eight runs (`build/2026-11-08-block-events-1` to `-8`); the scene section of every run completed.

## What the locked Endstone offers

Block events: `BlockBreakEvent`, `BlockPlaceEvent`, `BlockPistonExtendEvent`, `BlockPistonRetractEvent`,
`BlockFromToEvent` (liquid spread), `BlockGrowEvent`, `BlockFormEvent`, `LeavesDecayEvent`, `BlockExplodeEvent`,
`ActorExplodeEvent` (with the destroyed blocks), `BlockCookEvent`. There is **no** event for redstone or block state
changes, burning, falling blocks, fading or commands and plugins editing the world. Reading an arbitrary position is
`Dimension.get_block_at(x, y, z)`; `block.data.runtime_id` identifies the full state.

## Observations

| Cause | Event | What the per-tick read saw |
| --- | --- | --- |
| Bot breaks a block | `BlockBreakEvent` (player set, not cancelled) on the tick the client finished | the next tick, as the next-tick confirm |
| Bot places a block (`interact` with a block held) | `BlockPlaceEvent` with the placed state | the next tick, as the confirm |
| Bot empties a water bucket | none for the bucket itself | the water source the tick after use |
| Water spreading | `BlockFromToEvent` for 26 of 27 newly wet cells, always one tick **before** the world showed it; none for the 29 later depth changes | every change |
| Liquids placed by `setblock` (scene, three runs) | none | the source block only: such liquids never flowed, also with a ticking area and with the probe's FromTo and piston listeners removed |
| Sticky piston placed and powered by `setblock` | none | it never extended; pistons are therefore **not measured** |
| Redstone block next to a door (`setblock`) | none | `open_bit` flipped 3 ticks later, and back after removal |
| Sand falling | none | sand vanished 2 ticks after placement and appeared 14 ticks later at the bottom |
| Fire on planks | none | fire and planks turning to air over the following seconds |
| Leaves without logs (random tick speed 300) | `LeavesDecayEvent` once, matching the one change | the decay |
| Wheat growing (random tick speed 300) | `BlockGrowEvent` **never** (0 of 8 growth stages) | every stage |
| TNT lit by a redstone block | `ActorExplodeEvent` listing the 8 destroyed dirt blocks | the 8 blocks, plus the TNT block turning to air at ignition with no event |
| A block changed and restored after 1, 2, 4 and 8 ticks (plugin edit) | none | each state for exactly that many reads, twice per length |

## Cost and unloaded chunks

- Reading 1000 positions (`get_block_at` + `runtime_id`) on the server thread took a median of 2.1 to 3.6 ms per pass
  across runs (max 5.3 ms); 4096 positions 8.4 to 14.2 ms (max 17.3 ms). A full poll pass over about 1700 watched
  positions, including reading the type and states of changed ones, took a median of 4.7 to 6.2 ms, p99 under 10 ms.
- Reading a position in an **unloaded** chunk does not fail: it returns `minecraft:air` with a valid runtime id.
  An unloaded read is therefore indistinguishable from air; the source must check `Dimension.loaded_chunks` and skip
  positions whose chunk is not loaded, keeping their baseline until it loads again.

## Presence, chat and death

- `PlayerJoinEvent` and `PlayerQuitEvent` were raised for the Bot's despawn and resume.
- `PlayerDeathEvent` was raised for `kill` and for drowning, with a translatable message (`death.attack.generic`,
  the drowning key), which carries no other player's name or position.
- A Bot cannot send public chat, so `PlayerChatEvent` was **not measured**. `/tell` and `/me` performed as the Bot
  (`Player.perform_command`) raised neither `PlayerChatEvent` nor `PlayerCommandEvent`.

## Result

These values set the `block.change` registry entry and the `[events]` defaults (EVENTS.md E2, E3):

- **`block-poll-ticks` = 4.** Every watched position is read once every 4 ticks, a quarter of them on each tick, so
  the cost is spread evenly. A read sees a state for exactly as many ticks as it lasts, so a state that lasts at least
  4 ticks is always read once; shorter states may be missed. **`guarantee.ticks` = 4.**
- **`guarantee.each` = `player-break`, `player-place`.** Both were delivered by an event on the tick of the action and
  confirmed by the next-tick read. Explosions, leaf decay and liquid spread raised events too, but either did not
  cover every change (liquid depth, the TNT block itself) or were seen once; they are reported by the poll as `poll`.
  A later measurement may add them to `each`.
- **`max-watched-blocks` = 2048** by default: a quarter (512 positions) per tick costs about 1 to 2 ms on this PC.
  Operators may raise it; the cost grows by about 1 ms per tick for every 2000 more positions.
- **Liquid spread events arrive one tick before the world changes**, which the next-tick confirm (E3) already handles.
- **Unloaded chunks are skipped by the poll**, and a difference found when the chunk loads again is reported late with
  cause `poll`.
- **`player.join`, `player.leave` and death** have usable events. **`player.chat`** keeps public chat only and is
  verified with a human in the release pass; commands such as `/tell` are not chat events.
- Not measured: pistons (a `setblock`-placed piston did not move), liquids placed by commands (they did not flow),
  and anything a human does. These are covered by the poll and by the release pass.
