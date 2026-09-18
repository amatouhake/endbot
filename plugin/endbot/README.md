# Endbot Endstone plugin

The plugin owns the permission-protected `/bot` command, authoritative BDS observation, deferred spawn placement, and
teleport through Endstone APIs. Parsing and domain routing are separate from the Endstone adapter and the authenticated
runtime transport.

`endbot.command.control` defaults to false. The plugin grants only that node to explicitly listed human UUIDs/XUIDs; it
does not enable cheats, grant OP, or grant vanilla command privileges. The runtime control host must be loopback and its
token file must point to the token created by the runtime. See [the command guide](../../docs/COMMANDS.md) for syntax
and lifecycle semantics.
