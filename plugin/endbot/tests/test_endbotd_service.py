"""endbotd's service and the plugin adapter through the real link, with fake Endstone objects."""

import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from endbot_core.forms import ActionScreen, BotListView, Button, Run
from endbot_core.group_forms import GroupGuard, GroupView
from endbot_core.link import principal_from_wire, principal_to_wire
from endbot_core.principal import Principal
from endbot_core.script_forms import ScriptGuard
from endbot_core.wire_views import decode_view, encode_view
from endbotd.config import EndbotdConfig
from endbotd.service import EndbotService
from test_command_service import FakeControl
from test_link import ALICE, TOKEN, FakePlayer, FakeServer, RecordingWorld, ServerThread, wait_for

from endstone_endbot.dispatch import ServerThreadBridge
from endstone_endbot.link_adapter import PluginLink

OWNER = "2535412345678901"
CORE = Path(__file__).resolve().parents[3] / "core" / "src"


class CapacityWorld(RecordingWorld):
    def capacity(self, bot_ids):
        return 10, 1


class ServiceHarness(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        (self.root / "endbotd.token").write_text(TOKEN + "\n", encoding="utf-8")
        self.control = FakeControl()
        self.logs = []
        self.start_service()
        self.server_thread = ServerThread()
        self.addCleanup(self.server_thread.stop)
        self.bridge = ServerThreadBridge(self.server_thread.schedule)
        self.addCleanup(self.bridge.close)
        self.mc = FakeServer()
        self.views = []
        self.configs = []

    def start_service(self, permissions=None):
        config = EndbotdConfig(
            base=self.root, link_port=0, link_token_file=self.root / "endbotd.token", stores_directory=self.root,
            scripts_folder=self.root / "scripts",
            authorization={"controller-policy-file": "controller-policy.json"},
            ui={"language": "en"}, hotbar={"hotbar-sync-interval-ticks": 2},
            permissions=permissions if permissions is not None else {},
        )
        self.service = EndbotService(config, control=self.control,
                                     log=lambda level, message: self.logs.append((level, message))).start()
        self.addCleanup(self.service.close)

    def link(self, **overrides):
        options = {"host": "127.0.0.1", "port": self.service.link.address[1], "token_file": self.root / "endbotd.token",
                   "plugin_version": "0.4.0", "plugin": object(), "server": self.mc, "bridge": self.bridge,
                   "world": CapacityWorld(), "resolve_sender": self.resolve_sender,
                   "show_view": lambda player, view: self.views.append((player.name, view)),
                   "on_config": self.configs.append, "call_deadline": 0.5, "retry": (0.05, 0.2)}
        options.update(overrides)
        link = PluginLink(**options).start()
        self.addCleanup(link.stop)
        self.assertTrue(wait_for(lambda: link.connected and self.service.link.connected))
        return link

    def resolve_sender(self, principal):
        if principal.player_id is None:
            return self.mc.command_sender
        return self.mc.get_player(principal.player_id)

    def call(self, callback):
        return self.bridge.call(callback, timeout=2)

    def join(self, link, player):
        def joined():
            self.mc.players[str(player.unique_id)] = player
            link.player_joined(player)

        self.call(joined)

    def submit(self, link, sender, root, *argv, guard=None):
        return self.call(lambda: link.submit(root, sender, argv=list(argv), guard=guard)).result(5)

    def console(self, link, *argv):
        return self.submit(link, Principal("console"), "botcontrollers", *argv)


class EndbotServiceTests(ServiceHarness):
    def test_hello_carries_the_language_and_hotbar_settings(self):
        self.link()
        self.assertEqual(self.configs, [{"language": "en", "hotbar": {"hotbar-sync-interval-ticks": 2}}])

    def test_only_granted_players_command_bots_and_grants_reach_the_attachment(self):
        link = self.link()
        alice = FakePlayer(ALICE, "Alice", xuid=OWNER)
        self.join(link, alice)
        alice_sender = Principal("player", player_id=ALICE)
        self.assertEqual(self.submit(link, alice_sender, "bot", "Steve", "spawn"),
                         {"accepted": False, "code": "unauthorized"})
        self.assertTrue(wait_for(lambda: alice.messages == ["Endbot: you do not have Bot control permission"]))
        self.assertEqual(self.console(link, "add", "Alice"), {"accepted": True, "code": "ok"})
        self.assertTrue(wait_for(lambda: alice.attachments
                                 and alice.attachments[0].permissions == {"endbot.command.control": True}))
        self.assertTrue(wait_for(lambda: any("controller Alice updated" in line
                                             for line in self.mc.command_sender.messages)))
        self.assertEqual(self.submit(link, alice_sender, "bot", "Steve", "spawn"), {"accepted": True, "code": "ok"})
        self.assertTrue(wait_for(lambda: any("Steve" in message for message in alice.messages[1:])))
        self.assertIn("spawn", [operation for operation, _ in self.control.calls])

    def test_each_command_carries_the_senders_current_role(self):
        link = self.link()
        self.join(link, FakePlayer(ALICE, "Alice", xuid=OWNER))
        self.console(link, "add", "Alice")
        sender = {"kind": "player", "playerId": ALICE}
        # A new grant without --role is a controller (least privilege).
        self.assertEqual(self.service.link._sender(sender).role, "controller")
        self.assertFalse(self.service.link._sender(sender).can_author)
        # S5: the role is read for each command, with no restart or grant revision change.
        self.service.authority.roles.set(OWNER, "scripter")
        principal = self.service.link._sender(sender)
        self.assertEqual((principal.role, principal.can_author), ("scripter", True))
        self.assertEqual(principal.grant_revision, 0)
        self.assertEqual(self.service.link._sender({"kind": "console"}).role, None)
        self.assertEqual(principal_from_wire(principal_to_wire(principal)).role, "scripter")
        with self.assertRaises(ValueError):
            Principal("player", player_id=ALICE, role="operator")

    def test_the_cli_path_passes_a_role_and_refuses_an_invalid_one(self):
        result = self.service._cli_controllers({"operation": "add", "target": "Pending Tag", "role": "scripter"})
        self.assertTrue(result["ok"])
        self.assertEqual(self.service.authority.roles.pending_role("Pending Tag"), "scripter")
        self.assertEqual(result["entries"][0]["role"], "scripter")
        with self.assertRaises(ValueError):
            self.service._cli_controllers({"operation": "add", "target": "Other", "role": "admin"})
        self.assertIsNone(self.service.authority.roles.pending_role("Other"))
        with self.assertRaises(ValueError):
            self.service._cli_controllers({"operation": "remove", "target": "Pending Tag", "role": "scripter"})
        self.assertEqual(self.service.authority.roles.pending_role("Pending Tag"), "scripter")

    def test_controller_management_is_refused_to_players_even_over_the_link(self):
        link = self.link()
        alice = FakePlayer(ALICE, "Alice", xuid=OWNER)
        self.join(link, alice)
        self.console(link, "add", "Alice")
        answer = self.submit(link, Principal("player", player_id=ALICE), "botcontrollers", "add", "Mallory")
        self.assertEqual(answer, {"accepted": False, "code": "unauthorized"})
        self.assertTrue(wait_for(lambda: any("only from the server console" in line for line in alice.messages)))

    def test_players_online_before_the_link_get_their_permission_from_the_snapshot(self):
        link = self.link()
        alice = FakePlayer(ALICE, "Alice", xuid=OWNER)
        self.join(link, alice)
        self.console(link, "add", "Alice")
        self.assertTrue(wait_for(lambda: alice.attachments))
        link.stop()
        alice.attachments.clear()
        self.link()
        self.assertTrue(wait_for(lambda: alice.attachments
                                 and alice.attachments[0].permissions == {"endbot.command.control": True}))

    def test_a_menu_result_opens_its_screen_on_the_plugin(self):
        link = self.link()
        alice = FakePlayer(ALICE, "Alice", xuid=OWNER)
        self.join(link, alice)
        self.console(link, "add", "Alice")
        self.submit(link, Principal("player", player_id=ALICE), "bot", "menu")
        self.assertTrue(wait_for(lambda: self.views))
        name, view = self.views[0]
        self.assertEqual((name, type(view).__name__), ("Alice", "BotListView"))

    def test_console_replies_reach_the_bds_console_and_logged_results_the_endbotd_log(self):
        link = self.link()
        self.submit(link, Principal("console"), "bot", "Steve", "spawn")
        self.assertTrue(wait_for(lambda: any("Steve" in line for line in self.mc.command_sender.messages)))
        with self.assertLogs("endbotd", level="INFO") as captured:
            self.submit(link, Principal("console"), "bot", "Steve", "destroy", "stop")
            self.assertTrue(wait_for(lambda: any("destroy stopped" in line for line in captured.output)))

    def test_a_delivery_to_an_offline_player_is_logged(self):
        self.link()
        self.service.deliver_now(ALICE, ["hello"], encode_view(BotListView(())))
        self.assertIn(("warning", f"Delivery to player {ALICE} dropped a form and 1 line(s): player offline"),
                      self.logs)

    def test_permissions_defaults_configure_the_ownership_store(self):
        self.service.close()
        self.start_service(permissions={"bots-default": "private", "groups-default": "shared"})
        store = self.service.commands.ownership
        alice = Principal("player", name="Alice", xuid=OWNER, player_id=ALICE)
        store.record("bots", "0000000a-0000-4000-8000-00000000000b", alice)
        store.record("groups", "0000000a-0000-4000-8000-00000000000c", alice)
        self.assertEqual(store.get("bots", "0000000a-0000-4000-8000-00000000000b").visibility, "private")
        self.assertEqual(store.get("groups", "0000000a-0000-4000-8000-00000000000c").visibility, "shared")
        self.assertEqual(store.path, self.root / "ownership.json")

    def test_invalid_permissions_warn_and_use_shared(self):
        self.service.close()
        self.start_service(permissions={"bots-default": "private", "groups-default": "public"})
        self.assertIn(("warning", ("Invalid [permissions] config: groups-default must be shared or private, "
                                   "found 'public'; using shared")), self.logs)
        store = self.service.commands.ownership
        alice = Principal("player", name="Alice", xuid=OWNER, player_id=ALICE)
        store.record("bots", "0000000a-0000-4000-8000-00000000000d", alice)
        store.record("groups", "0000000a-0000-4000-8000-00000000000e", alice)
        self.assertEqual(store.get("bots", "0000000a-0000-4000-8000-00000000000d").visibility, "shared")
        self.assertEqual(store.get("groups", "0000000a-0000-4000-8000-00000000000e").visibility, "shared")

    def test_a_malformed_form_guard_is_refused(self):
        link = self.link()
        with self.assertRaises(ValueError):
            self.submit(link, Principal("console"), "bot", "ping", guard={"$view": "nope", "fields": {}})


class WireViewTests(unittest.TestCase):
    def test_views_screens_and_guards_round_trip(self):
        guard = GroupGuard("g1", "miners", ("a", "b"), bot_identity="a", return_to_management=True)
        values = (
            GroupView("g1", "miners", ({"name": "Steve"},), (), 2),
            ScriptGuard("r1", "test", values={"name": "Alice"}),
            ActionScreen("Title", "Body", (Button("Go", Run((("Alice", "stop"),), guard=guard)),)),
        )
        for value in values:
            with self.subTest(type(value).__name__):
                wire = json.loads(json.dumps(encode_view(value)))
                self.assertEqual(decode_view(wire), value)

    def test_unknown_types_are_refused_both_ways(self):
        with self.assertRaises(TypeError):
            encode_view(object())
        with self.assertRaises(ValueError):
            decode_view({"$view": "os.system", "fields": {}})


class EntryPointTests(unittest.TestCase):
    def test_endbotd_reports_ready_and_stops_when_its_stdin_closes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "endbotd.token").write_text(TOKEN, encoding="utf-8")
            (root / "endbotd.toml").write_text(
                "[link]\nport = 0\ntoken-file = 'endbotd.token'\n[plugin]\ndata-folder = '.'\n", encoding="utf-8")
            # port 0 is not a valid configured port; the entry point must refuse it with a clear error.
            refused = subprocess.run([sys.executable, "-m", "endbotd", "--config", str(root / "endbotd.toml")],
                                     capture_output=True, text=True, env=_environment(), timeout=30, check=False)
            self.assertEqual(refused.returncode, 1)
            self.assertIn("link.port", refused.stdout)
            (root / "endbotd.toml").write_text(
                f"[link]\nport = {_free_port()}\ntoken-file = 'endbotd.token'\n[plugin]\ndata-folder = '.'\n",
                encoding="utf-8")
            process = subprocess.Popen([sys.executable, "-m", "endbotd", "--config", str(root / "endbotd.toml")],
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
                                       env=_environment())
            try:
                ready = json.loads(process.stdout.readline())
                self.assertEqual(ready["event"], "endbotd_ready")
                process.stdin.close()
                started = time.monotonic()
                self.assertEqual(process.wait(15), 0)
                self.assertLess(time.monotonic() - started, 15)
                self.assertNotIn(TOKEN, process.stdout.read())
            finally:
                if process.poll() is None:
                    process.kill()
                process.stdout.close()


def _free_port():
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _environment():
    import os

    return {**os.environ, "PYTHONPATH": str(CORE)}


if __name__ == "__main__":
    unittest.main()
