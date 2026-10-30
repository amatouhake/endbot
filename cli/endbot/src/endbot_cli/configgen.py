"""Regenerating the derived configuration files on every start (docs/OPERATIONS.md section 2).

Four files are derived from ``endbot.toml`` plus ``state/`` and rewritten
atomically on every ``endbot start``:

- ``state/generated/endbot-runtime.json`` — the runtime config;
- ``state/generated/endbotd.toml`` — endbotd's config (docs/ENDBOTD.md P7):
  authorization, limits, UI, scripting, the runtime it drives and the link;
- ``<server>/plugins/endbot/config.toml`` — the plugin config, which now holds
  only the endbotd link settings (the runtime token no longer reaches BDS);
- the ``[local-bot-auth]`` table in ``<server>/endstone.toml`` — everything
  else in that file (other tables, keys, comments) is preserved via tomlkit.

``state/secrets/endbotd.token`` is created once, like the runtime's token.

Paths are written as TOML literal strings so Windows backslashes need no
escaping (a path containing a single quote cannot be a TOML literal string and
falls back to a basic string).
"""

from __future__ import annotations

import json
import os
import secrets
import sys
from dataclasses import dataclass, field
from pathlib import Path

from endbot_cli.compat import tomllib
from endbot_cli.config import EndbotConfig
from endbot_cli.fsutil import atomic_write_text
from endbot_cli.instance import InstancePaths
from endbot_cli.leveldat import LevelDatError, parse_level_dat
from endbot_cli.processes import _app_dir
from endbot_cli.properties import PreflightError, parse_properties

DEFAULT_SERVER_PORT = 19132
CONTROL_HOST = "127.0.0.1"
PLUGIN_TIMEOUT_SECONDS = 10.0
LOCAL_BOT_AUTH = {
    "enabled": True,
    "issuer": "endbot://local-bot",
    "audience": "endstone://local-bot",
    "clock-skew-seconds": 5,
    "max-token-lifetime-seconds": 120,
}


class ConfigGenerationError(RuntimeError):
    """A derived config file cannot be generated; the message names the fix."""


@dataclass(frozen=True, slots=True)
class GeneratedConfigs:
    runtime_config: Path
    plugin_config: Path
    endstone_config: Path
    endbotd_config: Path | None = None
    messages: list[str] = field(default_factory=list)


def _tomlkit():
    try:
        import tomlkit
    except ImportError as error:  # pragma: no cover - tomlkit is a hard dependency
        raise ConfigGenerationError("tomlkit is not installed; reinstall the endbot CLI package") from error
    return tomlkit


def _path_value(tomlkit, value: str):
    if "'" in value:  # TOML literal strings cannot contain single quotes
        return tomlkit.string(value)
    return tomlkit.string(value, literal=True)


def state_directory(paths: InstancePaths) -> Path:
    return paths.root / "state"


def read_server_port(server: Path) -> tuple[int, list[str]]:
    """Return ``server-port`` from ``server.properties`` (default 19132) plus warnings."""

    warnings: list[str] = []
    try:
        properties = parse_properties(server / "server.properties")
    except FileNotFoundError:
        return DEFAULT_SERVER_PORT, [
            f"WARN config-gen: {server / 'server.properties'} is missing; using server-port {DEFAULT_SERVER_PORT}"
        ]
    except (PreflightError, OSError) as error:
        return DEFAULT_SERVER_PORT, [f"WARN config-gen: {error}; using server-port {DEFAULT_SERVER_PORT}"]
    raw = properties.get("server-port")
    if raw is None:
        return DEFAULT_SERVER_PORT, []
    try:
        port = int(raw)
    except ValueError:
        port = -1
    if not 1 <= port <= 65535:
        warnings.append(
            f"WARN config-gen: server-port {raw!r} in {server / 'server.properties'} is not a valid port; "
            f"using {DEFAULT_SERVER_PORT}"
        )
        return DEFAULT_SERVER_PORT, warnings
    return port, warnings


BDS_DEFAULT_SERVER_NAME = "Dedicated Server"
BDS_DEFAULT_LEVEL_NAME = "Bedrock level"


def _read_advertised_level_name(server: Path, level_dir_name: str | None) -> str | None:
    """Return the world display name from ``level.dat``, or None to keep the fallback."""

    if not level_dir_name:
        return None
    level_dat = server / "worlds" / level_dir_name / "level.dat"
    try:
        raw = level_dat.read_bytes()
    except OSError:
        return None
    try:
        summary = parse_level_dat(raw)
    except LevelDatError:  # corrupt level.dat must not break config generation
        return None
    return summary.level_name


def read_server_identity(server: Path) -> tuple[str, str]:
    """Return the ``server-name`` and advertised world name this BDS uses over NetherNet.

    The runtime uses them to pick this server among every NetherNet host
    answering LAN discovery on the machine (docs/OPERATIONS.md section 5).
    Missing keys fall back to the BDS defaults.

    The advertised ``levelName`` is the world's display name, not necessarily
    the ``server.properties`` ``level-name`` directory name (issue #37): when
    ``worlds/<level-name>/level.dat`` exists and carries a non-empty
    ``LevelName`` tag, that value is used; otherwise the ``level-name`` value
    (or its default) is kept so pre-creation configs still resolve.
    """

    try:
        properties = parse_properties(server / "server.properties")
    except (FileNotFoundError, PreflightError, OSError):
        properties = {}
    server_name = properties.get("server-name") or BDS_DEFAULT_SERVER_NAME
    level_name = properties.get("level-name") or BDS_DEFAULT_LEVEL_NAME
    advertised = _read_advertised_level_name(server, level_name)
    return (server_name, advertised or level_name)


def uses_lan_discovery(config: EndbotConfig) -> bool:
    """Whether Bots find BDS through NetherNet LAN discovery on UDP 7551."""

    return config.server.transport == "nethernet" and config.server.nethernet_signalling == "lan"


def generate_runtime_config(
    paths: InstancePaths,
    config: EndbotConfig,
    server_port: int,
    server_identity: tuple[str, str] | None = None,
) -> Path:
    """Write ``state/generated/endbot-runtime.json`` with absolute paths (section 2)."""

    document = {
        "dataDirectory": str(state_directory(paths).resolve()),
        "controlTokenPath": str(paths.control_token.resolve()),
        "ownerPrivateKeyPath": str(paths.owner_private_key.resolve()),
        "ownerPublicKeyPath": str(paths.owner_public_key.resolve()),
        "controlHost": CONTROL_HOST,
        "controlPort": config.runtime.control_port,
        "chunkRadius": config.runtime.chunk_radius,
        "serverHost": CONTROL_HOST,
        "serverPort": server_port,
        "transport": config.server.transport,
    }
    if config.server.transport == "nethernet":
        document["nethernetSignalling"] = config.server.nethernet_signalling
    if config.server.transport == "nethernet" and server_identity is not None:
        document["serverName"], document["levelName"] = server_identity
    target = paths.state_generated / "endbot-runtime.json"
    atomic_write_text(target, json.dumps(document, indent=2) + "\n")
    return target


def _read_legacy_allowlists(*configs: Path) -> tuple[list[str], list[str], str | None]:
    """Preserve rc.1 ``allowed-uuids`` / ``allowed-xuids`` pre-bound entries.

    The first of ``configs`` with an ``[authorization]`` table carries them:
    the plugin config before the first endbotd start, endbotd.toml after it.
    """

    for config in configs:
        try:
            document = tomllib.loads(config.read_text(encoding="utf-8"))
        except FileNotFoundError:
            continue
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
            return (
                [],
                [],
                (
                    f"WARN config-gen: {config} cannot be parsed ({error}); "
                    "legacy [authorization] pre-bound entries are not preserved"
                ),
            )
        authorization = document.get("authorization")
        if isinstance(authorization, dict):
            break
    else:
        return [], [], None

    def strings(key: str) -> list[str]:
        value = authorization.get(key, [])
        if not isinstance(value, list):
            return []
        return [entry for entry in value if isinstance(entry, str) and entry]

    return strings("allowed-uuids"), strings("allowed-xuids"), None


def ensure_endbotd_token(paths: InstancePaths) -> Path:
    """Create ``state/secrets/endbotd.token`` once; a short one is replaced. The token is never printed."""

    token_file = paths.endbotd_token
    try:
        if len(token_file.read_text(encoding="utf-8").strip()) >= 32:
            return token_file
    except FileNotFoundError:
        pass
    except (OSError, UnicodeDecodeError) as error:
        raise ConfigGenerationError(f"{token_file} cannot be read: {error}; fix the file permissions") from error
    token_file.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(token_file, secrets.token_urlsafe(32) + "\n")
    if os.name != "nt":
        os.chmod(token_file, 0o600)
    return token_file


def _bundled_runner(paths: InstancePaths, config: EndbotConfig) -> str:
    runner = config.scripting.runner
    if runner:
        return runner
    # The installed CLI belongs to the running version, even if app/current changes.
    app_dir = next(
        (
            parent.parent for parent in Path(__file__).resolve().parents
            if parent.name == "python" and parent.parent.parent.name == "app"
        ),
        None,
    )
    if app_dir is None:  # source checkout / development invocation
        app_dir = _app_dir(paths)
    if app_dir is not None:
        candidate = app_dir / "runner" / (
            "endbot-luau-runner.exe" if sys.platform == "win32" else "endbot-luau-runner"
        )
        if candidate.is_file():
            return str(candidate.resolve())
    return ""


def generate_endbotd_config(server: Path, paths: InstancePaths, config: EndbotConfig) -> tuple[Path, list[str]]:
    """Write ``state/generated/endbotd.toml`` (docs/ENDBOTD.md P7); return it plus warnings."""

    tomlkit = _tomlkit()
    target = paths.state_generated / "endbotd.toml"
    plugin_data = server / "plugins" / "endbot"
    legacy_uuids, legacy_xuids, warning = _read_legacy_allowlists(plugin_data / "config.toml", target)

    document = tomlkit.document()
    link = tomlkit.table()
    link["host"] = CONTROL_HOST
    link["port"] = config.endbotd.link_port
    link["token-file"] = _path_value(tomlkit, str(paths.endbotd_token.resolve()))
    document.add("link", link)

    runtime = tomlkit.table()
    runtime["host"] = CONTROL_HOST
    runtime["port"] = config.runtime.control_port
    runtime["token-file"] = _path_value(tomlkit, str(paths.control_token.resolve()))
    runtime["timeout-seconds"] = PLUGIN_TIMEOUT_SECONDS
    document.add("runtime", runtime)

    plugin = tomlkit.table()
    plugin["hotbar-sync-interval-ticks"] = config.runtime.hotbar_sync_interval_ticks
    plugin["hotbar-sync-resend-ticks"] = config.runtime.hotbar_sync_resend_ticks
    document.add("plugin", plugin)

    # endbotd reads only the state/ layout (docs/ENDBOTD.md P7); `endbot start` migrates before it starts.
    stores = tomlkit.table()
    stores["directory"] = _path_value(tomlkit, str((paths.root / "state").resolve()))
    stores["scripts-folder"] = _path_value(tomlkit, str((paths.root / "scripts").resolve()))
    document.add("stores", stores)

    run = tomlkit.table()
    run["journal-file"] = _path_value(tomlkit, str((paths.state_run / "endbotd-runs.json").resolve()))
    document.add("run", run)

    authorization = tomlkit.table()
    authorization["controller-gamertags"] = list(config.controllers.gamertags)
    authorization["controllers-file"] = _path_value(tomlkit, str(paths.state_controllers.resolve()))
    authorization["controller-policy-file"] = _path_value(
        tomlkit, str((paths.state_controllers.parent / "controller-policy.json").resolve())
    )
    authorization["allowed-uuids"] = legacy_uuids
    authorization["allowed-xuids"] = legacy_xuids
    document.add("authorization", authorization)

    limits = tomlkit.table()
    limits["max-bots"] = config.limits.max_bots
    limits["reserved-player-slots"] = config.limits.reserved_player_slots
    document.add("limits", limits)

    ui = tomlkit.table()
    ui["language"] = config.ui.language
    document.add("ui", ui)

    scripting = tomlkit.table()
    scripting["enabled"] = config.scripting.enabled
    # Omit defaults as before; forward explicit resource-limit overrides.
    for key, value, default in (
        ("os-confinement", config.scripting.os_confinement, True),
        ("process-memory-bytes", config.scripting.process_memory_bytes, 268435456),
        ("cpu-seconds", config.scripting.cpu_seconds, 60),
        ("book-export", config.scripting.book_export, True),
        ("chat-logs", config.scripting.chat_logs, True),
        ("ui-pack-editor", config.scripting.ui_pack_editor, False),
        ("replies", config.scripting.replies, "failures"),
    ):
        if value != default:
            scripting[key] = value
    runner = _bundled_runner(paths, config)
    if runner:
        scripting["runner"] = _path_value(tomlkit, runner)
    document.add("scripting", scripting)

    atomic_write_text(target, tomlkit.dumps(document))
    return target, [warning] if warning else []


def generate_plugin_config(server: Path, paths: InstancePaths, config: EndbotConfig) -> Path:
    """Write ``<server>/plugins/endbot/config.toml``: only how the plugin reaches endbotd."""

    tomlkit = _tomlkit()
    plugin_config = server / "plugins" / "endbot" / "config.toml"
    document = tomlkit.document()
    endbotd = tomlkit.table()
    endbotd["host"] = CONTROL_HOST
    endbotd["port"] = config.endbotd.link_port
    endbotd["token-file"] = _path_value(tomlkit, str(paths.endbotd_token.resolve()))
    document.add("endbotd", endbotd)
    atomic_write_text(plugin_config, tomlkit.dumps(document))
    return plugin_config


def generate_endstone_config(server: Path, paths: InstancePaths) -> Path:
    """Set ``[local-bot-auth]`` in ``<server>/endstone.toml``, preserving everything else."""

    tomlkit = _tomlkit()
    endstone_toml = server / "endstone.toml"
    try:
        text = endstone_toml.read_text(encoding="utf-8")
    except FileNotFoundError:
        text = ""
    except (OSError, UnicodeDecodeError) as error:
        raise ConfigGenerationError(f"{endstone_toml} cannot be read: {error}; fix the file permissions") from error
    try:
        document = tomlkit.parse(text)
    except Exception as error:  # tomlkit raises several parse exception types
        raise ConfigGenerationError(
            f"{endstone_toml} cannot be parsed: {error}; fix or remove it (only [local-bot-auth] is generated)"
        ) from error

    table = document.get("local-bot-auth")
    if table is None:
        table = tomlkit.table()
        document.add("local-bot-auth", table)
    for key, value in LOCAL_BOT_AUTH.items():
        table[key] = value
    table["public-key-file"] = _path_value(tomlkit, str(paths.owner_public_key.resolve()))

    atomic_write_text(endstone_toml, tomlkit.dumps(document))
    return endstone_toml


def generate_all(config: EndbotConfig, paths: InstancePaths, server: Path) -> GeneratedConfigs:
    """Regenerate every derived config file (section 2) and report what happened."""

    server_port, messages = read_server_port(server)
    # RakNet and direct NetherNet signalling dial server-port, so a default must not stand in for it.
    if not uses_lan_discovery(config) and messages:
        raise ConfigGenerationError(
            "RakNet and direct NetherNet signalling require a readable server.properties with a valid server-port: "
            + "; ".join(messages)
        )
    identity = read_server_identity(server) if uses_lan_discovery(config) else None
    runtime_config = generate_runtime_config(paths, config, server_port, identity)
    ensure_endbotd_token(paths)
    # endbotd.toml first: it carries legacy entries out of the old plugin config before that is rewritten.
    endbotd_config, endbotd_messages = generate_endbotd_config(server, paths, config)
    plugin_config = generate_plugin_config(server, paths, config)
    messages = [*messages, *endbotd_messages]
    endstone_config = generate_endstone_config(server, paths)
    messages.append(
        f"PASS config-gen: generated {runtime_config}, {endbotd_config}, {plugin_config}, "
        f"[local-bot-auth] in {endstone_config}"
    )
    return GeneratedConfigs(
        runtime_config=runtime_config,
        plugin_config=plugin_config,
        endstone_config=endstone_config,
        endbotd_config=endbotd_config,
        messages=messages,
    )
