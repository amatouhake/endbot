#!/usr/bin/env python3
"""Run an opt-in live smoke on a fresh, isolated Endstone/BDS world.

RakNet is the default here; ``--transport nethernet`` runs the same checks over
NetherNet LAN discovery (the product default), which needs UDP 7551 free.

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
from uuid import uuid4

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
from endbot_cli.controller_requests import SESSION_FILE, dispatch_requests
from endbot_cli.instance import InstancePaths
from endbot_cli.leveldat import parse_level_dat
from endbot_cli.netcheck import lan_discovery_port_problem
from endbot_cli.properties import (
    edit_properties,
    verify_server_properties,
)
from sandbox_server import reusable_server

BOT = "RakNetSpike"
SERVER_NAME = "Endbot RakNet spike"
LEVEL_NAME = "raknet-spike"


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


def observed_position(
    process: subprocess.Popen, logfile: Path
) -> tuple[float, float, float]:
    """Use the plugin's BDS observation, never the runtime's predicted position."""
    offset = logfile.stat().st_size
    console(process, f"bot {BOT} status")
    expression = re.compile(
        r"(?:Overworld|minecraft:overworld) (-?[\d.]+) (-?[\d.]+) (-?[\d.]+) yaw="
    )

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


def check_locked_endstone() -> dict:
    """Fail before any BDS acquisition when this Python lacks the locked Endstone."""
    lock = json.loads((ROOT / "endstone.lock").read_text(encoding="utf-8"))
    installed = importlib.metadata.version("endstone")
    if installed != lock["endstone"]["package_version"]:
        raise ConsumerError(
            f"expected patched Endstone {lock['endstone']['package_version']}, found {installed}"
        )
    return lock


def exercise(
    output: Path, node: str, report: dict, *, mining=False, inventory=False, inventory_stack=False,
    item_use=False, groups=False, scripting=False,
    transport="raknet", server: Path | None = None,
) -> None:
    scripting_runner = None
    if scripting:
        from scripting_probe import built_runner

        scripting_runner = built_runner(ROOT)
    lock = check_locked_endstone()
    plugin = importlib.metadata.version("endstone-endbot")
    entrypoints = list(importlib.metadata.entry_points(group="endstone"))
    if len(entrypoints) != 1 or entrypoints[0].name != "endbot":
        raise ConsumerError(
            "use a dedicated Python environment with only the Endbot plugin installed"
        )
    protocol = json.loads(
        (ROOT / "runtime/node_modules/bedrock-protocol/package.json").read_text()
    )["version"]
    pinned = json.loads((ROOT / "runtime/package.json").read_text())["dependencies"][
        "bedrock-protocol"
    ]
    if protocol != pinned:
        raise ConsumerError(
            f"expected bedrock-protocol {pinned}, found {protocol}; run npm ci --prefix runtime"
        )
    node_version = subprocess.run(
        [node, "--version"], capture_output=True, text=True, check=True
    ).stdout.strip()
    if int(node_version.lstrip("v").split(".")[0]) < 24:
        raise ConsumerError("Node.js 24+ is required")
    report.update(
        platform=platform.system(),
        endstone=lock["endstone"]["package_version"],
        plugin=plugin,
        bedrock_protocol=protocol,
        locked_bds=lock["bds"],
        transport=transport,
        backend="raknet-native" if transport == "raknet" else "nethernet-lan",
        node=node_version,
        python=platform.python_version(),
    )
    report["source_sha256"] = {
        str(source.relative_to(ROOT)).replace("\\", "/"): hashlib.sha256(
            source.read_bytes()
        ).hexdigest()
        for source in [
            Path(__file__),
            ROOT / "runtime/src/config.js",
            ROOT / "runtime/src/protocol-session.js",
            ROOT / "runtime/src/inventory-sync.js",
            ROOT / "runtime/src/packets.js",
            ROOT / "runtime/src/lifecycle.js",
            ROOT / "plugin/endbot/src/endstone_endbot/commands.py",
            ROOT / "plugin/endbot/src/endstone_endbot/world.py",
            ROOT / "plugin/endbot/src/endstone_endbot/inventory_sync.py",
            ROOT / "scripts/mining_probe.py",
            ROOT / "scripts/item_use_probe.py",
            ROOT / "plugin/endbot/src/endstone_endbot/plugin.py",
            ROOT / "plugin/endbot/src/endstone_endbot/controller_policy.py",
            ROOT / "plugin/endbot/src/endstone_endbot/dispatch.py",
            ROOT / "runtime/scripts/raknet-auth-probe.js",
            ROOT / "endstone.lock",
            ROOT / "runtime/package-lock.json",
        ]
    }
    if groups:
        for relative in (
            "scripts/group_probe.py", "plugin/endbot/src/endstone_endbot/groups.py",
            "runtime/src/control-server.js",
        ):
            report["source_sha256"][relative] = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
    if scripting:
        sources = [ROOT / "scripts/scripting_probe.py", ROOT / "runner/main.cpp", ROOT / "runner/api.luau",
                   ROOT / "runner/luau.lock.json", scripting_runner, scripting_runner.with_name("api.luau")]
        sources.extend((ROOT / "plugin/endbot/src/endstone_endbot").glob("script*.py"))
        for source in sources:
            relative = source.relative_to(ROOT).as_posix()
            report["source_sha256"][relative] = hashlib.sha256(source.read_bytes()).hexdigest()
    if transport == "nethernet":
        # BDS cannot answer discovery while another program owns the port.
        problem = lan_discovery_port_problem()
        if problem:
            raise ConsumerError(problem)
    # Fresh output only: do not copy or mutate an operator's world or credentials.
    output.mkdir(parents=True, exist_ok=False)
    if os.name != "nt":
        output.chmod(0o700)
    # A reused sandbox (see sandbox_server.py) arrives installed and reset.
    reused = server is not None
    report["sandbox_server_reused"] = reused
    if not reused:
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
            "transport": transport,
            "server-port": str(server_port),
            "server-portv6": str(ipv6_port),
            "server-name": SERVER_NAME,
            "level-name": LEVEL_NAME,
            "allow-list": "true",
            # NetherNet Bots find BDS only through its LAN advertisement.
            "enable-lan-visibility": str(transport == "nethernet").lower(),
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
    paths.state_run.mkdir(parents=True, exist_ok=True)
    controller_session = str(uuid4())
    (paths.state_run / "supervisor.pid").write_text(str(os.getpid()), encoding="utf-8")
    (paths.state_run / SESSION_FILE).write_text(
        json.dumps({"session": controller_session}), encoding="utf-8"
    )
    (output / "endbot.toml").write_text(
        f"[server]\npath='{server if reused else 'server'}'\ntransport='{transport}'\n[controllers]\ngamertags=[]\n"
        f"[runtime]\ncontrol-port={control_port}\n",
        encoding="utf-8",
    )
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
                "transport": transport,
                # Select this BDS among every NetherNet host on the machine;
                # a fresh world advertises its level-name as display name.
                **(
                    {"serverName": SERVER_NAME, "levelName": LEVEL_NAME}
                    if transport == "nethernet"
                    else {}
                ),
                # BDS 1.26.52 still uses protocol 2193 and the pinned 1.26.51
                # wire schema; the protocol library has no 1.26.52 schema key.
                "gameVersion": json.loads(
                    (ROOT / "runtime/endbot-runtime.example.json").read_text()
                )["gameVersion"],
                "protocol": lock["bds"]["protocol"],
                "reconnect": {"maximumAttempts": 0},
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(ROOT / "plugin/endbot/src"), str(ROOT / "cli/endbot/src")]
    )
    environment["PYTHONIOENCODING"] = "utf-8"
    if mining or inventory or item_use or inventory_stack or scripting:
        from mining_probe import configure_fixture
        configure_fixture(ROOT, output, environment)
    if item_use:
        from item_use_probe import configure_fixture as configure_item_use
        configure_item_use(ROOT, output, environment)
    if inventory_stack:
        from inventory_stack_probe import configure_fixture as configure_stack

        configure_stack(ROOT, output, environment)
    if scripting:
        from scripting_probe import configure_fixture as configure_scripting

        configure_scripting(ROOT, output, environment)
    runtime = bds = None
    token = None
    with (
        (output / "runtime.log").open("w", encoding="utf-8") as runtime_log,
        (output / "server.log").open("w", encoding="utf-8") as server_log,
    ):
        try:
            report["stage"] = "runtime initialization"
            runtime = subprocess.Popen(
                [node, *(["--import", (ROOT / "runtime/scripts/inventory-stack-probe.js").as_uri()]
                         if inventory_stack else []),
                 str(ROOT / "runtime/src/cli.js"), "--config", str(configfile)],
                env=environment,
                stdout=runtime_log,
                stderr=subprocess.STDOUT,
                start_new_session=os.name != "nt",
            )

            def runtime_ready():
                if not paths.control_token.is_file():
                    return False
                try:
                    control_request(
                        control_port, paths.control_token.read_text().strip(), "ping"
                    )
                    return paths.owner_public_key.is_file()
                except (OSError, ValueError, ConsumerError):
                    return False

            wait_for("runtime ready", runtime_ready, 30, runtime)
            token = paths.control_token.read_text().strip()
            config = EndbotConfig(
                ServerConfig(str(server) if reused else "server", transport),
                ControllersConfig(()),
                RuntimeConfig(control_port),
            )
            plugin_config, _ = generate_plugin_config(server, paths, config)
            if scripting:
                from scripting_probe import enable_config

                enable_config(plugin_config, scripting_runner)
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
                return (output / "server.log").read_text(
                    encoding="utf-8", errors="replace"
                )

            wait_for(
                "Endbot enabled and BDS started",
                lambda: (
                    "Enabling endbot" in logtext() and "Server started" in logtext()
                ),
                180,
                bds,
            )
            version = re.search(r"Version: ([\d.]+)", logtext())
            build = re.search(r"Build ID: (\d+)", logtext())
            if not version or version.group(1) != lock["bds"]["version"]:
                raise ConsumerError(
                    "BDS startup version does not match endstone.lock; refusing Bot login"
                )
            report["observed_bds"] = {
                "version": version.group(1),
                "build": int(build.group(1)) if build else None,
            }
            report["stage"] = "live controller management"
            policy_file = paths.state_controllers.parent / "controller-policy.json"

            def management(command, expected):
                offset = len(logtext())
                console(bds, f"botcontrollers {command}")
                wait_for(
                    "controller console response",
                    lambda expected=expected, offset=offset: expected in logtext()[offset:],
                    10,
                    bds,
                )

            def cli_command(arguments, expected, code=0):
                child = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "endbot_cli",
                        "--instance",
                        str(output),
                        *arguments,
                    ],
                    env=environment,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                )

                def send(line):
                    console(bds, line)
                    return True

                deadline = time.monotonic() + 25
                while child.poll() is None and time.monotonic() < deadline:
                    dispatch_requests(paths.state_run, controller_session, send)
                    time.sleep(0.02)
                if child.poll() is None:
                    terminate_owned_tree(child)
                stdout, stderr = child.communicate()
                if child.returncode != code or expected not in stdout + stderr:
                    raise ConsumerError(
                        f"CLI result mismatch: {arguments}: {stdout} {stderr}"
                    )
                if "queued for" in stdout:
                    raise ConsumerError(
                        "controller CLI reported submission instead of execution"
                    )

            cli_command(["controllers", "add", "CLI Pending"], "CLI Pending: pending")
            cli_command(["controllers", "list"], "CLI Pending: pending")
            cli_command(["doctor", "--live"], "CLI Pending: pending", code=0)
            cli_command(
                ["controllers", "remove", "MissingController"],
                "Unknown offline controller",
                code=1,
            )
            cli_command(
                ["controllers", "remove", "CLI Pending"],
                "controller CLI Pending revoked",
            )
            report["controller_cli_correlated_results"] = True
            report["controller_doctor_live"] = True

            management(
                'add "Pending Controller"', "controller Pending Controller updated"
            )
            management("list", "Pending Controller: pending")
            management(
                'remove "Pending Controller"', "controller Pending Controller revoked"
            )
            policy = json.loads(policy_file.read_text(encoding="utf-8"))
            if policy["pending"] or "pending controller" not in policy["deniedNames"]:
                raise ConsumerError("controller removal was not persisted")
            management("add RestartController", "controller RestartController updated")
            report["controller_console_pending_add_remove"] = True
            report["stage"] = "local-bot login / spawn"
            console(bds, f"bot {BOT} spawn")

            def online():
                try:
                    return (
                        control_request(control_port, token, "status", name=BOT)[
                            "connectionState"
                        ]
                        == "online"
                    )
                except ConsumerError:
                    return False  # The console command may not have created the profile yet.

            try:
                wait_for("Bot online", online, 60, bds)
            except ConsumerError as error:
                # Keep the runtime's reason (for example a discovery miss).
                try:
                    status = control_request(control_port, token, "status", name=BOT)
                    detail = f"{status['connectionState']}: {status['lastError']}"
                except ConsumerError as status_error:
                    detail = str(status_error)
                raise ConsumerError(f"{error} ({detail})") from error
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
            prior_policy = policy_file.read_bytes()
            management(f"add {BOT}", "Only normally authenticated players with an XUID")
            if policy_file.read_bytes() != prior_policy:
                raise ConsumerError("local Bot enrollment changed controller policy")
            report["controller_local_bot_rejected"] = True
            for command, expected in (
                (f"bot {BOT} move forward", "movement set to forward"),
                (f"bot {BOT} stop", "movement and actions stopped"),
                (f"bot {BOT} jump", "jump once requested"),
                (f"bot {BOT} look 45 0", "look set to yaw=45.0 pitch=0.0"),
            ):
                offset = len(logtext())
                console(bds, command)
                wait_for(
                    "operation-specific reply",
                    lambda expected=expected, offset=offset: expected in logtext()[offset:],
                    10,
                    bds,
                )
            report["bot_operation_specific_replies"] = True
            if scripting:
                from scripting_probe import exercise_scripting

                report["stage"] = "scripting console commands and authoritative BDS state"
                exercise_scripting(
                    output, lambda line: console(bds, line),
                    lambda method, **params: control_request(control_port, token, method, **params),
                    lambda label, predicate, timeout: wait_for(label, predicate, timeout, bds), logtext, report,
                )
            if groups:
                from group_probe import exercise_groups

                report["stage"] = "Group native parser and bulk operations"
                exercise_groups(
                    BOT, lambda line: console(bds, line),
                    lambda method, **params: control_request(control_port, token, method, **params),
                    lambda label, predicate, timeout: wait_for(label, predicate, timeout, bds), logtext, report,
                )
            if mining:
                from mining_probe import exercise_mining
                report["stage"] = "Survival block mining"
                exercise_mining(
                    output, lambda line: console(bds, line),
                    lambda method, **params: control_request(control_port, token, method, name=BOT, **params),
                    lambda label, predicate, timeout: wait_for(label, predicate, timeout, bds), report,
                    log=logtext,
                )
            if item_use:
                from item_use_probe import exercise_item_use

                report["stage"] = "timed item use and equipment synchronization"
                exercise_item_use(
                    output, lambda line: console(bds, line),
                    lambda method, **params: control_request(control_port, token, method, name=BOT, **params),
                    lambda label, predicate, timeout: wait_for(label, predicate, timeout, bds), logtext, report,
                )
            if inventory_stack:
                from inventory_stack_probe import exercise_inventory_stack

                # Gather spike evidence even when provisional product operations fail.
                # The spike deactivates its observer before the product fixture runs.
                report["stage"] = "inventory stack requests"
                exercise_inventory_stack(
                    output, lambda line: console(bds, line),
                    lambda method, **params: control_request(control_port, token, method, name=BOT, **params),
                    lambda label, predicate, timeout: wait_for(label, predicate, timeout, bds), report,
                )
            if inventory:
                from mining_probe import exercise_inventory

                report["stage"] = "natural pickup and hotbar/drop"
                exercise_inventory(
                    output,
                    lambda line: console(bds, line),
                    lambda method, **params: control_request(
                        control_port, token, method, name=BOT, **params
                    ),
                    lambda label, predicate, timeout: wait_for(
                        label, predicate, timeout, bds
                    ),
                    report,
                )
            identity = control_request(control_port, token, "status", name=BOT)[
                "identityId"
            ]
            # Let a fresh survival spawn finish falling before taking observations.
            time.sleep(3)
            report["stage"] = "per-Bot radius commands"
            default_radius = control_request(control_port, token, "radius", name=BOT)[
                "chunkRadius"
            ]
            control_request(control_port, token, "spawn", name="RadiusBuddy")
            wait_for(
                "second Bot online",
                lambda: (
                    control_request(control_port, token, "status", name="RadiusBuddy")[
                        "connectionState"
                    ]
                    == "online"
                ),
                45,
                bds,
            )
            for suffix, requested, source in (
                ("12", 12, "Bot override"),
                ("default", default_radius, "shared default"),
            ):
                offset = len(logtext())
                console(bds, f"bot {BOT} radius {suffix}")
                wait_for(
                    f"native radius {suffix} command",
                    lambda expected=f"chunk radius {requested} ({source})", offset=offset: (
                        expected in logtext()[offset:]
                    ),
                    15,
                    bds,
                )
                wait_for(
                    "server radius acknowledgement",
                    lambda: (
                        control_request(control_port, token, "radius", name=BOT)[
                            "serverChunkRadius"
                        ]
                        is not None
                    ),
                    15,
                    bds,
                )
                result = control_request(control_port, token, "radius", name=BOT)
                report.setdefault("radius_commands", {})[suffix] = {
                    key: result[key]
                    for key in ("chunkRadius", "chunkRadiusSource", "serverChunkRadius")
                }
                if suffix == "12":
                    control_request(control_port, token, "reconnect", name=BOT)
                    wait_for("radius reconnect", online, 45, bds)
                    if (
                        control_request(control_port, token, "radius", name=BOT)[
                            "chunkRadius"
                        ]
                        != 12
                    ):
                        raise ConsumerError("reconnect lost the Bot radius override")
                    inherited = control_request(
                        control_port, token, "radius", name="RadiusBuddy"
                    )
                    if (
                        inherited["chunkRadiusSource"] != "default"
                        or inherited["chunkRadius"] != default_radius
                    ):
                        raise ConsumerError("Bot radius override affected another Bot")
            control_request(control_port, token, "despawn", name="RadiusBuddy")
            report["radius_reconnect_and_isolation"] = True
            report["stage"] = "movement"
            before = observed_position(bds, output / "server.log")
            control_request(control_port, token, "move", name=BOT, direction="forward")
            time.sleep(1)
            control_request(control_port, token, "stop", name=BOT)
            after = observed_position(bds, output / "server.log")
            distance = (
                (after[0] - before[0]) ** 2 + (after[2] - before[2]) ** 2
            ) ** 0.5
            report["movement_distance_bds"] = round(distance, 2)
            if distance < 0.5:
                raise ConsumerError(
                    "BDS did not observe meaningful horizontal movement"
                )
            time.sleep(2)
            report["stage"] = "timed movement and jump"
            # Native parser path: typed console commands, not the control port.
            offset = len(logtext())
            before = observed_position(bds, output / "server.log")
            console(bds, f"bot {BOT} move forward for 20")
            wait_for(
                "move for reply",
                lambda: "movement set to forward for 20 ticks" in logtext()[offset:],
                10,
                bds,
            )
            wait_for(
                "timed movement expired",
                lambda: control_request(control_port, token, "status", name=BOT)["inputs"]["movement"] is None,
                5,
                bds,
            )
            time.sleep(0.5)
            stopped = observed_position(bds, output / "server.log")
            time.sleep(1)
            settled = observed_position(bds, output / "server.log")
            moved = ((stopped[0] - before[0]) ** 2 + (stopped[2] - before[2]) ** 2) ** 0.5
            drift = ((settled[0] - stopped[0]) ** 2 + (settled[2] - stopped[2]) ** 2) ** 0.5
            report["timed_movement_bds"] = {"moved": round(moved, 2), "drift_after_expiry": round(drift, 3)}
            if moved < 0.5 or drift > 0.05:
                raise ConsumerError(f"move for did not move then stop: {report['timed_movement_bds']}")
            offset = len(logtext())
            console(bds, f"bot {BOT} jump interval 10 for 30")
            wait_for(
                "jump interval for reply",
                lambda: "jump started every 10 ticks for 30 ticks" in logtext()[offset:],
                10,
                bds,
            )
            wait_for(
                "timed jump expired",
                lambda: "jump" not in control_request(control_port, token, "status", name=BOT)["inputs"]["actions"],
                5,
                bds,
            )
            report["timed_jump_expired"] = True
            time.sleep(1)
            report["stage"] = "jump"
            base = observed_position(bds, output / "server.log")
            control_request(
                control_port, token, "action", name=BOT, action="jump", mode="once"
            )
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
                    control_request(control_port, token, "status", name=BOT)[
                        "connectionState"
                    ]
                    == "online"
                    and logtext().count(f"Player Spawned: {BOT}") > count
                ),
                60,
                bds,
            )
            if (
                control_request(control_port, token, "status", name=BOT)["identityId"]
                != identity
            ):
                raise ConsumerError("reconnect changed the Bot identity")
            report["reconnect_same_identity"] = True
            report["stage"] = "despawn"
            control_request(control_port, token, "despawn", name=BOT)
            wait_for(
                "Bot offline",
                lambda: (
                    control_request(control_port, token, "status", name=BOT)[
                        "connectionState"
                    ]
                    == "offline"
                ),
                15,
                bds,
            )
            report["despawn"] = True

            def negative_probe(variant):
                offset = len(logtext())
                probe = subprocess.run(
                    [
                        node,
                        str(ROOT / "runtime/scripts/raknet-auth-probe.js"),
                        str(configfile),
                        variant,
                    ],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=45,
                    check=False,
                )
                if probe.returncode:
                    raise ConsumerError(
                        f"negative auth {variant} failed: {probe.stdout} {probe.stderr}"
                    )
                result = json.loads(probe.stdout.splitlines()[-1])
                if not result["ok"]:
                    raise ConsumerError(
                        f"negative auth {variant} did not confirm rejection"
                    )
                if (
                    "Accepted local bot" in logtext()[offset:]
                    or "Player Spawned: RakNetNegative" in logtext()[offset:]
                ):
                    raise ConsumerError(
                        f"BDS accepted the negative auth probe {variant}"
                    )
                report.setdefault("negative_auth", {})[variant] = result

            report["stage"] = "negative local-bot auth"
            for variant in ("wrong-key", "wrong-issuer", "wrong-audience"):
                negative_probe(variant)

            # Reload disabled local-bot auth on the same isolated server, then
            # prove even a valid owner token is rejected by the vanilla validator.
            console(bds, "stop")
            bds.wait(timeout=45)
            if bds.returncode:
                raise ConsumerError(
                    "BDS did not stop cleanly before the auth-disabled gate"
                )
            endstone_config = server / "endstone.toml"
            import tomlkit

            document = tomlkit.parse(endstone_config.read_text(encoding="utf-8"))
            document["local-bot-auth"]["enabled"] = False
            endstone_config.write_text(tomlkit.dumps(document), encoding="utf-8")
            prior_starts = logtext().count("Server started")
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
            wait_for(
                "BDS restarted with local-bot auth disabled",
                lambda: logtext().count("Server started") > prior_starts,
                180,
                bds,
            )
            management("list", "RestartController: pending")
            management("list", "pending controller: revoked")
            if policy_file.read_bytes() != prior_policy:
                raise ConsumerError("BDS restart changed controller policy")
            report["controller_policy_restart"] = True
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
            (paths.state_run / "supervisor.pid").unlink(missing_ok=True)
            (paths.state_run / SESSION_FILE).unlink(missing_ok=True)
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
    summary = parse_level_dat((server / "worlds" / LEVEL_NAME / "level.dat").read_bytes())
    history = asdict(summary)
    report["world_history"] = history
    required = (
        summary.commands_enabled,
        summary.cheats_enabled,
        summary.has_been_loaded_in_creative,
        summary.experiments_ever_used,
        summary.saved_with_toggled_experiments,
    )
    if (
        any(flag is not False for flag in required)
        or summary.other_experiment_keys
        or summary.game_type != 0
    ):
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
    parser.add_argument(
        "--node", default=shutil.which("node"), help="Node.js 24+ executable"
    )
    parser.add_argument("--mining", action="store_true", help="prepare disposable blocks/tools and validate Survival mining")
    parser.add_argument("--inventory", action="store_true", help="validate natural pickups and hotbar/drop without reconnect")
    parser.add_argument("--inventory-stack", action="store_true", help="record stack requests/responses and authoritative inventory in disposable fixtures")
    parser.add_argument("--item-use", action="store_true", help="validate timed trident/bow/food use and observe sync cost")
    parser.add_argument("--groups", action="store_true", help="validate shared Group commands through real BDS")
    parser.add_argument("--scripting", action="store_true", help="enable scripting only in the disposable instance and validate native runs/stop/budgets with BDS observations")
    parser.add_argument(
        "--server-dir",
        type=Path,
        help="reusable sandbox BDS directory (reset before the run); keeps one Windows Firewall rule across runs",
    )
    parser.add_argument(
        "--transport",
        choices=("raknet", "nethernet"),
        default="raknet",
        help="Bot/BDS transport (nethernet needs UDP 7551 free)",
    )
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
        options = {"mining": args.mining, "inventory": args.inventory, "inventory_stack": args.inventory_stack,
                   "item_use": args.item_use,
                   "groups": args.groups, "scripting": args.scripting, "transport": args.transport}
        if args.server_dir is None:
            exercise(output, args.node, report, **options)
        else:
            # A wrong environment must not install its BDS into the shared sandbox.
            check_locked_endstone()
            report["stage"] = "sandbox BDS preparation"
            with reusable_server(args.server_dir) as server:
                exercise(output, args.node, report, server=server, **options)
    except (Exception, KeyboardInterrupt) as error:  # noqa: BLE001 - persist the failed layer for any upstream failure
        report["error"] = str(error)
        print(f"raknet-spike: FAIL at {report['stage']}: {error}", file=sys.stderr)
    finally:
        if output.is_dir():
            (output / "report.json").write_text(
                json.dumps(report, indent=2) + "\n", encoding="utf-8"
            )
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
