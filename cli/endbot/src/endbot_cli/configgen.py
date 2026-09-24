"""Regenerating the derived configuration files on every start (docs/OPERATIONS.md section 2).

Three files are derived from ``endbot.toml`` plus ``state/`` and rewritten
atomically on every ``endbot start``:

- ``state/generated/endbot-runtime.json`` — the runtime config;
- ``<server>/plugins/endbot/config.toml`` — the plugin config;
- the ``[local-bot-auth]`` table in ``<server>/endstone.toml`` — everything
  else in that file (other tables, keys, comments) is preserved via tomlkit.

Paths are written as TOML literal strings so Windows backslashes need no
escaping (a path containing a single quote cannot be a TOML literal string and
falls back to a basic string).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from endbot_cli.compat import tomllib
from endbot_cli.config import EndbotConfig
from endbot_cli.fsutil import atomic_write_text
from endbot_cli.instance import InstancePaths
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
            f"WARN config-gen: server-port {raw!r} in {server / 'server.properties'} is not a valid TCP port; "
            f"using {DEFAULT_SERVER_PORT}"
        )
        return DEFAULT_SERVER_PORT, warnings
    return port, warnings


def generate_runtime_config(paths: InstancePaths, config: EndbotConfig, server_port: int) -> Path:
    """Write ``state/generated/endbot-runtime.json`` with absolute paths (section 2)."""

    document = {
        "dataDirectory": str(state_directory(paths).resolve()),
        "controlTokenPath": str(paths.control_token.resolve()),
        "ownerPrivateKeyPath": str(paths.owner_private_key.resolve()),
        "ownerPublicKeyPath": str(paths.owner_public_key.resolve()),
        "controlHost": CONTROL_HOST,
        "controlPort": config.runtime.control_port,
        "serverHost": CONTROL_HOST,
        "serverPort": server_port,
    }
    target = paths.state_generated / "endbot-runtime.json"
    atomic_write_text(target, json.dumps(document, indent=2) + "\n")
    return target


def _read_legacy_allowlists(plugin_config: Path) -> tuple[list[str], list[str], str | None]:
    """Preserve rc.1 ``allowed-uuids`` / ``allowed-xuids`` pre-bound entries."""

    try:
        document = tomllib.loads(plugin_config.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return [], [], None
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        return [], [], (
            f"WARN config-gen: {plugin_config} cannot be parsed ({error}); "
            "legacy [authorization] pre-bound entries are not preserved"
        )
    authorization = document.get("authorization", {})
    if not isinstance(authorization, dict):
        return [], [], None

    def strings(key: str) -> list[str]:
        value = authorization.get(key, [])
        if not isinstance(value, list):
            return []
        return [entry for entry in value if isinstance(entry, str) and entry]

    return strings("allowed-uuids"), strings("allowed-xuids"), None


def generate_plugin_config(server: Path, paths: InstancePaths, config: EndbotConfig) -> tuple[Path, list[str]]:
    """Write ``<server>/plugins/endbot/config.toml`` (section 2); return it plus warnings."""

    tomlkit = _tomlkit()
    plugin_config = server / "plugins" / "endbot" / "config.toml"
    legacy_uuids, legacy_xuids, warning = _read_legacy_allowlists(plugin_config)

    document = tomlkit.document()
    authorization = tomlkit.table()
    authorization["controller-gamertags"] = list(config.controllers.gamertags)
    authorization["controllers-file"] = _path_value(tomlkit, str(paths.state_controllers.resolve()))
    authorization["allowed-uuids"] = legacy_uuids
    authorization["allowed-xuids"] = legacy_xuids
    document.add("authorization", authorization)

    runtime = tomlkit.table()
    runtime["token-file"] = _path_value(tomlkit, str(paths.control_token.resolve()))
    runtime["host"] = CONTROL_HOST
    runtime["port"] = config.runtime.control_port
    runtime["timeout-seconds"] = PLUGIN_TIMEOUT_SECONDS
    document.add("runtime", runtime)

    atomic_write_text(plugin_config, tomlkit.dumps(document))
    warnings = [warning] if warning else []
    return plugin_config, warnings


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
    runtime_config = generate_runtime_config(paths, config, server_port)
    plugin_config, plugin_messages = generate_plugin_config(server, paths, config)
    messages = [*messages, *plugin_messages]
    endstone_config = generate_endstone_config(server, paths)
    messages.append(
        f"PASS config-gen: generated {runtime_config}, {plugin_config}, [local-bot-auth] in {endstone_config}"
    )
    return GeneratedConfigs(
        runtime_config=runtime_config,
        plugin_config=plugin_config,
        endstone_config=endstone_config,
        messages=messages,
    )
