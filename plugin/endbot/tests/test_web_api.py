"""What a signed-in browser can ask, with the real authority and ownership rules (docs/WEB.md P11)."""

import importlib.util
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from endbot_core.control import RuntimeControlError
from endbot_core.events import Event
from endbot_core.link import Presence
from endbot_core.ownership import OwnershipStore
from endbot_core.principal import Principal
from endbotd.authority import Authority
from endbotd.web_store import LinkCodes, WebStore

HAS_LIBRARIES = importlib.util.find_spec("webauthn") is not None
if HAS_LIBRARIES:
    from endbotd.web_api import LOG_LINES, WebApi
    from endbotd.web_auth import WebAuth

XUID = "2535412345678901"
PLAYER = "00000000-0000-4000-8000-0000000000a1"
OTHER_XUID = "2535400000000002"
BOT_A = "00000000-0000-4000-8000-00000000000a"
BOT_B = "00000000-0000-4000-8000-00000000000b"


class FakeControl:
    def __init__(self):
        self.down = False

    def request(self, method, **_):
        if self.down:
            raise RuntimeControlError("unavailable", "the runtime is down")
        return [{"name": "Shared", "identityId": BOT_A, "connectionState": "online"},
                {"name": "Private", "identityId": BOT_B, "connectionState": "offline"}]


@unittest.skipUnless(HAS_LIBRARIES, "the WebAuthn library is not installed")
class WebApiTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.authority = Authority.from_config(Presence(), {"controller-policy-file": "policy.json"}, root)
        self.authority.policy.add("Alice", xuid=XUID, player_uuid=PLAYER)
        self.authority.roles.set(XUID, "controller")
        self.store = WebStore(root / "web")
        self.auth = WebAuth(self.store, LinkCodes(), "https://endbot.example", self.authority)
        self.account = self.store.ensure_account(XUID, PLAYER, "Alice")
        self.token = self.auth.issue_session(self.account, "passkey")
        self.ownership = OwnershipStore(root / "ownership.json")
        self.ownership.set_owner("bots", BOT_B, f"xuid:{OTHER_XUID}", "Bob")
        self.ownership.set_visibility("bots", BOT_B, "private")
        self.control = FakeControl()
        self.groups_seen = []
        self.runs_for = []
        group = SimpleNamespace(name="miners", members=("a", "b"))
        commands = SimpleNamespace(ownership=self.ownership,
                                   visible_groups=lambda principal: self.groups_seen.append(principal) or (group,))
        run = SimpleNamespace(identity="r1", name="line")
        scripts = SimpleNamespace(active=lambda principal: self.runs_for.append(principal) or (run,))
        self.bds = True
        self.api = WebApi(self.auth, commands=commands, scripts=scripts, control=self.control,
                          bds_up=lambda: self.bds, display_name=lambda: "Mine", clock=lambda: 5.0)
        self.pushed = []

    def ask(self, **message):
        return self.api.handle(self.token, "c1", message, self.pushed.append)

    def log_event(self, xuid=XUID, text="hello", chat=True):
        to = Principal("player", xuid=xuid, player_id=PLAYER, name="Alice")
        self.api.record(Event(to, "run.log", {"run": "r1", "name": "line", "text": text, "limited": False,
                                              "chat": chat, "skipped": 0}))

    def test_status_shows_this_players_view(self):
        answer = self.ask(type="query", what="status")
        self.assertEqual(answer["code"], "ok")
        data = answer["data"]
        self.assertEqual((data["displayName"], data["bds"], data["gamertag"], data["role"]),
                         ("Mine", "up", "Alice", "controller"))
        self.assertEqual(data["groups"], [{"name": "miners", "members": 2}])
        self.assertEqual(data["runs"], [{"id": "r1", "name": "line"}])

    def test_someone_elses_private_bot_is_not_listed(self):
        names = [bot["name"] for bot in self.ask(type="query", what="bots")["data"]]
        self.assertEqual(names, ["Shared"])

    def test_visibility_is_asked_as_the_web_principal(self):
        self.ask(type="query", what="groups")
        self.ask(type="query", what="runs")
        self.assertEqual(self.groups_seen[0].kind, "web")
        self.assertEqual(self.runs_for[0].xuid, XUID)

    def test_the_runtime_being_down_is_unknown_not_empty(self):
        self.control.down = True
        answer = self.ask(type="query", what="status")
        self.assertEqual(answer["code"], "ok")
        self.assertIsNone(answer["data"]["bots"])

    def test_a_revoke_holds_for_the_next_message(self):
        self.assertEqual(self.ask(type="query", what="status")["code"], "ok")
        self.authority.operation(["remove"], "Alice")
        self.assertEqual(self.ask(type="query", what="status")["code"], "signed_out")

    def test_a_role_change_holds_for_the_next_message(self):
        self.authority.roles.set(XUID, "scripter")
        self.assertEqual(self.ask(type="query", what="status")["data"]["role"], "scripter")

    def test_a_token_that_is_not_a_session_gets_nothing(self):
        self.assertEqual(self.api.handle("nope", "c1", {"type": "query", "what": "status"}, print)["code"],
                         "signed_out")

    def test_unknown_requests_are_unsupported(self):
        for message in ({"type": "query", "what": "secrets"}, {"type": "query", "what": 5}, {"type": "nope"},
                        {"type": "subscribe", "topic": "others"}, {"type": "op"}):
            with self.subTest(message=message):
                self.assertEqual(self.api.handle(self.token, "c1", message, print)["code"], "unsupported")
        self.assertEqual(self.api.handle(self.token, "c1", 5, print)["code"], "invalid_request")

    def test_the_log_shows_only_this_players_events(self):
        self.log_event(text="mine")
        self.log_event(xuid=OTHER_XUID, text="theirs")
        entries = self.ask(type="query", what="log")["data"]
        self.assertEqual(len(entries), 1)
        self.assertEqual((entries[0]["kind"], entries[0]["time"]), ("run.log", 5.0))
        self.assertIn("mine", " ".join(entries[0]["lines"]))
        self.assertNotIn("§", " ".join(entries[0]["lines"]))

    def test_the_log_is_bounded_and_console_events_are_not_kept(self):
        for index in range(LOG_LINES + 5):
            self.log_event(text=str(index))
        self.api.record(Event(Principal("console"), "run.log", {"run": "r", "name": "s", "text": "c", "limited": False,
                                                                "chat": False, "skipped": 0}))
        self.assertEqual(len(self.ask(type="query", what="log")["data"]), LOG_LINES)
        self.assertEqual(list(self.api._logs), [XUID])

    def test_a_line_chat_held_back_still_reaches_the_players_log(self):
        self.log_event(text="not in chat", chat=False)
        self.assertIn("not in chat", " ".join(self.ask(type="query", what="log")["data"][0]["lines"]))

    def test_an_account_notice_reaches_only_that_accounts_pages(self):
        self.ask(type="subscribe", topic="status")
        self.api.notify_account(OTHER_XUID, "passkey_added")
        self.assertEqual(self.pushed, [])
        self.api.notify_account(XUID, "passkey_added")
        self.assertEqual(self.pushed, [{"type": "event", "topic": "account", "data": {"kind": "passkey_added"}}])

    def test_a_log_subscriber_gets_its_own_events_until_it_leaves(self):
        self.assertEqual(self.ask(type="subscribe", topic="log")["code"], "ok")
        self.log_event(xuid=OTHER_XUID, text="theirs")
        self.assertEqual(self.pushed, [])
        self.log_event(text="mine")
        self.assertEqual(self.pushed[0]["topic"], "log")
        self.assertIn("mine", " ".join(self.pushed[0]["data"]["lines"]))
        self.ask(type="unsubscribe", topic="log")
        self.log_event(text="after")
        self.assertEqual(len(self.pushed), 1)

    def test_a_closed_connection_drops_its_subscription(self):
        self.ask(type="subscribe", topic="log")
        self.api.connection_closed("c1")
        self.log_event()
        self.assertEqual(self.pushed, [])

    def test_a_status_subscriber_hears_when_bds_changes(self):
        self.ask(type="subscribe", topic="status")
        self.bds = False
        self.api.status_changed()
        self.assertEqual(self.pushed, [{"type": "event", "topic": "status", "data": {"bds": "down"}}])


if __name__ == "__main__":
    unittest.main()
