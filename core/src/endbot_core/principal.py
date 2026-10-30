"""Who an operation acts for, independent of the surface it arrived on.

Every entry (chat, forms, books, script runs, later the web) builds one
``Principal``; the service never sees an Endstone sender. Resolving senders and
delivering replies belong to the adapter. Authorization has two layers: every
principal except the console needs a grant at its captured grant revision, and
then each kind adds its own condition (see ``EndbotPlugin._authorize``).
"""

from __future__ import annotations

from dataclasses import dataclass, replace

# kind values: "player" (typed command or form), "console", "run" (a script
# run), "job" (work a Bot continues after its command returned, such as a
# continuous mining loop). "web" and "trigger" arrive with BOT-021 and BOT-022.
KINDS = frozenset({"player", "console", "run", "job"})
ROLES = frozenset({None, "scripter", "controller"})


@dataclass(frozen=True, slots=True)
class Principal:
    """A worker-safe snapshot of who acts; never holds a native Endstone object.

    Endstone player wrappers end when the player disconnects, so the adapter
    resolves ``player_id`` again on the server thread whenever it needs one.
    """

    kind: str = "console"
    name: str | None = None
    xuid: str | None = None
    grant_revision: int | None = None
    # Reply language, resolved on the server thread when the command arrives.
    language: str = "en"
    # Where a script run started: x, y, z, yaw, pitch and dimension.
    origin: dict | None = None
    # In-game identity (the player's UUID) used to resolve the online player.
    player_id: str | None = None
    # Session revision of an in-game player; logoff or a permission change bumps it.
    authorization_revision: int | None = None
    group_guard: object | None = None
    run_id: str | None = None
    # The controller's role (docs/PERMISSIONS.md), read when the command arrives: "scripter" or "controller".
    # None is a grant with no recorded role, which is a scripter (the migration rule).
    role: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"unknown principal kind: {self.kind}")
        if self.role not in ROLES:
            raise ValueError(f"unknown role: {self.role}")

    @property
    def console(self) -> bool:
        """The console, or a run or job the console started: always authorized."""
        return self.player_id is None and self.xuid is None

    @property
    def can_author(self) -> bool:
        """Create, save, import, copy, rename, restore and share scripts (S6): the console and scripters."""
        return self.console or self.role != "controller"


def as_job(principal):
    """The principal a job keeps once its command returned.

    A player's job outlives the player's session: it needs the owner's grant
    but not the in-game online condition. Runs and the console stay as they are.
    """
    if isinstance(principal, Principal) and principal.kind == "player":
        return replace(principal, kind="job")
    return principal
