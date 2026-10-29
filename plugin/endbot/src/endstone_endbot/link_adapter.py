"""The plugin's end of the endbotd link (docs/ENDBOTD.md P2, P4).

The plugin dials out and reconnects on its own. It reports presence (a
snapshot on every connect with a new epoch, then joins, quits and locale
changes), forwards commands with ``submit`` and answers endbotd's ``world``,
``deliver`` and ``permission`` requests on the server thread. The link's own
threads never touch Endstone objects.

Presence reports and the snapshot are queued from the server thread, so they
reach endbotd in the order the server saw them. Nothing here is wired into the
plugin yet: the product still calls the core in process until the switch
(step 3c).
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from pathlib import Path
from uuid import UUID

from endbot_core.commands import strip_colors
from endbot_core.link import (
    PROTOCOL,
    WORLD_METHODS,
    LinkConnection,
    LinkError,
    connect,
    decode_value,
    load_token,
    registry_hash,
    require_loopback,
)
from endbot_core.principal import Principal

from endstone_endbot.dispatch import ServerThreadBridge, ServerThreadWorld

CONTROL_PERMISSION = "endbot.command.control"


class _DeadlineBridge:
    """A ServerThreadBridge whose every call carries the link's per-call deadline."""

    def __init__(self, bridge: ServerThreadBridge, deadline: float) -> None:
        self._bridge = bridge
        self._deadline = deadline

    def call(self, callback):
        return self._bridge.call(callback, timeout=self._deadline)


def _player_record(player) -> dict:
    record = {"playerId": str(player.unique_id), "name": str(player.name), "xuid": str(player.xuid)}
    try:
        record["locale"] = str(player.locale)
    except Exception:  # noqa: BLE001 - a missing locale must not hide the player
        record["locale"] = None
    return record


class PluginLink:
    """Keep one link to endbotd open and serve it.

    ``call_deadline`` bounds every server-thread call made for endbotd; keep it
    below endbotd's own request deadline so a stuck tick fails one call.
    """

    def __init__(
        self,
        *,
        host: str,
        port: int,
        token_file: Path,
        plugin_version: str,
        plugin,
        server,
        bridge: ServerThreadBridge,
        world,
        resolve_sender: Callable[[Principal], object],
        show_view: Callable[[object, dict], None] | None = None,
        log: Callable[[str, str], None] | None = None,
        call_deadline: float = 5.0,
        retry: tuple[float, float] = (1.0, 30.0),
    ) -> None:
        self._host = require_loopback(host)
        self._port = port
        self._token_file = token_file
        self._plugin_version = plugin_version
        self._plugin = plugin
        self._server = server
        self._bridge = bridge
        self._world = ServerThreadWorld(world, _DeadlineBridge(bridge, call_deadline), resolve_sender)
        self._show_view = show_view
        self._log = log or (lambda level, message: None)
        self._deadline = call_deadline
        self._retry = retry
        # Reentrant: a send that overflows closes the link, whose callback takes this lock.
        self._lock = threading.RLock()
        self._live: LinkConnection | None = None
        self._stopping = threading.Event()
        self._down = threading.Event()
        self._down.set()
        self._thread: threading.Thread | None = None
        self._last_failure: str | None = None
        # Server thread only, like the plugin's own attachments today.
        self._attachments: dict[str, object] = {}
        self.epoch: str | None = None
        self.config: dict | None = None

    @property
    def connected(self) -> bool:
        with self._lock:
            return self._live is not None

    def start(self) -> PluginLink:
        self._thread = threading.Thread(target=self._run, name="endbot-link", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stopping.set()
        with self._lock:
            live, self._live = self._live, None
        if live is not None:
            live.close("plugin stopping")
        self._down.set()
        if self._thread is not None:
            self._thread.join(5)

    def _run(self) -> None:
        delay = self._retry[0]
        while not self._stopping.is_set():
            try:
                self._connect_once()
            except (LinkError, RuntimeError, TimeoutError) as error:
                failure = f"{getattr(error, 'code', type(error).__name__)}: {error}"
                if failure != self._last_failure:
                    self._log("warning", f"Endbot cannot link to endbotd ({failure})")
                    self._last_failure = failure
            else:
                self._last_failure = None
                delay = self._retry[0]
                self._down.wait()
            if self._stopping.wait(delay):
                return
            delay = min(delay * 2, self._retry[1])

    def _connect_once(self) -> None:
        epoch = uuid.uuid4().hex
        hello = {"protocol": PROTOCOL, "pluginVersion": self._plugin_version, "registryHash": registry_hash(),
                 "epoch": epoch, "token": load_token(self._token_file)}
        handlers = {"world": self._world_call, "deliver": self._deliver, "permission": self._permission}
        connection, result = connect(self._host, self._port, hello, handlers, concurrent=frozenset({"world"}),
                                     on_close=self._closed)
        try:
            self._bridge.call(lambda: self._go_live(connection, epoch), timeout=self._deadline)
        except BaseException:
            connection.close("presence snapshot failed")
            raise
        self.config = result.get("config") if isinstance(result.get("config"), dict) else {}
        self._log("info", f"Endbot linked to endbotd {result.get('endbotdVersion')}")

    def _go_live(self, connection: LinkConnection, epoch: str) -> None:
        """Server thread: read the snapshot and publish the link in one step,
        so no join or quit can fall between them."""
        snapshot = [_player_record(player) for player in self._server.online_players]
        with self._lock:
            if connection.closed or self._stopping.is_set():
                raise LinkError("unavailable", "link closed before the snapshot")
            self._live = connection
            self.epoch = epoch
            self._down.clear()
            self._report(connection, {"kind": "snapshot", "players": snapshot})

    def _closed(self, connection: LinkConnection, reason: str) -> None:
        with self._lock:
            if self._live is not connection:
                return
            self._live = None
        if not self._stopping.is_set():
            self._log("warning", f"Endbot link to endbotd closed: {reason}")
        self._down.set()

    def _report(self, connection: LinkConnection, body: dict) -> None:
        def failed(future) -> None:
            error = future.exception()
            if error is not None and getattr(error, "code", None) != "unavailable":
                self._log("warning", f"endbotd refused a presence report: {error}")

        connection.send("presence", body).add_done_callback(failed)

    def _presence(self, body: dict) -> None:
        with self._lock:
            if self._live is not None:
                self._report(self._live, body)

    # Server-thread entry points from the plugin's event handlers.

    def player_joined(self, player) -> None:
        self._presence({"kind": "join", "player": _player_record(player)})

    def player_quit(self, player) -> None:
        player_id = str(player.unique_id)
        self._attachments.pop(player_id, None)
        self._presence({"kind": "quit", "playerId": player_id})

    def locale_changed(self, player) -> None:
        record = _player_record(player)
        self._presence({"kind": "locale", "playerId": record["playerId"], "locale": record["locale"]})

    def submit(self, root: str, sender: Principal, *, argv: list[str] | None = None,
               operation: dict | None = None, guard: dict | None = None):
        """Forward a command line or form reply; returns a Future of endbotd's answer.

        Only who typed it travels; endbotd derives revisions from presence.
        """
        with self._lock:
            live = self._live
        if live is None:
            raise LinkError("unavailable", "Endbot service is restarting")
        wire = {"kind": sender.kind, "language": sender.language}
        if sender.player_id is not None:
            wire["playerId"] = sender.player_id
        if sender.origin is not None:
            wire["origin"] = sender.origin
        body = {"root": root, "sender": wire}
        if argv is not None:
            body["argv"] = list(argv)
        if operation is not None:
            body["operation"] = operation
        if guard is not None:
            body["guard"] = guard
        return live.send("submit", body)

    # Requests from endbotd. World calls run concurrently on link workers;
    # deliveries and permissions run in order. All reach Endstone only
    # through the bridge, each bounded by the per-call deadline.

    def _world_call(self, body: dict):
        method, args, kwargs = body.get("method"), body.get("args", []), body.get("kwargs", {})
        if method not in WORLD_METHODS:
            raise LinkError("unsupported", f"unknown world method {method!r}")
        if not isinstance(args, list) or not isinstance(kwargs, dict):
            raise LinkError("invalid", "world call needs args and kwargs")
        return getattr(self._world, method)(*decode_value(args), **decode_value(kwargs))

    def _deliver(self, body: dict) -> dict:
        to, lines, view = body.get("to"), body.get("lines"), body.get("view")
        if not isinstance(to, str) or not isinstance(lines, list) or not all(isinstance(line, str) for line in lines):
            raise LinkError("invalid", "deliver needs to and lines")
        if view is not None and (not isinstance(view, dict) or to == "console"):
            raise LinkError("invalid", "a view goes to a player as an object")
        if view is not None and self._show_view is None:
            raise LinkError("unsupported", "this plugin cannot show views")
        return {"delivered": self._bridge.call(lambda: self._deliver_now(to, lines, view), timeout=self._deadline)}

    def _deliver_now(self, to: str, lines: list[str], view: dict | None) -> bool:
        if to == "console":
            console = self._server.command_sender
            for line in lines:
                # The console stream is also the server log operators read as text.
                console.send_message(strip_colors(line))
            return True
        player = self._server.get_player(UUID(to))
        if player is None:
            return False
        for line in lines:
            player.send_message(line)
        if view is not None:
            self._show_view(player, view)
        return True

    def _permission(self, body: dict) -> dict:
        player_id, allowed = body.get("playerId"), body.get("allowed")
        if not isinstance(player_id, str) or not isinstance(allowed, bool):
            raise LinkError("invalid", "permission needs playerId and allowed")
        return {"applied": self._bridge.call(lambda: self._apply_permission(player_id, allowed),
                                             timeout=self._deadline)}

    def _apply_permission(self, player_id: str, allowed: bool) -> bool:
        player = self._server.get_player(UUID(player_id))
        if player is None:
            return False
        attachment = self._attachments.get(player_id)
        if attachment is not None:
            attachment.set_permission(CONTROL_PERMISSION, allowed)
        elif allowed:
            attachment = player.add_attachment(self._plugin, CONTROL_PERMISSION, True)
            if attachment is not None:
                self._attachments[player_id] = attachment
        player.update_commands()
        return True
