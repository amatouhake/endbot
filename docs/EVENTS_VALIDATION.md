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
- `server.started`: raised on the first plugin `hello` whose `boot` differs from the last boot endbotd saw. The
  plugin makes one boot id per enable, so a reconnect of the same BDS process raises nothing, while a restarted BDS
  (a new plugin process) raises once.

### Not yet run

The live check for these four kinds is part of the release pass and has not been run yet. It covers: a Bot
connecting and disconnecting while a watch is open; a profile removed while its Bot is online; a mining job ending
while a watch is open; and a BDS restart raising `server.started` exactly once with a reconnect raising nothing.

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
