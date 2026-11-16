# Event guarantees: what was measured

Each event kind names a section of this document in its registry `guarantee.record`, and a registry test requires
that section to exist. The sections say where each kind comes from, what it guarantees and what that rests on. The
unit tests prove the delivery path, not what BDS actually does; these are the live measurements and checks.

Everything here was measured on Endstone `0.11.15+endbot.1` / BDS `1.26.60.6` (protocol 2225), Windows 11, NetherNet
with direct signalling. The Bot-driven measurements ran on 2026-11-08 JST and the checks with a person in the 0.7.0
release pass on 2026-11-11 and 2026-11-12 JST ([what the release pass checked](COMPATIBILITY.md#events-watches-and-triggers)). They do not carry over to another Endstone/BDS pair.

## Block changes

**Measured with Bots** ([EVENTS.md S9](EVENTS.md#settled-points)): a Bot's break and place are real player actions on
BDS.

Driver: `scripts/raknet_spike.py --block-events` with the opt-in probe `scripts/block_events_probe.py` (never
installed in the product). The probe registers MONITOR listeners for every block, explosion, chat, command, death,
join and quit event the locked Endstone has, reads every watched position once per tick (about 1700 positions),
runs a timed scene, and logs both streams with the tick. Raw logs stay in the ignored output directory. The evidence
below comes from eight runs; the scene section of every run completed.

### What the locked Endstone offers

Block events: `BlockBreakEvent`, `BlockPlaceEvent`, `BlockPistonExtendEvent`, `BlockPistonRetractEvent`,
`BlockFromToEvent` (liquid spread), `BlockGrowEvent`, `BlockFormEvent`, `LeavesDecayEvent`, `BlockExplodeEvent`,
`ActorExplodeEvent` (with the destroyed blocks), `BlockCookEvent`. There is **no** event for redstone or block state
changes, burning, falling blocks, fading or commands and plugins editing the world. Reading an arbitrary position is
`Dimension.get_block_at(x, y, z)`; `block.data.runtime_id` identifies the full state.

### Observations

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

### Cost and unloaded chunks

- Reading 1000 positions (`get_block_at` + `runtime_id`) on the server thread took a median of 2.1 to 3.6 ms per pass
  across runs (max 5.3 ms); 4096 positions 8.4 to 14.2 ms (max 17.3 ms). A full poll pass over about 1700 watched
  positions, including reading the type and states of changed ones, took a median of 4.7 to 6.2 ms, p99 under 10 ms.
- Reading a position in an **unloaded** chunk does not fail: it returns `minecraft:air` with a valid runtime id.
  An unloaded read is therefore indistinguishable from air; the source must check `Dimension.loaded_chunks` and skip
  positions whose chunk is not loaded, keeping their baseline until it loads again.

### Values the registry uses

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
- Not measured: pistons (a `setblock`-placed piston did not move) and liquids placed by commands (they did not flow).
  The poll covers them. A human breaking and placing was checked in the 0.7.0 release pass: `player-break` and
  `player-place` arrived with no player name in the payload, and a block falling by physics and bamboo growing arrived
  as `poll`.

## Presence and chat

- `player.join` and `player.leave` come from Endstone's `PlayerJoinEvent` and `PlayerQuitEvent`. Both were raised for a
  Bot's despawn and resume; human players raise the same events. Bots are left out (they have the `bot.*` kinds).
- `player.chat` comes from `PlayerChatEvent` at MONITOR, with chat cancelled by another Endstone plugin ignored and a
  chat BDS targets at only some players dropped. A Bot cannot send public chat, so it could only be checked with a
  person. `/tell` and `/me` performed as a Bot (`Player.perform_command`) raised neither `PlayerChatEvent` nor
  `PlayerCommandEvent`: such commands are not chat events.
- `player.chat` is off unless the operator sets `[events] chat = true` (because of the known
  limit below); a watch or trigger on it is refused on other servers with the setting named.
- Known limit: on the locked pair, Endstone raises `PlayerChatEvent` before BDS runs the Script API's
  `chatSend` before-event and its chat filter, so a message a behavior pack mutes, or BDS rejects, may still reach a
  `player.chat` watch or trigger although no player saw it.

Checked in the 0.7.0 release pass ([what the release pass checked](COMPATIBILITY.md#events-watches-and-triggers)): a human joining and leaving, public chat delivered once, and
`/tell`, `/msg` and `/w` never delivered. A chat muted by a behavior pack (the known limit above) was not run.

## Bot death

`bot.died` comes from Endstone's `PlayerDeathEvent`; the plugin reports it for Bots only, never for a human player.
The event was raised for `kill` and for drowning, with a translatable message (`death.attack.generic`, the drowning
key), which carries no other player's name or position. The 0.7.0 release pass saw `death.attack.player` and
`death.attack.mob`.

## Runtime and server kinds

These kinds are raised by endbotd, not by the plugin, so their guarantees were checked by a person on the locked Endstone/BDS pair, in the
0.7.0 release pass.

- `bot.online`: endbotd polls `endbot_core.control.bot_profiles` once a second while a watch or trigger wants
  `bot.online` or `bot.offline`. The first poll after interest appears is the baseline; a Bot whose
  `connectionState` becomes `online` raises one occurrence. No occurrence is replayed for a transition that
  happened while nobody was watching.
  The kind is a sample, not a feed: `guarantee` is `persist` for 40 ticks (a state that lasts two seconds is
  delivered, a shorter connect and disconnect may be missed), and a watch or trigger made after the last sample
  does not receive a change that may predate it. A failed poll is a gap (`source restarted`) for every
  `bot.online` and `bot.offline` watch and trigger.
- `bot.offline`: the same poll, when a Bot that was `online` is no longer `online`. `reason` is the new
  `connectionState` (`offline`, `connecting`, `reconnecting`, ...), or `removed` when the profile is gone.
- `job.finished`: raised when the command service publishes a `job.ended` Event. `bot` is the Event's Bot name,
  `job` the job kind (the only job Events published today are mining jobs, reported as `mining`), and `reason` the
  Event's reason rendered in English.
- `server.started`: raised on the first link of a plugin process: the plugin makes one boot id per enable and says
  in its `hello` whether this is that process's first link, so a reconnect, or endbotd restarting under a running
  BDS, raises nothing, while a restarted BDS (a new plugin process) raises once.

Checked in the 0.7.0 release pass ([what the release pass checked](COMPATIBILITY.md#events-watches-and-triggers)): a Bot connecting and disconnecting, a mining job ending, and a BDS
restart raising `server.started` once while an endbotd restart raised nothing. A profile removed while its Bot is
online (`reason=removed`) was not run.

## Schedule and world time

- `schedule`: raised inside endbotd by its wall-clock timer, not by the plugin or the runtime. Each watch or
  trigger names `every` (an integer and `s`, `m` or `h`, from 10s to 24h, counted from the moment the subscription
  was made) or `at` (`HH:MM` in endbotd's local time, fired once a day), or both; the occurrence carries the fired
  local time in `at`. The timer only follows subscriptions that exist, and `schedule` occurrences are still raised
  with the web enabled and BDS stopped (EVENTS.md S3): the trigger engine skips and counts those starts while the
  link is down, and watches still receive them.
- `world.time`: raised by the plugin's poll of `level.time` on the server thread. While `world.time` is in the
  interest set's `kinds`, the plugin reads the level time every 20 ticks and reports a phase change (`day`,
  `sunset`, `night` or `sunrise`) with the time of day (`level.time % 24000`). The first read after interest
  appears is the baseline, so a watch opened mid-phase sees only later changes; phases last at least 1000 ticks,
  well over the poll period.

Checked in the 0.7.0 release pass ([what the release pass checked](COMPATIBILITY.md#events-watches-and-triggers)): `every` and `at` firing on time, a `schedule` trigger skipped and
counted while BDS was stopped, and `world.time` phase changes within 20 ticks of their boundary.
