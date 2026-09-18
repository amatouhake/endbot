# Endbot Endstone plugin

M1 registers the permission-protected `/bot ping` command and returns `Endbot: pong`. Command parsing is separated
from the Endstone adapter so future bot actions do not accumulate in one handler.

The `endbot.command.control` permission defaults to false. On player join, the plugin grants only that node to UUIDs or
XUIDs explicitly listed in its `config.toml`; an empty allowlist grants nobody. Endbot does not enable cheats, make the
player an operator, or grant vanilla command privileges.
