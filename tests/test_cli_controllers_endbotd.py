"""`endbot controllers add|remove|list` against a real endbotd in process (docs/ENDBOTD.md P8)."""

import json
import os
import socket
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core" / "src"))
sys.path.insert(0, str(ROOT / "cli" / "endbot" / "src"))

from endbot_cli.endbotd_client import PROTOCOL, EndbotdError, controllers_request
from endbotd.config import EndbotdConfig
from endbotd.service import EndbotService

TOKEN = "k" * 40


class EndbotdCliTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.run_dir = self.root / "run"
        self.run_dir.mkdir()
        (self.run_dir / "supervisor.pid").write_text(str(os.getpid()), encoding="utf-8")
        self.token = self.root / "endbotd.token"
        self.token.write_text(TOKEN + "\n", encoding="utf-8")
        self.logs: list[tuple[str, str]] = []
        config = EndbotdConfig(
            base=self.root, link_port=0, link_token_file=self.token, stores_directory=self.root,
            scripts_folder=self.root / "scripts", journal_file=self.root / "endbotd-runs.json",
            authorization={"controller-policy-file": "controller-policy.json"}, ui={"language": "en"},
        )
        # No runtime is needed: controller management never reaches it.
        self.service = EndbotService(config, control=object(),
                                     log=lambda level, message: self.logs.append((level, message))).start()
        self.addCleanup(self.service.close)
        self.port = self.service.link.address[1]

    def request(self, operation: str, target: str | None = None, token: Path | None = None) -> dict:
        return controllers_request(self.run_dir, token or self.token, self.port, operation, target, timeout=5)

    def raw(self, frames: list[dict], replies: int) -> list[dict]:
        with socket.create_connection(("127.0.0.1", self.port), 5) as sock, sock.makefile("rb") as stream:
            for frame in frames:
                sock.sendall(json.dumps(frame).encode() + b"\n")
            return [json.loads(stream.readline()) for _ in range(replies)]

    def test_add_list_and_remove_round_trip_through_endbotd(self) -> None:
        added = self.request("add", "Player With Spaces")
        self.assertTrue(added["ok"], added)
        self.assertEqual(added["messages"][0], "Endbot: controller Player With Spaces updated")
        listed = self.request("list")
        self.assertEqual([(row["name"], row["state"]) for row in listed["entries"]],
                         [("Player With Spaces", "pending")])
        policy = json.loads((self.root / "controller-policy.json").read_text(encoding="utf-8"))
        self.assertIn("Player With Spaces", json.dumps(policy))
        removed = self.request("remove", "Player With Spaces")
        self.assertTrue(removed["ok"], removed)
        self.assertFalse(self.request("remove", "Missing")["ok"])
        self.assertTrue(any("endbot CLI controllers add" in message for _, message in self.logs))

    def test_wrong_token_is_refused_and_never_logged(self) -> None:
        wrong = self.root / "wrong.token"
        wrong.write_text("w" * 40, encoding="utf-8")
        with self.assertRaises(EndbotdError) as raised:
            self.request("add", "Player", token=wrong)
        self.assertEqual(raised.exception.code, "unauthorized")
        self.assertFalse((self.root / "controller-policy.json").exists())
        self.assertTrue(any("endbot CLI connection refused" in message for _, message in self.logs))
        self.assertFalse(any(TOKEN in message or "w" * 40 in message for _, message in self.logs))

    def test_duplicate_request_id_is_refused_and_not_run_again(self) -> None:
        hello = {"id": 0, "type": "hello", "body": {"client": "cli", "protocol": PROTOCOL, "token": TOKEN}}
        request = {"id": 7, "type": "controllers", "body": {"operation": "add", "target": "Once"}}
        _, first, second = self.raw([hello, request, request], 3)
        self.assertEqual((first["id"], first["ok"]), (7, True))
        self.assertEqual((second["id"], second["ok"], second["error"]["code"]), (7, False, "duplicate"))
        added = [message for _, message in self.logs if "endbot CLI controllers add" in message]
        self.assertEqual(len(added), 1)

    def test_cli_connection_carries_only_cli_requests_and_leaves_the_plugin_link_alone(self) -> None:
        hello = {"id": 0, "type": "hello", "body": {"client": "cli", "protocol": PROTOCOL, "token": TOKEN}}
        submit = {"id": 1, "type": "submit", "body": {"root": "botcontrollers", "argv": ["add", "X"]}}
        bad = {"id": 2, "type": "controllers", "body": {"operation": "add", "target": "a\nstop"}}
        accepted, unsupported, invalid = self.raw([hello, submit, bad], 3)
        self.assertTrue(accepted["ok"])
        self.assertEqual(unsupported["error"]["code"], "unsupported")
        self.assertEqual(invalid["error"]["code"], "invalid")
        self.assertFalse(self.service.link.connected)
        self.assertFalse((self.root / "controller-policy.json").exists())

    def test_endbotd_down_says_so(self) -> None:
        self.service.close()
        with self.assertRaises(EndbotdError) as raised:
            self.request("list")
        self.assertEqual(raised.exception.code, "unavailable")
        self.assertIn("endbotd is not running", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
