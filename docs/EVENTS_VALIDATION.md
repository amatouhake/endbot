# Event kind validation records

Each event kind names a validation record in its registry `guarantee.record`. The records here are the live
checks that back a listed guarantee; the unit tests prove the delivery path, not what BDS actually does.

## Presence and chat

- `player.join` and `player.leave` come from Endstone's `PlayerJoinEvent` and `PlayerQuitEvent`, measured with a Bot
  in the step 1 spike ([record](BLOCK_EVENTS_SPIKE.md#presence-chat-and-death)); human players raise the same events.
  Bots are left out (they have the `bot.*` kinds).
- `player.chat` comes from `PlayerChatEvent` at MONITOR, with chat cancelled by another Endstone plugin ignored and a
  chat BDS targets at only some players dropped. Bots cannot chat, so it has **not been measured**: the guarantee is
  stated from the event's contract and is checked with a human in the release pass.
- Known limit: on the locked pair, Endstone raises `PlayerChatEvent` before BDS runs the Script API's
  `chatSend` before-event and its chat filter, so a message a behavior pack mutes, or BDS rejects, may still reach a
  `player.chat` watch or trigger although no player saw it.

### Not yet run

A human joining and leaving; a human's public chat delivered once; a `/tell`, `/msg` and `/w` never delivered; a
chat muted by a behavior pack (the known limit above).
