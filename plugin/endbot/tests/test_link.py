"""The endbotd link end to end: core server and plugin adapter over real loopback sockets."""

import queue
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from uuid import UUID

from endbot_core.forms import BotListView, GroupTarget
from endbot_core.group_forms import GroupGuard
from endbot_core.i18n import LocalizedError
from endbot_core.link import (
    MAX_FRAME_BYTES,
    PROTOCOL,
    LinkError,
    LinkServer,
    encode,
    error_from_wire,
    registry_hash,
    token_matches,
)
from endbot_core.principal import Principal
from endbot_core.wire_views import encode_view
from plain_service import PlainCommandService
from test_command_service import FakeControl, FakeWorld

from endstone_endbot.dispatch import ServerThreadBridge
from endstone_endbot.link_adapter import PluginLink

TOKEN = "t" * 43
ALICE = "00000000-0000-4000-8000-0000000000a1"


def wait_for(condition, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return False


class ServerThread:
    """One thread standing in for the BDS server thread."""

    def __init__(self) -> None:
        self.tasks = queue.Queue()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def schedule(self, callback):
        self.tasks.put(callback)

    def _run(self):
        while (callback := self.tasks.get()) is not None:
            callback()

    def stop(self):
        self.tasks.put(None)


class FakeAttachment:
    def __init__(self, permission, value):
        self.permissions = {permission: value}

    def set_permission(self, permission, value):
        self.permissions[permission] = value


class FakePlayer:
    def __init__(self, player_id, name, xuid="2535400000000001", locale="en_US"):
        self.unique_id = UUID(player_id)
        self.name = name
        self.xuid = xuid
        self.locale = locale
        self.messages = []
        self.attachments = []
        self.command_updates = 0

    def send_message(self, message):
        self.messages.append(message)

    def add_attachment(self, plugin, permission, value):
        attachment = FakeAttachment(permission, value)
        self.attachments.append(attachment)
        return attachment

    def update_commands(self):
        self.command_updates += 1


class FakeConsole:
    def __init__(self):
        self.messages = []

    def send_message(self, message):
        self.messages.append(message)


class FakeServer:
    def __init__(self):
        self.players = {}
        self.command_sender = FakeConsole()

    @property
    def online_players(self):
        return list(self.players.values())

    def get_player(self, unique_id):
        return self.players.get(str(unique_id))


class RecordingWorld(FakeWorld):
    def __init__(self):
        super().__init__()
        self.observed = []

    def observe(self, identity_id):
        self.observed.append(identity_id)
        return {"identityId": identity_id, "position": (1.5, 64.0, 2.5)}

    def target(self, identity_id):
        raise LocalizedError("Unknown dimension: {dimension}", dimension="nether")


class LinkHarness(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.token_file = Path(self.directory.name) / "endbotd.token"
        self.token_file.write_text(TOKEN + "\n", encoding="utf-8")
        self.logs = []
        self.submissions = []
        self.service = PlainCommandService(FakeControl(), None)
        self.server_thread = ServerThread()
        self.addCleanup(self.server_thread.stop)
        self.bridge = ServerThreadBridge(self.server_thread.schedule)
        self.addCleanup(self.bridge.close)
        self.mc = FakeServer()
        self.world = RecordingWorld()
        self.views = []
        self.endbotd = self.start_endbotd()
        self.service.world = self.endbotd.world

    def log(self, side):
        return lambda level, message: self.logs.append((side, level, message))

    def start_endbotd(self, port=0, **overrides):
        options = {"version": "0.4.0", "config": {"language": "auto"}, "on_submit": self.on_submit,
                   "log": self.log("endbotd"), "world_timeout": 2.0}
        options.update(overrides)
        server = LinkServer("127.0.0.1", port, TOKEN, **options).start()
        self.addCleanup(server.stop)
        return server

    def on_submit(self, submission):
        self.submissions.append(submission)
        if submission.argv and submission.argv[0] == "record":
            return {"revision": list(submission.principal.authorization_revision or ())}
        result = self.service.execute(list(submission.argv), submission.principal)
        self.endbotd.deliver(submission.principal.player_id or "console", list(result.messages))
        return {"accepted": result.accepted, "code": result.code}

    def resolve_sender(self, principal):
        if principal.player_id is None:
            return self.mc.command_sender
        return self.mc.get_player(UUID(principal.player_id))

    def start_plugin(self, port=None, token_file=None, **overrides):
        options = {"host": "127.0.0.1", "port": port or self.endbotd.address[1],
                   "token_file": token_file or self.token_file, "plugin_version": "0.4.0", "plugin": object(),
                   "server": self.mc, "bridge": self.bridge, "world": self.world,
                   "resolve_sender": self.resolve_sender,
                   "show_view": lambda player, view: self.views.append((player.name, view)),
                   "log": self.log("plugin"), "call_deadline": 0.5, "retry": (0.05, 0.2)}
        options.update(overrides)
        link = PluginLink(**options).start()
        self.addCleanup(link.stop)
        return link

    def on_server_thread(self, callback):
        return self.bridge.call(callback, timeout=2)

    def join(self, link, player):
        def joined():
            self.mc.players[str(player.unique_id)] = player
            link.player_joined(player)

        self.on_server_thread(joined)

    def quit(self, link, player):
        def quit():
            self.mc.players.pop(str(player.unique_id), None)
            link.player_quit(player)

        self.on_server_thread(quit)

    def submit(self, link, sender, *argv):
        return self.on_server_thread(lambda: link.submit("bot", sender, argv=list(argv))).result(5)

    def linked(self):
        link = self.start_plugin()
        self.assertTrue(wait_for(lambda: link.connected and self.endbotd.connected))
        return link

    def assert_token_never_logged(self):
        self.assertFalse([entry for entry in self.logs if TOKEN in entry[2]])


class HandshakeTests(LinkHarness):
    def test_link_opens_with_a_snapshot_under_a_new_epoch(self):
        self.mc.players[ALICE] = FakePlayer(ALICE, "Alice")
        link = self.linked()
        self.assertTrue(wait_for(lambda: self.endbotd.presence.player(ALICE) is not None))
        self.assertEqual(self.endbotd.presence.epoch, link.epoch)
        self.assertEqual(self.endbotd.presence.session_revision(ALICE), (link.epoch, 1))
        self.assertEqual(self.endbotd.presence.player(ALICE)["name"], "Alice")
        self.assertEqual(link.config, {"language": "auto"})
        self.assert_token_never_logged()

    def test_wrong_token_is_refused_and_never_logged(self):
        wrong = Path(self.directory.name) / "wrong.token"
        wrong.write_text("w" * 43, encoding="utf-8")
        link = self.start_plugin(token_file=wrong)
        self.assertTrue(wait_for(
            lambda: any("unauthorized" in entry[2] for entry in self.logs if entry[0] == "plugin")))
        self.assertFalse(self.endbotd.connected)
        self.assertFalse(link.connected)
        self.assertFalse([entry for entry in self.logs if "w" * 43 in entry[2]])
        self.assert_token_never_logged()

    def test_protocol_and_registry_mismatches_refuse_the_link(self):
        for field, value, code in (("protocol", PROTOCOL + 1, "protocol_mismatch"),
                                   ("registryHash", "0" * 64, "registry_mismatch")):
            with self.subTest(field=field):
                hello = {"protocol": PROTOCOL, "pluginVersion": "0.4.0", "registryHash": registry_hash(),
                         "epoch": "e", "token": TOKEN, field: value}
                with socket.create_connection(self.endbotd.address, 2) as sock:
                    sock.sendall(encode({"id": 0, "type": "hello", "body": hello}))
                    reply = sock.makefile("rb").readline()
                self.assertIn(f'"code":"{code}"'.encode(), reply)
                self.assertFalse(self.endbotd.connected)

    def test_a_link_that_does_not_open_with_hello_is_refused(self):
        with socket.create_connection(self.endbotd.address, 2) as sock:
            sock.sendall(encode({"id": 0, "type": "submit", "body": {"root": "bot"}}))
            self.assertIn(b'"code":"protocol"', sock.makefile("rb").readline())

    def test_both_ends_reject_non_loopback_hosts(self):
        with self.assertRaises(ValueError):
            LinkServer("0.0.0.0", 0, TOKEN, version="0", config={}, on_submit=lambda submission: None)
        with self.assertRaises(ValueError):
            self.start_plugin(host="192.168.1.2")

    def test_tokens_compare_exactly(self):
        self.assertTrue(token_matches(TOKEN, TOKEN))
        self.assertFalse(token_matches(TOKEN, TOKEN[:-1]))
        self.assertFalse(token_matches(TOKEN, None))

    def test_frames_over_one_mebibyte_are_rejected_and_drop_the_link(self):
        with self.assertRaises(LinkError) as raised:
            encode({"id": 1, "type": "world", "body": {"padding": "x" * MAX_FRAME_BYTES}})
        self.assertEqual(raised.exception.code, "too_large")
        link = self.linked()
        epoch = link.epoch
        with socket.create_connection(self.endbotd.address, 2) as sock:
            sock.sendall(b"x" * (MAX_FRAME_BYTES + 1))
            reader = sock.makefile("rb")
            self.assertIn(b'"code":"protocol"', reader.readline())
            try:
                self.assertEqual(reader.read(), b"")
            except ConnectionResetError:
                pass  # Closing with the oversized frame unread resets the connection.
        self.assertEqual(self.endbotd.presence.epoch, epoch)

    def test_reconnect_brings_a_new_epoch_and_a_fresh_snapshot(self):
        self.mc.players[ALICE] = FakePlayer(ALICE, "Alice")
        link = self.linked()
        first = link.epoch
        self.endbotd.drop_link("test")
        self.assertTrue(wait_for(lambda: link.connected and link.epoch != first and self.endbotd.connected))
        self.assertTrue(wait_for(lambda: self.endbotd.presence.session_revision(ALICE) == (link.epoch, 1)))

    def test_without_a_link_world_calls_and_submits_fail_as_unavailable(self):
        with self.assertRaises(LinkError) as raised:
            self.endbotd.world.observe("bot")
        self.assertEqual(raised.exception.code, "unavailable")
        link = self.start_plugin(port=1)
        with self.assertRaises(LinkError) as raised:
            link.submit("bot", Principal("console"), argv=["list"])
        self.assertEqual(raised.exception.code, "unavailable")


class PresenceAndCommandTests(LinkHarness):
    def test_presence_and_commands_keep_stream_order(self):
        link = self.linked()
        alice = FakePlayer(ALICE, "Alice")
        sender = Principal("player", player_id=ALICE, language="ja")

        self.join(link, alice)
        joined = self.submit(link, sender, "record")
        self.quit(link, alice)
        after_quit = self.submit(link, sender, "record")
        self.join(link, alice)
        rejoined = self.submit(link, sender, "record")

        self.assertEqual(joined, {"revision": [link.epoch, 1]})
        self.assertEqual(after_quit, {"revision": []})
        self.assertEqual(rejoined, {"revision": [link.epoch, 2]})
        principal = self.submissions[0].principal
        self.assertEqual((principal.kind, principal.name, principal.xuid, principal.language),
                         ("player", "Alice", "2535400000000001", "ja"))

    def test_endbotd_bumps_a_session_revision(self):
        link = self.linked()
        self.join(link, FakePlayer(ALICE, "Alice"))
        self.assertTrue(wait_for(lambda: self.endbotd.presence.player(ALICE) is not None))
        self.endbotd.presence.bump(ALICE)
        self.assertEqual(self.endbotd.presence.session_revision(ALICE), (link.epoch, 2))

    def test_a_command_runs_in_the_core_and_answers_through_world_calls_and_delivery(self):
        link = self.linked()
        alice = FakePlayer(ALICE, "Alice")
        self.join(link, alice)
        answer = self.submit(link, Principal("player", player_id=ALICE), "Steve", "spawn")
        self.assertEqual(answer, {"accepted": True, "code": "ok"})
        self.assertEqual(len(self.world.placements), 1)
        _identity, placement = self.world.placements[0]
        self.assertEqual(placement["coordinates"], [1, 2, 3])
        self.assertTrue(alice.messages)
        self.assertIn("Steve", alice.messages[0])

    def test_console_delivery_strips_colors(self):
        self.linked()
        self.assertTrue(self.endbotd.deliver("console", ["§aready"]))
        self.assertEqual(self.mc.command_sender.messages, ["ready"])

    def test_delivery_to_an_offline_player_reports_it(self):
        self.linked()
        self.assertFalse(self.endbotd.deliver(ALICE, ["hello"]))

    def test_delivery_shows_a_view(self):
        link = self.linked()
        alice = FakePlayer(ALICE, "Alice")
        self.join(link, alice)
        view = BotListView(("Steve",), (GroupTarget("miners", 2, GroupGuard("g1", "miners")),), True)
        self.assertTrue(self.endbotd.deliver(ALICE, ["§aok"], view=encode_view(view)))
        self.assertEqual(alice.messages, ["§aok"])
        self.assertEqual(self.views, [("Alice", view)])
        with self.assertRaisesRegex(ValueError, "invalid view"):
            self.endbotd.deliver(ALICE, [], view={"$view": "builtins.object", "fields": {}})

    def test_permission_sets_and_clears_the_control_attachment(self):
        link = self.linked()
        alice = FakePlayer(ALICE, "Alice")
        self.join(link, alice)
        self.assertTrue(self.endbotd.permission(ALICE, True))
        self.assertTrue(self.endbotd.permission(ALICE, False))
        self.assertEqual(len(alice.attachments), 1)
        self.assertEqual(alice.attachments[0].permissions, {"endbot.command.control": False})
        self.assertEqual(alice.command_updates, 2)
        self.quit(link, alice)
        self.assertFalse(self.endbotd.permission(ALICE, True))


class WorldCallTests(LinkHarness):
    def test_results_and_principals_cross_the_link(self):
        self.linked()
        self.assertEqual(self.endbotd.world.observe("bot-1"),
                         {"identityId": "bot-1", "position": [1.5, 64.0, 2.5]})
        self.assertEqual(self.endbotd.world.resolve_spawn_placement({"dimension": "x"}, Principal("console")),
                         {"dimension": "x", "resolved": True})

    def test_world_failures_keep_their_exception_class(self):
        self.linked()
        with self.assertRaises(ValueError) as raised:
            self.endbotd.world.inventory("Steve")
        self.assertEqual(str(raised.exception), "Bot is not present in the world")
        with self.assertRaises(LocalizedError) as localized:
            self.endbotd.world.target("bot-1")
        self.assertEqual(localized.exception.template, "Unknown dimension: {dimension}")
        self.assertEqual(localized.exception.params, {"dimension": "nether"})

    def test_mining_batch_round_trips_with_a_per_item_error(self):
        self.linked()

        def resolve(identity_id, parameters, sender):
            if identity_id == "bad":
                raise LocalizedError("No {block} in sight", block="stone")
            return {"blockPosition": [1, 64, 2], "identityId": identity_id}

        self.world.resolve_mining = resolve
        results = self.endbotd.world.resolve_mining_batch([
            {"identityId": "ok", "blockType": None},
            {"identityId": "bad", "blockType": None},
        ])
        self.assertEqual(results[0], {"target": {"blockPosition": [1, 64, 2], "identityId": "ok"}})
        error = error_from_wire(results[1]["error"])
        self.assertIsInstance(error, LocalizedError)
        self.assertEqual((error.template, error.params), ("No {block} in sight", {"block": "stone"}))

    def test_unknown_world_methods_are_neither_sent_nor_served(self):
        self.linked()
        self.assertFalse(hasattr(self.endbotd.world, "shutdown"))
        with self.assertRaises(LinkError) as raised:
            self.endbotd.request("world", {"method": "__init__", "args": [], "kwargs": {}}, 2)
        self.assertEqual(raised.exception.code, "unsupported")

    def test_a_stuck_tick_fails_one_call_within_the_deadline(self):
        self.linked()
        release = threading.Event()
        self.server_thread.schedule(lambda: release.wait(5))
        started = time.monotonic()
        with self.assertRaises(LinkError) as raised:
            self.endbotd.world.observe("stuck")
        self.assertEqual(raised.exception.code, "timeout")
        self.assertLess(time.monotonic() - started, 1.5)
        release.set()
        self.assertEqual(self.endbotd.world.observe("after")["identityId"], "after")
        self.assertEqual(self.world.observed, ["after"])
        self.assertTrue(self.endbotd.connected)


if __name__ == "__main__":
    unittest.main()
