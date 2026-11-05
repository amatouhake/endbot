"""The real endbotd for the end-to-end test: no BDS and no runtime, only what a browser can reach.

Reads commands on stdin and answers on stdout, one JSON line each:
``code`` gives a link code for the test player, ``reset`` deletes every passkey and session,
``revoke`` removes their grant, ``log`` records a run log line, ``online NAME`` brings a Bot online.
Never used outside tests.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core" / "src"))

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat  # noqa: E402
from endbot_core.events import Event  # noqa: E402
from endbot_core.principal import Principal  # noqa: E402
from endbotd.config import EndbotdConfig  # noqa: E402
from endbotd.service import EndbotService  # noqa: E402
from endbotd.web_store import LinkIdentity  # noqa: E402

XUID = "2535412345678901"
PLAYER = "00000000-0000-4000-8000-0000000000a1"
BOT = "00000000-0000-4000-8000-00000000000b"


class FakeControl:
    """A runtime with two Bots that accepts what it is asked; ``calls`` is what the page's operations reached."""

    def __init__(self):
        self.calls = []
        self.states = {"Miner_01": "online", "Farmer": "offline"}

    def request(self, method, **params):
        if method in {"profiles", "list"}:
            return [{"name": "Miner_01", "identityId": BOT, "connectionState": self.states["Miner_01"]},
                    {"name": "Farmer", "identityId": "00000000-0000-4000-8000-00000000000c",
                     "connectionState": self.states["Farmer"]}]
        if method == "capabilities":
            return {"identityPinnedRequests": True, "groupInputBatch": True}
        self.calls.append([method, params])
        if method == "status":
            return {"name": params.get("name"), "identityId": BOT, "connectionState": "online", "desiredState": "online",
                    "chunkRadius": 5, "vitals": {"hunger": 20}}
        return {"name": params.get("name"), "identityId": BOT, "connectionState": "online"}


def main() -> None:
    origin, key_path = sys.argv[1], Path(sys.argv[2])
    key_path.write_bytes(Ed25519PrivateKey.from_private_bytes(bytes(range(32))).private_bytes(
        Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
    key_path.chmod(0o600)
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "endbotd.token").write_text("t" * 40, encoding="utf-8")
        runner = os.environ.get("E2E_RUNNER")
        control = FakeControl()
        config = EndbotdConfig(
            base=root, link_port=0, link_token_file=root / "endbotd.token", stores_directory=root,
            scripts_folder=root / "scripts", authorization={"controller-policy-file": "policy.json"},
            ui={"language": "en"}, web={"enabled": True, "origin": origin, "display-name": "E2E server"},
            web_link_key_file=key_path,
            # Enabled with or without a runner: the editor and its checks of the page need no running script.
            scripting={"enabled": True, **({"runner": runner} if runner else {})},
        )
        service = EndbotService(config, control=control, log=lambda level, message: print(
            f"[{level}] {message}", file=sys.stderr, flush=True))
        service.authority.policy.add("Alice", xuid=XUID, player_uuid=PLAYER)
        service.authority.roles.set(XUID, "scripter")
        # What the plugin would see in the world, with no BDS here to see it.
        service.commands.world.observe = lambda identity: {
            "x": 12.5, "y": 64, "z": -3, "dimension": "overworld", "yaw": 90, "pitch": 0, "health": 18, "maxHealth": 20,
            "selectedSlot": 2, "held": {"name": "minecraft:diamond_pickaxe", "count": 1}}
        service.start()
        print(json.dumps({"ready": True}), flush=True)
        for line in sys.stdin:
            command = line.strip()
            if command == "code":
                print(json.dumps({"code": service.web.codes.issue(LinkIdentity(XUID, PLAYER, "Alice"))}), flush=True)
            elif command == "calls":
                print(json.dumps(control.calls), flush=True)
            elif command.startswith("online "):
                control.states[command.split()[1]] = "online"
                print(json.dumps({"ok": True}), flush=True)
            elif command == "reset":
                service.web.domain_changed()
                print(json.dumps({"ok": True}), flush=True)
            elif command == "revoke":
                service._cli_controllers({"operation": "remove", "target": "Alice"})
                print(json.dumps({"ok": True}), flush=True)
            elif command == "log":
                to = Principal("player", name="Alice", xuid=XUID, player_id=PLAYER)
                service.web_api.record(Event(to, "run.log", {
                    "run": "a1b2c3d4e5f6", "name": "strip_mine", "text": "layer 3 done, moving down", "limited": False,
                    "chat": True, "skipped": 0}))
                print(json.dumps({"ok": True}), flush=True)
        service.close()


if __name__ == "__main__":
    main()
