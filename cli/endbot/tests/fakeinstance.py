"""Builders for fake Endbot instances in temporary directories (tests only).

Never commit a real world file, key, or token (AGENTS.md); every instance here
is synthetic and lives in a test-owned temporary directory.
"""

from __future__ import annotations

import json
import socket
import sys
from pathlib import Path

import fakenbt

from endbot_cli.instance import InstancePaths
from endbot_cli.lock import LockData, normalize_bds_version

FAKES_DIR = Path(__file__).resolve().parent

ENDBOT_TOML_TEMPLATE = """\
[server]
path = '{server_path}'

[controllers]
gamertags = {gamertags}

[runtime]
control-port = {control_port}
"""

SERVER_PROPERTIES = """\
online-mode=true
allow-cheats=false
level-name=world
"""

CONTROL_TOKEN = "test-control-token-0123456789abcdef0123456789"

FAKE_BDS_PROPERTIES = """\
# Bedrock Dedicated Server properties
online-mode = false
allow-cheats = true
level-name=world
"""


def build_bds_dir(
    server: Path,
    *,
    version: str | None = "26.51",
    properties: str | None = SERVER_PROPERTIES,
    executable: bool = True,
    packs: bool = True,
    worlds: bool = False,
    creative: bool = False,
) -> Path:
    """Create a synthetic (vanilla or earlier-Endstone) BDS directory."""

    server.mkdir(parents=True, exist_ok=True)
    if properties is not None:
        (server / "server.properties").write_text(properties, encoding="utf-8")
    if version is not None:
        (server / "version.txt").write_text(version, encoding="utf-8")
    if executable:
        (server / "bedrock_server").write_bytes(b"fake-bds-binary")
    if packs:
        for name in ("behavior_packs", "resource_packs", "definitions"):
            (server / name / "vanilla").mkdir(parents=True, exist_ok=True)
            (server / name / "vanilla" / "contents.json").write_text("{}\n", encoding="utf-8")
    if worlds:
        world = server / "worlds" / "world"
        world.mkdir(parents=True, exist_ok=True)
        tags = {"hasBeenLoadedInCreative": fakenbt.byte(1)} if creative else {}
        (world / "level.dat").write_bytes(fakenbt.level_dat(tags))
    return server


def simulate_bds_download(server: Path, version: str = "26.51") -> Path:
    """What Endstone's ``_download`` leaves behind (fresh files plus version.txt).

    Like the real step, an existing ``server.properties`` keeps its values.
    """

    server.mkdir(parents=True, exist_ok=True)
    if not (server / "server.properties").exists():
        (server / "server.properties").write_text(FAKE_BDS_PROPERTIES, encoding="utf-8")
    (server / "version.txt").write_text(version, encoding="utf-8")
    (server / "bedrock_server").write_bytes(b"fake-bds-binary")
    for name in ("behavior_packs", "resource_packs", "definitions"):
        (server / name / "vanilla").mkdir(parents=True, exist_ok=True)
    return server


def generate_key_pem() -> tuple[bytes, bytes]:
    """Return ``(private_pem, public_pem)`` for a fresh EC P-384 owner key pair."""

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    private_key = ec.generate_private_key(ec.SECP384R1())
    private_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_pem = private_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private_pem, public_pem


def write_endbot_toml(
    root: Path, *, gamertags: str = '["ExampleTag"]', control_port: int = 19142, server_path: str = "server"
) -> Path:
    path = root / "endbot.toml"
    path.write_text(
        ENDBOT_TOML_TEMPLATE.format(gamertags=gamertags, control_port=control_port, server_path=server_path),
        encoding="utf-8",
    )
    return path


def write_endstone_toml(
    server: Path, *, enabled: bool = True, public_key_file: str = "../state/secrets/owner-public.pem"
) -> Path:
    path = server / "endstone.toml"
    enabled_text = "true" if enabled else "false"
    path.write_text(
        f"[local-bot-auth]\nenabled = {enabled_text}\npublic-key-file = '{public_key_file}'\n",
        encoding="utf-8",
    )
    return path


def build_instance(root: Path, lock: LockData) -> InstancePaths:
    """Create a complete healthy instance: safe properties, matching keys, no world yet."""

    root.mkdir(parents=True, exist_ok=True)
    paths = InstancePaths.for_root(root)
    write_endbot_toml(root)
    server = root / "server"
    server.mkdir()
    (server / "server.properties").write_text(SERVER_PROPERTIES, encoding="utf-8")
    normalized = ".".join(str(part) for part in normalize_bds_version(lock.bds_version))
    (server / "version.txt").write_text(normalized, encoding="utf-8")
    paths.state_secrets.mkdir(parents=True)
    private_pem, public_pem = generate_key_pem()
    paths.owner_private_key.write_bytes(private_pem)
    paths.owner_public_key.write_bytes(public_pem)
    paths.control_token.write_text(CONTROL_TOKEN, encoding="utf-8")
    write_endstone_toml(server)
    return paths


def free_port() -> int:
    """Return a loopback TCP port that was free a moment ago."""

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def write_runtime_config(
    path: Path, *, data_dir: Path, token_path: Path, private_path: Path, public_path: Path, control_port: int
) -> Path:
    """Write the runtime JSON config the fake runtime child reads."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "dataDirectory": str(data_dir.resolve()),
                "controlTokenPath": str(token_path.resolve()),
                "ownerPrivateKeyPath": str(private_path.resolve()),
                "ownerPublicKeyPath": str(public_path.resolve()),
                "controlHost": "127.0.0.1",
                "controlPort": control_port,
                "serverHost": "127.0.0.1",
                "serverPort": 19132,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def fake_runtime_command(runtime_config: Path) -> list[str]:
    return [sys.executable, str(FAKES_DIR / "fakeruntime.py"), "--config", str(runtime_config)]


def fake_server_command() -> list[str]:
    return [
        sys.executable,
        str(FAKES_DIR / "fakebds.py"),
        "-m",
        "endstone",
        "-s",
        "server",
        "--no-interactive",
    ]


class LineFeed:
    """A blocking iterable of stdin lines the supervisor forwards to BDS."""

    def __init__(self) -> None:
        import queue

        self._lines: queue.Queue = queue.Queue()

    def feed(self, line: str) -> None:
        self._lines.put(line + "\n")

    def close(self) -> None:
        self._lines.put(None)

    def __iter__(self):
        return self

    def __next__(self) -> str:
        line = self._lines.get()
        if line is None:
            raise StopIteration
        return line
