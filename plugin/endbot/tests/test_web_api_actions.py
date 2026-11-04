"""Bot operations and scripts from the web, through the real command path (docs/WEB.md P11, steps 6 and 7)."""

import importlib.util
import unittest
from unittest.mock import patch

from endbot_core.principal import Principal
from test_endbotd_service import OWNER, ServiceHarness
from test_link import ALICE, FakePlayer, wait_for

HAS_LIBRARIES = importlib.util.find_spec("webauthn") is not None
if HAS_LIBRARIES:
    from endbotd.web_api import WebApi

ORIGIN = "https://endbot.example"
GOOD = 'params {}\nlog("hello")\n'


@unittest.skipUnless(HAS_LIBRARIES, "the web libraries are not installed")
class WebActionCase(ServiceHarness):
    def start_service(self, permissions=None, web=None):
        self.scripting = {"enabled": True}
        super().start_service(permissions, web={"enabled": True, "origin": ORIGIN})
        self.api = WebApi(self.service.web.auth, runner=self.service.runner, commands=self.service.commands,
                          scripts=self.service.scripts, control=self.control, bds_up=lambda: True,
                          display_name=lambda: "Test")

    def setUp(self):
        super().setUp()
        self.mc_link = self.link()
        self.alice = FakePlayer(ALICE, "Alice", xuid=OWNER)
        self.join(self.mc_link, self.alice)
        self.console(self.mc_link, "add", "Alice", "--role", "scripter")
        self.assertTrue(wait_for(lambda: self.alice.attachments))
        self.assertTrue(wait_for(lambda: self.service.authority.role(OWNER) == "scripter"))
        web = self.service.web
        account = web.store.ensure_account(OWNER, ALICE, "Alice")
        self.token = web.auth.issue_session(account, "passkey")

    def ask(self, **message):
        return self.api.handle(self.token, "c1", message, lambda _: None)


class OperationTests(WebActionCase):
    def test_an_operation_reaches_the_runtime_as_this_player(self):
        answer = self.ask(type="op", operation={"op": "spawn", "target": "Steve", "params": {}})
        self.assertEqual(answer["code"], "ok", answer)
        self.assertTrue(answer["messages"])
        self.assertIn("spawn", [operation for operation, _ in self.control.calls])

    def test_operations_outside_the_registry_or_kept_from_the_web_are_refused(self):
        before = list(self.control.calls)
        for operation in ({"op": "menu", "target": "Steve"}, {"op": "help"}, {"op": "owner", "target": "Steve",
                                                                                "params": {"owner": "Bob"}},
                          {"op": "nonsense", "target": "Steve"}, {"op": 5}, "move", None,
                          {"op": "move", "target": "Steve", "params": []}):
            with self.subTest(operation=operation):
                self.assertEqual(self.ask(type="op", operation=operation)["code"], "invalid_argument")
        self.assertEqual(self.control.calls, before)

    def test_arguments_are_typed_and_cannot_smuggle_another_command(self):
        before = list(self.control.calls)
        for operation in ({"op": "move", "target": "Steve", "params": {"direction": "sideways"}},
                          {"op": "move", "target": "Steve stop", "params": {"direction": "forward"}},
                          {"op": "rename", "target": "Steve", "params": {"name": "a b"}},
                          {"op": "move", "target": "Steve", "params": {"direction": "forward", "extra": 1}}):
            with self.subTest(operation=operation):
                self.assertEqual(self.ask(type="op", operation=operation)["code"], "invalid_argument")
        self.assertEqual(self.control.calls, before)

    def test_a_revoke_refuses_the_next_operation(self):
        self.service.web.auth.authority = None
        self.service._cli_controllers({"operation": "remove", "target": "Alice"})
        answer = self.ask(type="op", operation={"op": "spawn", "target": "Steve", "params": {}})
        self.assertEqual(answer["code"], "signed_out")

    def test_the_web_principal_is_never_the_console(self):
        principal = self.service.web.auth.principal(self.token)
        self.assertEqual(principal.kind, "web")
        self.assertFalse(principal.console)


class ScriptTests(WebActionCase):
    def declared(self):
        """No runner in this suite: the declaration check is the part the runner answers."""
        return patch.object(self.service.scripts, "inspect", side_effect=lambda script, source: type(
            "Declaration", (), {"schema": [], "requires": ()})())

    def test_saving_lists_and_reads_back_with_an_owner(self):
        with self.declared():
            answer = self.ask(type="script.save", name="line", source=GOOD, revision=None)
        self.assertEqual(answer["code"], "ok", answer)
        self.assertEqual(answer["data"]["source"], GOOD)
        listed = self.ask(type="script.list")["data"]
        self.assertTrue(listed["canAuthor"])
        self.assertEqual([(s["name"], s["own"], s["canEdit"]) for s in listed["scripts"]], [("line", True, True)])

    def test_a_stale_revision_is_a_conflict_that_shows_the_current_source(self):
        with self.declared():
            first = self.ask(type="script.save", name="line", source=GOOD, revision=None)["data"]
            second = self.ask(type="script.save", name="line", source=GOOD + "-- two\n", revision=first["revision"])
            self.assertEqual(second["code"], "ok", second)
            stale = self.ask(type="script.save", name="line", source=GOOD + "-- three\n", revision=first["revision"])
        self.assertEqual(stale["code"], "conflict", stale)
        self.assertEqual(stale["current"]["source"], GOOD + "-- two\n")
        self.assertEqual(self.ask(type="script.get", name="line")["data"]["source"], GOOD + "-- two\n")

    def test_a_new_script_cannot_overwrite_an_existing_name(self):
        with self.declared():
            self.ask(type="script.save", name="line", source=GOOD, revision=None)
            again = self.ask(type="script.save", name="line", source="params {}\n", revision=None)
        self.assertNotEqual(again["code"], "ok")
        self.assertEqual(self.ask(type="script.get", name="line")["data"]["source"], GOOD)

    def test_a_controller_can_run_but_not_author(self):
        self.service.authority.roles.set(OWNER, "controller")
        with self.declared():
            answer = self.ask(type="script.save", name="line", source=GOOD, revision=None)
            compile_answer = self.ask(type="script.compile", name="line", source=GOOD)
        self.assertEqual(answer["code"], "unauthorized")
        self.assertNotEqual(compile_answer["code"], "ok")
        self.assertFalse(self.ask(type="script.list")["data"]["canAuthor"])

    def test_someone_elses_private_script_looks_missing(self):
        self.service.scripts.store.save("theirs", GOOD, None, "xuid:2535400000000002", "Bob")
        store = self.service.scripts.store
        store.set_visibility("theirs", "private", store.get("theirs").revision)
        answer = self.ask(type="script.get", name="theirs")
        self.assertEqual(answer["code"], "not_found" if answer["code"] == "not_found" else answer["code"])
        self.assertNotEqual(answer["code"], "ok")
        self.assertIn("does not exist", answer["messages"][0])
        self.assertEqual(self.ask(type="script.list")["data"]["scripts"], [])

    def test_someone_elses_shared_script_can_be_read_and_not_saved(self):
        store = self.service.scripts.store
        store.save("theirs", GOOD, None, "xuid:2535400000000002", "Bob")
        store.set_visibility("theirs", "shared", store.get("theirs").revision)
        read = self.ask(type="script.get", name="theirs")["data"]
        self.assertFalse(read["canEdit"])
        with self.declared():
            answer = self.ask(type="script.save", name="theirs", source="params {}\n", revision=read["revision"])
        self.assertNotEqual(answer["code"], "ok")
        self.assertEqual(store.get("theirs").source, GOOD)

    def test_a_name_with_anything_else_in_it_is_refused(self):
        for name in ("a b", "a;b", "../x", "", 5, None, "x" * 65):
            with self.subTest(name=name):
                self.assertEqual(self.ask(type="script.get", name=name)["code"], "invalid_argument")
                self.assertEqual(self.ask(type="script.delete", name=name)["code"], "invalid_argument")

    def test_copy_rename_visibility_restore_and_delete_use_the_in_game_rules(self):
        with self.declared():
            first = self.ask(type="script.save", name="line", source=GOOD, revision=None)["data"]
            self.ask(type="script.save", name="line", source=GOOD + "-- two\n", revision=first["revision"])
        current = self.ask(type="script.get", name="line")["data"]
        self.assertEqual(len(current["history"]), 1)
        self.assertEqual(self.ask(type="script.copy", name="line", new="line2", revision=current["revision"])["code"],
                         "ok")
        self.assertEqual(self.ask(type="script.visibility", name="line", visibility="shared",
                                  revision=current["revision"])["code"], "ok")
        current = self.ask(type="script.get", name="line")["data"]
        answer = self.ask(type="script.restore", name="line", index=1, revision=current["revision"])
        self.assertEqual(answer["code"], "ok", answer)
        current = self.ask(type="script.get", name="line")["data"]
        self.assertEqual(current["source"], GOOD)
        self.assertEqual(self.ask(type="script.rename", name="line", new="row", revision=current["revision"])["code"],
                         "ok")
        self.assertEqual(self.ask(type="script.delete", name="row", revision=current["revision"])["code"], "ok")
        self.assertNotEqual(self.ask(type="script.get", name="row")["code"], "ok")

    def test_a_stale_form_cannot_delete_a_newer_script(self):
        with self.declared():
            first = self.ask(type="script.save", name="line", source=GOOD, revision=None)["data"]
            self.ask(type="script.save", name="line", source=GOOD + "-- two\n", revision=first["revision"])
        self.assertNotEqual(self.ask(type="script.delete", name="line", revision=first["revision"])["code"], "ok")
        self.assertEqual(self.ask(type="script.get", name="line")["code"], "ok")

    def test_junk_in_the_other_fields_is_refused(self):
        with self.declared():
            self.ask(type="script.save", name="line", source=GOOD, revision=None)
        for message in ({"type": "script.rename", "name": "line", "new": "a b"},
                        {"type": "script.visibility", "name": "line", "visibility": "everyone"},
                        {"type": "script.restore", "name": "line", "index": "1; delete"},
                        {"type": "script.nonsense", "name": "line"}):
            with self.subTest(message=message):
                self.assertEqual(self.ask(**message)["code"], "unsupported")
        for message in ({"type": "script.save", "name": "line", "source": 5},
                        {"type": "script.run", "name": "line", "values": [], "requires": [], "revision": "r"},
                        {"type": "script.run", "name": "line", "values": {"a": {}}, "requires": [], "revision": "r"},
                        {"type": "script.run", "name": "line", "values": {}, "requires": "all", "revision": "r"},
                        {"type": "script.stop", "run": "a b"}):
            with self.subTest(message=message):
                self.assertEqual(self.ask(**message)["code"], "invalid_argument")

    def test_a_compile_error_names_its_line(self):
        with patch.object(self.service.scripts, "inspect",
                          side_effect=ValueError("Script declaration failed: script:3: Expected 'end'")):
            answer = self.ask(type="script.compile", name="line", source=GOOD)
        self.assertEqual(answer["line"], 3)
        self.assertIn("Expected", answer["messages"][0])

    def test_a_save_that_fails_its_check_names_its_line_and_keeps_the_old_source(self):
        with self.declared():
            first = self.ask(type="script.save", name="line", source=GOOD, revision=None)["data"]
        with patch.object(self.service.scripts, "inspect",
                          side_effect=ValueError("Script declaration failed: script:2: bad")):
            answer = self.ask(type="script.save", name="line", source="broken", revision=first["revision"])
        self.assertNotEqual(answer["code"], "ok")
        self.assertEqual(answer["line"], 2)
        self.assertEqual(self.ask(type="script.get", name="line")["data"]["source"], GOOD)

    def test_a_run_is_held_to_the_declaration_the_form_read(self):
        with self.declared():
            first = self.ask(type="script.save", name="line", source=GOOD, revision=None)["data"]
        with patch.object(self.service.scripts, "start", return_value="run1") as start:
            answer = self.ask(type="script.run", name="line", values={"count": 3}, requires=["log"],
                              revision=first["revision"])
        self.assertEqual(answer["code"], "ok", answer)
        args = start.call_args.args
        self.assertEqual(args[1], {"count": 3})
        self.assertEqual(args[4], ("log",))
        self.assertEqual(args[2].kind, "web")

    def test_a_run_from_an_older_revision_is_refused(self):
        with self.declared():
            first = self.ask(type="script.save", name="line", source=GOOD, revision=None)["data"]
            self.ask(type="script.save", name="line", source=GOOD + "-- two\n", revision=first["revision"])
        answer = self.ask(type="script.run", name="line", values={}, requires=[], revision=first["revision"])
        self.assertNotEqual(answer["code"], "ok")

    def test_scripting_off_says_so(self):
        self.service.scripts.config = type(self.service.scripts.config)()
        answer = self.ask(type="script.list")
        self.assertNotEqual(answer["code"], "ok")
        self.assertTrue(answer["messages"])

    def test_templates_are_offered(self):
        self.assertTrue(self.ask(type="script.templates")["data"])

    def test_the_web_principal_is_not_a_player_principal(self):
        self.assertIsInstance(self.service.web.auth.principal(self.token), Principal)


if __name__ == "__main__":
    unittest.main()
