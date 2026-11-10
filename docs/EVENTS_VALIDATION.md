# Event kind validation records

Each event kind names a validation record in its registry `guarantee.record`. The records here are the live
checks that back a listed guarantee; the unit tests prove the delivery path, not what BDS actually does.

## Runtime and server kinds

These kinds are raised by endbotd, not by the plugin, so their guarantees are measured in the combined BOT-021 and
BOT-022 release pass (EVENTS.md step 8) on the locked Endstone/BDS pair.

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

### Not yet run

The live check for these four kinds is part of the release pass and has not been run yet. It covers: a Bot
connecting and disconnecting while a watch is open; a profile removed while its Bot is online; a mining job ending
while a watch is open; and a BDS restart raising `server.started` exactly once with a reconnect raising nothing.

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

### Not yet run

The live checks for these two kinds belong to the combined BOT-021/BOT-022 release pass (EVENTS.md step 8) and
have not been run yet. They cover: `every` firing at its interval and `at` at its local minute while a watch and a
trigger are open; a `schedule` trigger being skipped and counted while BDS is stopped; and `world.time` reporting
each phase change within 20 ticks across a full day cycle.

## Presence and chat

- `player.join` and `player.leave` come from Endstone's `PlayerJoinEvent` and `PlayerQuitEvent`, measured with a Bot
  in the step 1 spike ([record](BLOCK_EVENTS_SPIKE.md#presence-chat-and-death)); human players raise the same events.
  Bots are left out (they have the `bot.*` kinds).
- `player.chat` comes from `PlayerChatEvent` at MONITOR, with chat cancelled by another Endstone plugin ignored and a
  chat BDS targets at only some players dropped. Bots cannot chat, so it has **not been measured**: the guarantee is
  stated from the event's contract and is checked with a human in the release pass.
- `player.chat` is off unless the operator sets `[events] chat = true` (decided 2026-11-08 JST because of the known
  limit below); a watch or trigger on it is refused on other servers with the setting named.
- Known limit: on the locked pair, Endstone raises `PlayerChatEvent` before BDS runs the Script API's
  `chatSend` before-event and its chat filter, so a message a behavior pack mutes, or BDS rejects, may still reach a
  `player.chat` watch or trigger although no player saw it.

### Not yet run

A human joining and leaving; a human's public chat delivered once; a `/tell`, `/msg` and `/w` never delivered; a
chat muted by a behavior pack (the known limit above).

## Bot-only live smoke

Run 2026-11-08 JST on Endstone `0.11.15+endbot.1` / BDS `1.26.60.6`, Windows 11, NetherNet with direct
signalling, in the disposable sandbox world, with Bots only (EVENTS.md S9): `scripts/raknet_spike.py --events`
(`scripts/events_probe.py`). Both runs below completed the whole smoke (`ok: true`).

| Check | Result |
| --- | --- |
| A Bot breaks a block a run watches | delivered once: `minecraft:dirt` to `minecraft:air`, `cause=player-break`, `bot` the Bot's name, `missed=0`; the next wait timed out |
| 81 cells change at once while the run sleeps | the watch kept the 64 newest and reported `missed=17` |
| The plugin's link drops and comes back | the waiting run got `ok=false, code=unavailable, reason=source restarted` once, then the next change after the relink |
| A watched area of 729 blocks | refused before anything was watched: `A watched area may hold at most 512 blocks; this one holds 729` |
| A console trigger on a watched block, a Bot breaks it | the script started with `a.event.to=minecraft:air`, `cause=player-break`, `missed=0` |
| endbotd killed and restarted | the trigger came back `armed` with its stats and started again for the next change (`cause=poll`) |
| `startsPerMinute=2`, `cooldown=0`, parallel, four changes in a few seconds | disabled with `rate limit`, logged for its owner |
| enable, remove, list | re-armed, removed, `No triggers` |

The start-cap row records the behavior before EVENTS.md S14 (2026-11-10): the cap now throttles instead of
disabling, which the release pass checks again.

Evidence: `build/2026-11-08-events-2` (watches) and `build/2026-11-08-triggers-2` (watches and triggers) in the
worktrees that ran them; raw logs stay out of the repository.

Not covered by the Bot-only smoke and left for the release pass (step 8): a human breaking and placing, human
`player.join`, `player.leave` and public `player.chat` (Bots cannot chat; a `/tell` must never be delivered), a
player-owned trigger and its revocation and regrant, a shared script growing its declaration under someone else's
trigger, the trigger forms and their declaration confirm in the Windows client, the disable notice in a player's chat,
the web trigger editor from a phone, `bot.online`/`bot.offline`/`bot.died`/`job.finished`/`server.started`,
`schedule` and `world.time` live, BDS restarted with watches open, update and rollback with `state/triggers.json`, and
achievement history.
