# Endbot Endstone plugin

The plugin is the world end of the endbotd link ([ENDBOTD.md](../../docs/ENDBOTD.md)). It registers the
permission-protected `/bot`, `/botgroup`, `/botscript` and console-only `/botcontrollers` commands, reports presence,
forwards command lines and form replies to endbotd, answers endbotd's world calls (authoritative BDS observation,
deferred spawn placement, teleport, block reads) on the BDS server thread, renders forms and delivers chat. It holds no
policy and no store, and it never talks to the runtime.

`endbot.command.control` defaults to false. endbotd decides who may control Bots and tells the plugin which players
get that node; bootstrap enrollment and persistent console-only `botcontrollers add|remove|list` manage access.
Revocations override bootstrap and legacy allowlists and invalidate queued commands. See
[runtime authorization](../../docs/OPERATIONS.md#runtime-grants-and-revocations). While endbotd is down every Endbot
command is refused and the last control permissions stay as they were. It does not enable cheats, grant OP, or grant
vanilla command privileges. The `[endbotd]` link host must be loopback and its token file must hold endbotd's token.
See [the command guide](../../docs/COMMANDS.md) for syntax and lifecycle semantics.
