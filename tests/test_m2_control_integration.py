import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugin" / "endbot" / "src"))

from endstone_endbot.control import RuntimeControlClient


class M2ControlIntegrationTests(unittest.TestCase):
    def test_python_plugin_client_talks_to_node_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            config = {
                "dataDirectory": "./data",
                "controlTokenPath": "./control.token",
                "ownerPrivateKeyPath": "./owner-private.pem",
                "ownerPublicKeyPath": "./owner-public.pem",
                "controlPort": 0,
            }
            config_path = directory / "runtime.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            process = subprocess.Popen(
                ["node", str(ROOT / "runtime" / "src" / "cli.js"), "--config", str(config_path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                line = process.stdout.readline()
                self.assertTrue(line, f"runtime exited before readiness (status={process.poll()})")
                ready = json.loads(line)
                self.assertEqual(ready["event"], "endbot_runtime_ready")
                client = RuntimeControlClient("127.0.0.1", ready["port"], directory / "control.token")
                self.assertEqual(client.request("ping"), {"runtime": "endbot", "protocolVersion": 1})
                self.assertEqual(client.request("list"), [])
            finally:
                # This test owns an isolated child and tests transport
                # interoperability, not service-manager signal behavior.
                process.kill()
                process.wait(timeout=10)
                process.stdout.close()
                process.stderr.close()


if __name__ == "__main__":
    unittest.main()
