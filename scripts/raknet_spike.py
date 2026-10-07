#!/usr/bin/env python3
"""Run an opt-in RakNet feasibility smoke on a fresh, isolated Endstone/BDS world.

Uses the active Python's exactly pinned patched Endstone and installed plugin,
the repository runtime, and Endstone's normal BDS acquisition. Never adopts an
existing server, weakens online authentication, or certifies Xbox achievements.
Raw logs, keys, Bot profiles and the report belong in an ignored output directory.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "cli" / "endbot" / "src"))

from e2e_consumer import (
    ConsumerError,
    control_request,
    kill_tree,
    wait_for,
)
from endbot_cli.bds import install_locked_bds
from endbot_cli.config import (
    ControllersConfig,
    EndbotConfig,
    RuntimeConfig,
    ServerConfig,
)
from endbot_cli.configgen import (
    generate_endstone_config,
    generate_plugin_config,
)
from endbot_cli.instance import InstancePaths
from endbot_cli.leveldat import parse_level_dat
from endbot_cli.properties import (
    edit_properties,
    verify_server_properties,
)

BOT = "RakNetSpike"


def terminate_owned_tree(process: subprocess.Popen) -> None:
    if os.name == "nt":
        kill_tree(process)
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=30)


def free_port(kind: int) -> int:
    with socket.socket(socket.AF_INET, kind) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def console(process: subprocess.Popen, command: str) -> None:
    process.stdin.write(command + "\n")
    process.stdin.flush()


def observed_position(process: subprocess.Popen, logfile: Path) -> tuple[float, float, float]:
    """Use the plugin's BDS observation, never the runtime's predicted position."""
    offset = logfile.stat().st_size
    console(process, f"bot {BOT} status")
    expression = re.compile(r"(?:Overworld|minecraft:overworld) (-?[\d.]+) (-?[\d.]+) (-?[\d.]+) yaw=")

    def latest():
        with logfile.open("rb") as stream:
            stream.seek(offset)
            return expression.search(stream.read().decode("utf-8", errors="replace"))

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        match = latest()
        if match:
            return tuple(float(value) for value in match.groups())
        if process.poll() is not None:
            raise ConsumerError("BDS exited during position observation")
        time.sleep(0.02)
    raise ConsumerError("timed out waiting for BDS position observation")


def exercise(output: Path, node: str, report: dict) -> None:
    lock = json.loads((ROOT / "endstone.lock").read_text(encoding="utf-8"))
    installed = importlib.metadata.version("endstone")
    if installed != lock["endstone"]["package_version"]:
        raise ConsumerError(f"expected patched Endstone {lock['endstone']['package_version']}, found {installed}")
    plugin = importlib.metadata.version("endstone-endbot")
    entrypoints = list(importlib.metadata.entry_points(group="endstone"))
    if len(entrypoints) != 1 or entrypoints[0].name != "endbot":
        raise ConsumerError("use a dedicated Python environment with only the Endbot plugin installed")
    protocol = json.loads((ROOT / "runtime/node_modules/bedrock-protocol/package.json").read_text())["version"]
    pinned = json.loads((ROOT / "runtime/package.json").read_text())["dependencies"]["bedrock-protocol"]
    if protocol != pinned:
        raise ConsumerError(f"expected bedrock-protocol {pinned}, found {protocol}; run npm ci --prefix runtime")
    node_version = subprocess.run([node, "--version"], capture_output=True, text=True, check=True).stdout.strip()
    if int(node_version.lstrip("v").split(".")[0]) < 24:
        raise ConsumerError("Node.js 24+ is required")
    report.update(
        platform=platform.system(),
        endstone=installed,
        plugin=plugin,
        bedrock_protocol=protocol,
        locked_bds=lock["bds"],
        transport="raknet",
        backend="raknet-native",
        node=node_version,
        python=platform.python_version(),
    )
    report["source_sha256"] = {
        str(source.relative_to(ROOT)).replace("\\", "/"): hashlib.sha256(source.read_bytes()).hexdigest()
        for source in [
            Path(__file__),
            ROOT / "runtime/src/config.js",
            ROOT / "runtime/src/protocol-session.js",
            ROOT / "runtime/scripts/raknet-auth-probe.js",
            ROOT / "endstone.lock",
            ROOT / "runtime/package-lock.json",
        ]
    }
    # Fresh output only: do not copy or mutate an operator's world or credentials.
    output.mkdir(parents=True, exist_ok=False)
    if os.name != "nt":
        output.chmod(0o700)
    server = output / "server"
    server.mkdir()
    report["stage"] = "BDS acquisition"
    install_locked_bds(server)
    server_port = free_port(socket.SOCK_DGRAM)
    ipv6_port = free_port(socket.SOCK_DGRAM)
    while ipv6_port == server_port:
        ipv6_port = free_port(socket.SOCK_DGRAM)
    control_port = free_port(socket.SOCK_STREAM)
    edit_properties(
        server / "server.properties",
        {
            "online-mode": "true",
            "allow-cheats": "false",
            "gamemode": "survival",
            "transport": "raknet",
            "server-port": str(server_port),
            "server-portv6": str(ipv6_port),
            "server-name": "Endbot RakNet spike",
            "level-name": "raknet-spike",
            "allow-list": "true",
            "enable-lan-visibility": "false",
        },
    )
    verify_server_properties(server / "server.properties")
    (server / "allowlist.json").write_text("[]\n", encoding="utf-8")
    report["server_properties"] = {
        "online-mode": True,
        "allow-cheats": False,
        "allow-list": True,
    }
    paths = InstancePaths.for_root(output)
    configfile = output / "runtime.json"
    configfile.write_text(
        json.dumps(
            {
                "dataDirectory": "state",
                "controlTokenPath": str(paths.control_token),
                "ownerPrivateKeyPath": str(paths.owner_private_key),
                "ownerPublicKeyPath": str(paths.owner_public_key),
                "controlPort": control_port,
                "serverHost": "127.0.0.1",
                "serverPort": server_port,
                "transport": "raknet",
                "gameVersion": lock["bds"]["version"].rsplit(".", 1)[0],
                "protocol": lock["bds"]["protocol"],
                "reconnect": {"maximumAttempts": 0},
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join([str(ROOT / "plugin/endbot/src"), str(ROOT / "cli/endbot/src")])
    environment["PYTHONIOENCODING"] = "utf-8"
    runtime = bds = None
    token = None
    with (
        (output / "runtime.log").open("w", encoding="utf-8") as runtime_log,
        (output / "server.log").open("w", encoding="utf-8") as server_log,
    ):
        try:
            report["stage"] = "runtime initialization"
            runtime = subprocess.Popen(
                [node, str(ROOT / "runtime/src/cli.js"), "--config", str(configfile)],
                env=environment,
                stdout=runtime_log,
                stderr=subprocess.STDOUT,
                start_new_session=os.name != "nt",
            )

            def runtime_ready():
                if not paths.control_token.is_file():
                    return False
                try:
                    control_request(control_port, paths.control_token.read_text().strip(), "ping")
                    return paths.owner_public_key.is_file()
                except (OSError, ValueError, ConsumerError):
                    return False

            wait_for("runtime ready", runtime_ready, 30, runtime)
            token = paths.control_token.read_text().strip()
            config = EndbotConfig(
                ServerConfig("server"),
                ControllersConfig(()),
                RuntimeConfig(control_port),
            )
            generate_plugin_config(server, paths, config)
            generate_endstone_config(server, paths)
            report["stage"] = "BDS startup"
            bds = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "endbot_cli.endstone_entry",
                    "-s",
                    str(server),
                    "-y",
                    "--no-interactive",
                ],
                env=environment,
                stdin=subprocess.PIPE,
                stdout=server_log,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                start_new_session=os.name != "nt",
            )

            def logtext():
                return (output / "server.log").read_text(encoding="utf-8", errors="replace")

            wait_for(
                "Endbot enabled and BDS started",
                lambda: "Enabling endbot" in logtext() and "Server started" in logtext(),
                180,
                bds,
            )
            version = re.search(r"Version: ([\d.]+)", logtext())
            build = re.search(r"Build ID: (\d+)", logtext())
            if not version or version.group(1) != lock["bds"]["version"]:
                raise ConsumerError("BDS startup version does not match endstone.lock; refusing Bot login")
            report["observed_bds"] = {
                "version": version.group(1),
                "build": int(build.group(1)) if build else None,
            }
            report["stage"] = "local-bot login / spawn"
            console(bds, f"bot {BOT} spawn")

            def online():
                try:
                    return control_request(control_port, token, "status", name=BOT)["connectionState"] == "online"
                except ConsumerError:
                    return False  # The console command may not have created the profile yet.

            wait_for(
                "Bot online",
                online,
                60,
                bds,
            )
            wait_for(
                "BDS accepted local bot and spawned it",
                lambda: (
                    f"Accepted local bot '{BOT}'" in logtext()
                    and f"{BOT} joined the game" in logtext()
                    and f"Player Spawned: {BOT}" in logtext()
                ),
                30,
                bds,
            )
            report["spawn"] = True
            identity = control_request(control_port, token, "status", name=BOT)["identityId"]
            # Let a fresh survival spawn finish falling before taking observations.
            time.sleep(3)
            before = observed_position(bds, output / "server.log")
            report["stage"] = "movement"
            control_request(control_port, token, "move", name=BOT, direction="forward")
            time.sleep(1)
            control_request(control_port, token, "stop", name=BOT)
            after = observed_position(bds, output / "server.log")
            distance = ((after[0] - before[0]) ** 2 + (after[2] - before[2]) ** 2) ** 0.5
            report["movement_distance_bds"] = round(distance, 2)
            if distance < 0.5:
                raise ConsumerError("BDS did not observe meaningful horizontal movement")
            time.sleep(2)
            report["stage"] = "jump"
            base = observed_position(bds, output / "server.log")
            control_request(control_port, token, "action", name=BOT, action="jump", mode="once")
            heights = []
            for _ in range(8):
                time.sleep(0.1)
                heights.append(observed_position(bds, output / "server.log")[1])
            rise = max(heights) - base[1]
            report["jump_rise_bds"] = round(rise, 2)
            if rise < 0.25:
                raise ConsumerError("BDS did not observe the jump")
            report["stage"] = "reconnect"
            count = logtext().count(f"Player Spawned: {BOT}")
            control_request(control_port, token, "reconnect", name=BOT)
            wait_for(
                "Bot reconnected",
                lambda: (
                    control_request(control_port, token, "status", name=BOT)["connectionState"] == "online"
                    and logtext().count(f"Player Spawned: {BOT}") > count
                ),
                60,
                bds,
            )
            if control_request(control_port, token, "status", name=BOT)["identityId"] != identity:
                raise ConsumerError("reconnect changed the Bot identity")
            report["reconnect_same_identity"] = True
            report["stage"] = "despawn"
            control_request(control_port, token, "despawn", name=BOT)
            wait_for(
                "Bot offline",
                lambda: control_request(control_port, token, "status", name=BOT)["connectionState"] == "offline",
                15,
                bds,
            )
            report["despawn"] = True

            def negative_probe(variant):
                offset = len(logtext())
                probe = subprocess.run(
                    [node, str(ROOT / "runtime/scripts/raknet-auth-probe.js"), str(configfile), variant],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=25,
                    check=False,
                )
                if probe.returncode:
                    raise ConsumerError(f"negative auth {variant} failed: {probe.stdout} {probe.stderr}")
                result = json.loads(probe.stdout.splitlines()[-1])
                if not result["ok"]:
                    raise ConsumerError(f"negative auth {variant} did not confirm rejection")
                if "Accepted local bot" in logtext()[offset:] or "Player Spawned: RakNetNegative" in logtext()[offset:]:
                    raise ConsumerError(f"BDS accepted the negative auth probe {variant}")
                report.setdefault("negative_auth", {})[variant] = result

            report["stage"] = "negative local-bot auth"
            for variant in ("wrong-key", "wrong-issuer", "wrong-audience"):
                negative_probe(variant)

            # Reload disabled local-bot auth on the same isolated server, then
            # prove even a valid owner token is rejected by the vanilla validator.
            console(bds, "stop")
            bds.wait(timeout=45)
            if bds.returncode:
                raise ConsumerError("BDS did not stop cleanly before the auth-disabled gate")
            endstone_config = server / "endstone.toml"
            import tomlkit

            document = tomlkit.parse(endstone_config.read_text(encoding="utf-8"))
            document["local-bot-auth"]["enabled"] = False
            endstone_config.write_text(tomlkit.dumps(document), encoding="utf-8")
            prior_starts = logtext().count("Server started")
            bds = subprocess.Popen(
                [sys.executable, "-m", "endbot_cli.endstone_entry", "-s", str(server), "-y", "--no-interactive"],
                env=environment,
                stdin=subprocess.PIPE,
                stdout=server_log,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                start_new_session=os.name != "nt",
            )
            wait_for(
                "BDS restarted with local-bot auth disabled",
                lambda: logtext().count("Server started") > prior_starts,
                180,
                bds,
            )
            negative_probe("auth-disabled")
            report["stage"] = "clean shutdown"
            console(bds, "stop")
            bds.wait(timeout=45)
            control_request(control_port, token, "shutdown")
            runtime.wait(timeout=15)
            if bds.returncode or runtime.returncode:
                raise ConsumerError("server/runtime did not exit cleanly")
            report["clean_shutdown"] = True
        finally:
            if bds is not None and bds.poll() is None:
                try:
                    console(bds, "stop")
                    bds.wait(timeout=45)
                except (OSError, subprocess.TimeoutExpired):
                    terminate_owned_tree(bds)
            if runtime is not None and runtime.poll() is None:
                try:
                    if token:
                        control_request(control_port, token, "shutdown")
                        runtime.wait(timeout=15)
                    else:
                        runtime.terminate()
                        runtime.wait(timeout=10)
                except (OSError, ConsumerError, subprocess.TimeoutExpired):
                    terminate_owned_tree(runtime)
    report["stage"] = "post-session world history"
    summary = parse_level_dat((server / "worlds/raknet-spike/level.dat").read_bytes())
    history = asdict(summary)
    report["world_history"] = history
    required = (
        summary.commands_enabled,
        summary.cheats_enabled,
        summary.has_been_loaded_in_creative,
        summary.experiments_ever_used,
        summary.saved_with_toggled_experiments,
    )
    if any(flag is not False for flag in required) or summary.other_experiment_keys or summary.game_type != 0:
        raise ConsumerError("world history is unsafe or incomplete; inspect the report")
    report["stage"] = "complete"
    report["ok"] = True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="new ignored directory; never an existing instance",
    )
    parser.add_argument("--node", default=shutil.which("node"), help="Node.js 24+ executable")
    args = parser.parse_args()
    output = args.output.resolve()
    # Protect raw artifacts from an accidental git add, even for outputs outside build/.
    if output.exists():
        parser.error("--output must not exist; choose a fresh directory")
    ignored = subprocess.run(
        ["git", "check-ignore", "--quiet", str(output)],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )
    if ignored.returncode != 0:
        parser.error("--output must be git-ignored (for example build/raknet-spike-1)")
    if not args.node:
        parser.error("Node.js 24+ is required")
    report = {
        "ok": False,
        "stage": "prerequisites",
        "human_xbox_join_observed": False,
        "xbox_achievement_observed": False,
    }
    try:
        exercise(output, args.node, report)
    except (Exception, KeyboardInterrupt) as error:  # noqa: BLE001 - persist the failed layer for any upstream failure
        report["error"] = str(error)
        print(f"raknet-spike: FAIL at {report['stage']}: {error}", file=sys.stderr)
    finally:
        if output.is_dir():
            (output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
