"""What a signed-in browser can ask, with the real authority and ownership rules (docs/WEB.md P11)."""

import importlib.util
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from endbot_core.control import RuntimeControlError
from endbot_core.events import Event
from endbot_core.groups import GroupCommandService, GroupStore
from endbot_core.i18n import LocalizedError
from endbot_core.link import Presence
from endbot_core.operations import EVENTS
from endbot_core.ownership import OwnershipStore
from endbot_core.principal import Principal
from endbot_core.script_config import ScriptConfig
from endbot_core.script_inputs import OwnedInputs
from endbot_core.script_store import ScriptStore
from endbot_core.scripting import Declaration, ScriptService
from endbot_core.trigger_store import TriggerStore
from endbotd.authority import Authority
from endbotd.occurrences import OccurrenceHub
from endbotd.triggers import TriggerEngine
from endbotd.web_api import LOG_LINES, WebApi
from endbotd.web_store import LinkCodes, WebStore
from test_command_service import FakeControl as FakeRuntimeControl
from test_scripting import FakeRunner

HAS_LIBRARIES = importlib.util.find_spec("webauthn") is not None
if HAS_LIBRARIES:
    from endbotd.web_auth import WebAuth

XUID = "2535412345678901"
PLAYER = "00000000-0000-4000-8000-0000000000a1"
OTHER_XUID = "2535400000000002"
OTHER_PLAYER = "00000000-0000-4000-8000-0000000000b2"
BOT_A = "00000000-0000-4000-8000-00000000000a"
BOT_B = "00000000-0000-4000-8000-00000000000b"


class FakeAuth:
    """The session side of the web handshake, without WebAuthn: a token maps to its current principal."""

    def __init__(self):
        self.principals = {}

    def add(self, token, principal):
        self.principals[token] = principal

    def principal(self, token, language="en", active=True):
        principal = self.principals.get(token)
        return replace(principal, language=language) if principal is not None else None


class FakeControl:
    def __init__(self):
        self.down = False
        self.states = {}

    def request(self, method, **_):
        if self.down:
            raise RuntimeControlError("unavailable", "the runtime is down")
        return [{"name": "Shared", "identityId": BOT_A, "connectionState": self.states.get("Shared", "online")},
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
        self.clock = [1_000_000.0]
        self.store = WebStore(root / "web", clock=lambda: self.clock[0])
        self.auth = WebAuth(self.store, LinkCodes(), "https://endbot.example", self.authority,
                            clock=lambda: self.clock[0])
        self.account = self.store.ensure_account(XUID, PLAYER, "Alice")
        self.token = self.auth.issue_session(self.account, "passkey")
        self.ownership = OwnershipStore(root / "ownership.json")
        self.ownership.set_owner("bots", BOT_B, f"xuid:{OTHER_XUID}", "Bob")
        self.ownership.set_visibility("bots", BOT_B, "private")
        self.control = FakeControl()
        self.groups_seen = []
        self.runs_for = []
        group = SimpleNamespace(name="miners", members=(BOT_A, "gone"))
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
        self.assertEqual(data["groups"], [{"name": "miners", "members": ["Shared", None]}])
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
                        {"type": "subscribe", "topic": "others"}):
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

    def test_a_page_that_only_listens_stops_hearing_when_its_session_expires(self):
        self.ask(type="subscribe", topic="log")
        self.ask(type="subscribe", topic="status")
        self.log_event(text="while valid")
        self.assertEqual(len(self.pushed), 1)
        self.clock[0] += 8 * 86400
        self.log_event(text="after expiry")
        self.api.status_changed()
        self.api.notify_account(XUID, "passkey_added")
        self.assertEqual(len(self.pushed), 1)
        self.assertEqual(self.api._subscribers, {})

    def test_a_closed_connection_drops_its_subscription(self):
        self.ask(type="subscribe", topic="log")
        self.api.connection_closed("c1")
        self.log_event()
        self.assertEqual(self.pushed, [])

    def test_a_status_subscriber_hears_when_anything_changes_and_only_then(self):
        self.ask(type="subscribe", topic="status")
        self.api.poll()
        self.assertEqual(len(self.pushed), 1)
        self.api.poll()
        self.assertEqual(len(self.pushed), 1)
        self.bds = False
        self.api.status_changed()
        self.assertEqual(self.pushed[-1]["data"]["bds"], "down")
        self.control.states["Shared"] = "offline"
        self.api.poll()
        self.assertEqual(self.pushed[-1]["data"]["bds"], "down")
        self.assertEqual(len(self.pushed), 3)
        self.assertEqual([bot["state"] for bot in self.pushed[-1]["data"]["bots"]], ["offline"])

    def test_a_subscriber_hears_only_what_it_may_see(self):
        self.ask(type="subscribe", topic="status")
        self.api.poll()
        self.assertEqual([bot["name"] for bot in self.pushed[0]["data"]["bots"]], ["Shared"])

    def test_a_command_result_joins_this_players_history_and_reaches_the_page(self):
        self.ask(type="subscribe", topic="log")
        to = Principal("player", xuid=XUID, player_id=PLAYER)
        self.api.record_result(to, ["Miner", "jump"], "bot", SimpleNamespace(messages=("Endbot: done",), code="ok"))
        self.assertEqual(self.pushed[0]["data"]["command"], "/bot Miner jump")
        self.assertEqual(self.pushed[0]["data"]["lines"], ["done"])
        entries = self.ask(type="query", what="log")["data"]
        self.assertEqual(entries[0]["kind"], "command")

    def test_a_saved_scripts_source_stays_out_of_the_history(self):
        to = Principal("player", xuid=XUID, player_id=PLAYER)
        self.api.record_result(to, ["save", "line", "secret source"], "botscript",
                               SimpleNamespace(messages=("saved",), code="ok"))
        self.assertEqual(self.ask(type="query", what="log")["data"][0]["command"], "/botscript save line")

    def test_a_scripts_values_and_contents_stay_out_of_the_history(self):
        to = Principal("player", xuid=XUID, player_id=PLAYER)
        self.api.record_result(to, ["run", "deploy", "key=secret"], "botscript",
                               SimpleNamespace(messages=("started",), code="ok"))
        self.api.record_result(to, ["show", "deploy"], "botscript",
                               SimpleNamespace(messages=("secret source",), code="ok"))
        entries = [entry for entry in self.ask(type="query", what="log")["data"] if entry["kind"] == "command"]
        self.assertEqual([entry["command"] for entry in entries], ["/botscript run deploy"])
        self.assertNotIn("secret", str(entries))

    def test_someone_elses_results_stay_out_of_the_history(self):
        other = Principal("player", xuid=OTHER_XUID, player_id=PLAYER)
        self.api.record_result(other, ["x", "jump"], "bot", SimpleNamespace(messages=("done",), code="ok"))
        console = Principal("console")
        self.api.record_result(console, ["x", "jump"], "bot", SimpleNamespace(messages=("done",), code="ok"))
        self.assertEqual(self.ask(type="query", what="log")["data"], [])

    def test_a_group_request_is_checked_before_it_becomes_a_command(self):
        for message in ({"type": "group.create", "name": "a b"}, {"type": "group.add", "name": "miners", "bot": "a b"},
                        {"type": "group.create", "name": 5}, {"type": "group.add", "name": "miners"}):
            with self.subTest(message=message):
                self.assertEqual(self.api.handle(self.token, "c1", message, print)["code"], "invalid_argument")
        self.assertEqual(self.ask(type="group.nope", name="miners")["code"], "unsupported")

    def test_one_bot_is_read_by_a_checked_name(self):
        for name in ("a b", "help", 5, None):
            with self.subTest(name=name):
                self.assertEqual(self.ask(type="query", what="bot", name=name)["code"], "invalid_argument")

    def test_triggers_are_unavailable_without_an_engine(self):
        answer = self.ask(type="trigger.list")
        self.assertEqual((answer["code"], answer["messages"]), ("unavailable", ["Triggers are unavailable"]))


class TriggerWebApiTests(unittest.TestCase):
    """The trigger and event-kind messages, through a real engine and its own ownership rules."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.auth = FakeAuth()
        self.token, self.bob_token = "alice", "bob"
        self.auth.add(self.token, Principal("web", name="Alice", xuid=XUID, player_id=PLAYER,
                                            grant_revision=1, role="controller"))
        self.auth.add(self.bob_token, Principal("web", name="Bob", xuid=OTHER_XUID, player_id=OTHER_PLAYER,
                                                grant_revision=1, role="controller"))
        self.alice = Principal("player", name="Alice", xuid=XUID, player_id=PLAYER,
                               grant_revision=1, role="controller")

        self.config = ScriptConfig(enabled=True, wall_seconds=5)
        self.inputs = OwnedInputs(FakeRuntimeControl())
        groups = GroupStore(root / "groups.json")
        self.commands = GroupCommandService(self.inputs, None, groups, lambda source: True)
        self.script_store = ScriptStore(root / "scripts.json", self.config)
        self.declaration = Declaration([{"name": "event", "type": "event"}], ())
        self.scripts = ScriptService(self.config, self.script_store, self.commands, self.inputs, root,
                                     lambda source: True,
                                     process_factory=lambda *args: FakeRunner([{"type": "done"}],
                                                                              schema=self.declaration.schema),
                                     bootstrap="trusted")
        self.scripts.inspect = lambda script, source: self.declaration
        self.worker = ThreadPoolExecutor(max_workers=1)
        self.addCleanup(self.worker.shutdown)
        self.scripts.ordered = lambda callback: self.worker.submit(callback).result(timeout=3)
        self.scripts.notify = lambda *args, **kwargs: None
        self.scripts.publish = lambda event: None
        self.script_store.save("guard", "params { param.event('event') }", None,
                               owner=f"xuid:{XUID}", owner_name="Alice")

        self.hub = OccurrenceHub(EVENTS, lambda interest: None)
        self.hub.link_changed("epoch-1")
        self.trigger_store = TriggerStore(root / "triggers.json")
        self.clock = [1_000_000.0]
        self.engine = TriggerEngine(self.trigger_store, self.hub, self.scripts,
                                    authorize=lambda source: True, linked=lambda: True,
                                    monotonic=lambda: self.clock[0], wall=lambda: self.clock[0])
        self.addCleanup(self.engine.close)
        self.engine.start()
        self.api = WebApi(self.auth, commands=self.commands, scripts=self.scripts,
                          control=FakeRuntimeControl(), bds_up=lambda: True, display_name=lambda: "Mine",
                          triggers=self.engine)
        self.engine.on_changed = self.api.trigger_changed
        self.pushed, self.bob_pushed = [], []

    def ask(self, **message):
        answer = self.api.handle(self.token, "c1", message, self.pushed.append)
        self.engine.flush_notices()  # change notices go out on their own thread
        return answer

    def ask_bob(self, **message):
        answer = self.api.handle(self.bob_token, "c2", message, self.bob_pushed.append)
        self.engine.flush_notices()
        return answer

    def create(self, **fields):
        message = {"type": "trigger.create", "script": "guard", "kind": "block.change",
                   "filter": {"area": "within 0 64 0 2 65 2"}, "arguments": [], "name": "guard"}
        message.update(fields)
        answer = self.ask(**message)
        self.assertEqual(answer["code"], "ok", answer)
        return answer["data"]["trigger"]

    def bob(self):
        return self.auth.principal(self.bob_token, active=False)

    def test_event_kinds_is_the_registry_object(self):
        answer = self.ask(type="event.kinds")
        self.assertEqual((answer["code"], answer["data"]), ("ok", {"kinds": EVENTS}))

    def test_create_a_schedule_trigger(self):
        trigger = self.create(kind="schedule", filter={"every": "10m"}, name="tick")
        self.assertEqual((trigger["kind"], trigger["filter"], trigger["status"]),
                         ("schedule", {"every": "10m"}, "armed"))

    def test_create_lists_and_gets_a_trigger_as_a_plain_object(self):
        self.declaration = Declaration([{"name": "event", "type": "event"}], ("observe.chat",))
        trigger = self.create(visibility="shared", policy={"cooldown": 0})
        self.assertEqual((trigger["name"], trigger["owner"], trigger["ownerName"]),
                         ("guard", f"xuid:{XUID}", "Alice"))
        self.assertEqual(trigger["filter"], {"area": "within 0 64 0 2 65 2"})
        self.assertEqual(trigger["approved"], {"declaration": ["observe.chat"]})
        self.assertTrue(trigger["own"])
        self.assertNotIn("playerId", json.dumps(trigger))
        self.assertNotIn("grantRevision", json.dumps(trigger))
        listed = self.ask(type="trigger.list")
        self.assertEqual(listed["data"], {"triggers": [trigger], "canArm": True, "limit": 8})
        got = self.ask(type="trigger.get", name="guard")
        self.assertEqual(got["data"], {"trigger": trigger, "requires": ["observe.chat"]})

    def test_update_enable_disable_and_remove_call_the_engine(self):
        self.create()
        updated = self.ask(type="trigger.update", name="guard", visibility="shared",
                           policy={"concurrency": "queue", "cooldown": 0, "queue": 2})
        self.assertEqual(updated["code"], "ok", updated)
        self.assertEqual(updated["data"]["trigger"]["visibility"], "shared")
        self.assertEqual(updated["data"]["trigger"]["policy"],
                         {"concurrency": "queue", "cooldown": 0, "startsPerMinute": 10, "queue": 2})
        self.assertEqual(self.ask(type="trigger.disable", name="guard")["data"]["trigger"]["status"], "disabled")
        self.assertEqual(self.ask(type="trigger.enable", name="guard")["data"]["trigger"]["status"], "armed")
        removed = self.ask(type="trigger.remove", name="guard")
        self.assertEqual((removed["code"], removed["data"]),
                         ("ok", {"name": "guard", "owner": f"xuid:{XUID}"}))
        self.assertEqual(self.ask(type="trigger.list")["data"]["triggers"], [])

    def test_a_filter_area_round_trips_as_its_clause_text(self):
        for text in ("within 0 64 0", "within 0 64 0 3 65 3", "within 0 64 0 3 65 3 except 0 64 0"):
            with self.subTest(text=text):
                trigger = self.create(name="area", filter={"area": text})
                self.assertEqual(trigger["filter"], {"area": text})
                self.assertEqual(self.ask(type="trigger.get", name="area")["data"]["trigger"]["filter"],
                                 {"area": text})
                self.ask(type="trigger.remove", name="area")

    def test_someone_elses_private_trigger_is_refused_with_the_engines_words(self):
        self.create()
        self.assertEqual(self.ask_bob(type="trigger.list")["data"]["triggers"], [])
        with self.assertRaises(LocalizedError) as caught:
            self.engine.enable(self.bob(), "guard", f"xuid:{XUID}")
        answer = self.ask_bob(type="trigger.enable", name="guard", owner=f"xuid:{XUID}")
        self.assertEqual((answer["code"], answer["messages"]), ("invalid_argument", [str(caught.exception)]))
        self.assertEqual(self.ask_bob(type="trigger.get", name="guard", owner=f"xuid:{XUID}")["messages"],
                         [str(caught.exception)])
        self.assertEqual(self.ask_bob(type="subscribe", topic=f"trigger:xuid:{XUID}/guard")["messages"],
                         [str(caught.exception)])

    def test_a_shared_trigger_is_visible_and_only_its_owner_may_change_it(self):
        self.create()
        self.ask(type="trigger.update", name="guard", visibility="shared")
        listed = self.ask_bob(type="trigger.list")["data"]["triggers"]
        self.assertEqual([(trigger["name"], trigger["own"]) for trigger in listed], [("guard", False)])
        with self.assertRaises(LocalizedError) as caught:
            self.engine.disable(self.bob(), "guard", f"xuid:{XUID}")
        answer = self.ask_bob(type="trigger.disable", name="guard", owner=f"xuid:{XUID}")
        self.assertEqual((answer["code"], answer["messages"]), ("invalid_argument", [str(caught.exception)]))

    def test_a_subscription_hears_state_changes_until_it_unsubscribes(self):
        self.create()
        topic = f"trigger:xuid:{XUID}/guard"
        self.assertEqual(self.ask(type="subscribe", topic=topic)["code"], "ok")
        self.ask(type="trigger.disable", name="guard")
        self.assertEqual(len(self.pushed), 1)
        self.assertEqual((self.pushed[0]["type"], self.pushed[0]["topic"]), ("event", topic))
        self.assertEqual(self.pushed[0]["data"]["trigger"]["status"], "disabled")
        self.ask(type="unsubscribe", topic=topic)
        self.ask(type="trigger.enable", name="guard")
        self.assertEqual(len(self.pushed), 1)

    def test_a_subscription_does_not_hear_a_trigger_it_may_not_see(self):
        self.create(visibility="shared")
        topic = f"trigger:xuid:{XUID}/guard"
        self.assertEqual(self.ask_bob(type="subscribe", topic=topic)["code"], "ok")
        self.ask(type="trigger.disable", name="guard")
        self.assertEqual(len(self.bob_pushed), 1)
        self.ask(type="trigger.enable", name="guard")
        self.assertEqual(len(self.bob_pushed), 2)
        self.ask(type="trigger.update", name="guard", visibility="private")
        self.assertEqual(len(self.bob_pushed), 2)
        self.ask(type="trigger.disable", name="guard")
        self.assertEqual(len(self.bob_pushed), 2)
        refused = self.ask_bob(type="subscribe", topic=topic)
        self.assertEqual((refused["code"], refused["messages"]),
                         ("invalid_argument", ["Trigger guard does not exist"]))

    def test_a_revoked_session_stops_hearing_trigger_changes(self):
        self.create()
        topic = f"trigger:xuid:{XUID}/guard"
        self.ask(type="subscribe", topic=topic)
        del self.auth.principals[self.token]
        self.engine.disable(self.alice, "guard")
        self.engine.flush_notices()
        self.assertEqual(self.pushed, [])
        self.assertEqual(self.api._subscribers, {})

    def test_triggers_turned_off_refuse_arming_exactly_as_the_game_does(self):
        self.create()
        self.engine.max_triggers = 0
        listed = self.ask(type="trigger.list")
        self.assertEqual((listed["code"], [item["name"] for item in listed["data"]["triggers"]]), ("ok", ["guard"]))
        self.assertFalse(listed["data"]["canArm"])
        answer = self.ask(type="trigger.enable", name="guard")
        self.assertNotEqual(answer["code"], "ok")
        self.assertIn("Triggers are turned off on this server", answer.get("messages", []))
        self.assertEqual(self.ask(type="trigger.remove", name="guard")["code"], "ok")

    def test_a_removal_is_pushed_as_a_removal(self):
        self.create()
        topic = f"trigger:xuid:{XUID}/guard"
        self.ask(type="subscribe", topic=topic)
        self.ask(type="trigger.remove", name="guard")
        self.assertEqual(self.pushed[-1]["data"], {"removed": True, "name": "guard", "owner": f"xuid:{XUID}"})

    def test_checked_names_owners_and_fields(self):
        for message in (
                {"type": "trigger.get"}, {"type": "trigger.get", "name": "a b"},
                {"type": "trigger.get", "name": "guard", "owner": "xuid:oops"},
                {"type": "trigger.create"}, {"type": "trigger.create", "script": "guard", "kind": "Bad",
                                             "filter": {}, "arguments": []},
                {"type": "trigger.create", "script": "guard", "kind": "block.change", "filter": [], "arguments": []},
                {"type": "trigger.create", "script": "guard", "kind": "block.change", "filter": {},
                 "arguments": [5]},
                {"type": "trigger.create", "script": "guard", "kind": "block.change", "filter": {},
                 "arguments": [], "name": "a b"},
                {"type": "trigger.create", "script": "guard", "kind": "block.change", "filter": {},
                 "arguments": [], "visibility": "public"},
                {"type": "trigger.create", "script": "guard", "kind": "block.change", "filter": {},
                 "arguments": [], "policy": []},
                {"type": "trigger.update", "name": "guard"},
                {"type": "trigger.update", "name": "guard", "visibility": "public"},
                {"type": "trigger.nope", "name": "guard"},
                {"type": "subscribe", "topic": "trigger:not-a-key/guard"}):
            with self.subTest(message=message):
                self.assertEqual(self.ask(**message)["code"],
                                 "unsupported" if message["type"] == "trigger.nope" else "invalid_argument")


if __name__ == "__main__":
    unittest.main()
