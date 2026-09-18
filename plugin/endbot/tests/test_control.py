import json
import socketserver
import tempfile
import threading
import unittest
from pathlib import Path

from endstone_endbot.control import RuntimeControlClient, RuntimeControlError


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        request = json.loads(self.rfile.readline())
        if request["token"] != self.server.token:
            response = {
                "version": 1,
                "id": request["id"],
                "ok": False,
                "error": {"code": "unauthorized", "message": "Unauthorized"},
            }
        else:
            response = {"version": 1, "id": request["id"], "ok": True, "result": request["params"]}
        self.wfile.write(json.dumps(response).encode() + b"\n")


class ControlClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.token_file = Path(self.directory.name) / "token"
        self.token = "a" * 43
        self.token_file.write_text(self.token)
        self.server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
        self.server.token = self.token
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.directory.cleanup()

    def test_authenticated_request_round_trip(self) -> None:
        client = RuntimeControlClient("127.0.0.1", self.server.server_address[1], self.token_file)
        self.assertEqual(client.request("spawn", name="Alice"), {"name": "Alice"})

    def test_bad_token_is_rejected(self) -> None:
        self.token_file.write_text("b" * 43)
        client = RuntimeControlClient("127.0.0.1", self.server.server_address[1], self.token_file)
        with self.assertRaisesRegex(RuntimeControlError, "Unauthorized"):
            client.request("list")

    def test_remote_control_host_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "loopback"):
            RuntimeControlClient("192.0.2.1", 19142, self.token_file)


if __name__ == "__main__":
    unittest.main()
