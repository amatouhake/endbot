"""Typed loading and validation of ``endbot.toml`` (docs/OPERATIONS.md §2).

``endbot.toml`` is the only file operators edit. Every error names the file,
the offending key, and the fix so the operator can repair the file by hand.
Identifiers (GamerTags) must be strings and numbers must be quoted; TOML
quoting style itself cannot be re-checked after parsing, so only value types
are validated here.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from endbot_cli.compat import tomllib

DEFAULT_CONTROL_PORT = 19142
SECTIONS = ("server", "controllers", "runtime")


class ConfigError(ValueError):
    """``endbot.toml`` is invalid; the message names file, key, and fix."""


@dataclass(frozen=True, slots=True)
class ServerConfig:
    path: str

    def resolve(self, instance_root: Path) -> Path:
        """Return the BDS directory; relative paths resolve against the instance."""

        candidate = Path(self.path)
        return candidate if candidate.is_absolute() else instance_root / candidate


@dataclass(frozen=True, slots=True)
class ControllersConfig:
    gamertags: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    control_port: int = DEFAULT_CONTROL_PORT


@dataclass(frozen=True, slots=True)
class EndbotConfig:
    server: ServerConfig
    controllers: ControllersConfig
    runtime: RuntimeConfig


def _fail(path: Path, key: str, problem: str, fix: str) -> ConfigError:
    return ConfigError(f"{path}: key {key!r} {problem}; {fix}")


def _require_table(path: Path, document: dict[str, Any], name: str) -> dict[str, Any]:
    table = document.get(name)
    if table is None:
        raise _fail(path, name, "is missing", f"add a [{name}] section as shown in docs/OPERATIONS.md §2")
    if not isinstance(table, dict):
        raise _fail(path, name, "must be a table", f"write it as a [{name}] section")
    return table


def _reject_unknown_keys(path: Path, table: dict[str, Any], section: str, known: tuple[str, ...]) -> None:
    for key in table:
        if key not in known:
            raise _fail(
                path,
                f"{section}.{key}",
                "is not recognized",
                f"remove it or fix the spelling (known keys: {', '.join(known)})",
            )


def _load_server(path: Path, document: dict[str, Any]) -> ServerConfig:
    table = _require_table(path, document, "server")
    _reject_unknown_keys(path, table, "server", ("path",))
    if "path" not in table:
        raise _fail(path, "server.path", "is missing", "add [server] path = 'server' (the BDS directory)")
    value = table["path"]
    if not isinstance(value, str) or not value:
        raise _fail(path, "server.path", "must be a non-empty string", "quote the value, e.g. path = 'server'")
    return ServerConfig(path=value)


def _load_controllers(path: Path, document: dict[str, Any]) -> ControllersConfig:
    table = _require_table(path, document, "controllers")
    _reject_unknown_keys(path, table, "controllers", ("gamertags",))
    if "gamertags" not in table:
        raise _fail(
            path,
            "controllers.gamertags",
            "is missing",
            'add [controllers] gamertags = ["ExampleTag"] (an empty list is allowed)',
        )
    value = table["gamertags"]
    if not isinstance(value, list):
        raise _fail(path, "controllers.gamertags", "must be an array of strings", 'e.g. gamertags = ["ExampleTag"]')
    gamertags: list[str] = []
    for index, entry in enumerate(value):
        key = f"controllers.gamertags[{index}]"
        if isinstance(entry, (bool, int, float)):
            raise _fail(
                path,
                key,
                f"must be a string, found number {entry!r}",
                f'quote the GamerTag, e.g. "{entry}" (identifiers are always strings)',
            )
        if not isinstance(entry, str):
            raise _fail(path, key, f"must be a string, found {type(entry).__name__}", 'e.g. "ExampleTag"')
        if not entry:
            raise _fail(path, key, "is empty", "remove the entry or use the real GamerTag")
        gamertags.append(entry)
    return ControllersConfig(gamertags=tuple(gamertags))


def _load_runtime(path: Path, document: dict[str, Any]) -> RuntimeConfig:
    table = document.get("runtime", {})
    if not isinstance(table, dict):
        raise _fail(path, "runtime", "must be a table", "write it as a [runtime] section")
    _reject_unknown_keys(path, table, "runtime", ("control-port",))
    if "control-port" not in table:
        return RuntimeConfig()
    value = table["control-port"]
    if isinstance(value, bool) or not isinstance(value, int):
        raise _fail(
            path,
            "runtime.control-port",
            f"must be an integer, found {type(value).__name__} {value!r}",
            f"use an integer 1..65535, e.g. control-port = {DEFAULT_CONTROL_PORT}",
        )
    if not 1 <= value <= 65535:
        raise _fail(
            path,
            "runtime.control-port",
            f"must be between 1 and 65535, found {value}",
            f"use a valid TCP port, e.g. control-port = {DEFAULT_CONTROL_PORT}",
        )
    return RuntimeConfig(control_port=value)


def load_config(path: Path) -> EndbotConfig:
    """Load and validate ``endbot.toml``; raise :class:`ConfigError` on any violation."""

    try:
        raw = path.read_bytes()
    except FileNotFoundError as error:
        raise ConfigError(
            f"{path}: file is missing; run `endbot setup` to create it (see docs/OPERATIONS.md §2)"
        ) from error
    except OSError as error:
        raise ConfigError(f"{path}: file cannot be read: {error}; fix the file permissions") from error
    try:
        document = tomllib.loads(raw.decode("utf-8"))
    except UnicodeDecodeError as error:
        raise ConfigError(f"{path}: file is not valid UTF-8: {error}; re-save it as UTF-8") from error
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"{path}: invalid TOML: {error}; fix the syntax") from error
    for key in document:
        if key not in SECTIONS:
            raise _fail(
                path, key, "is not recognized", f"remove it or fix the spelling (known keys: {', '.join(SECTIONS)})"
            )
    return EndbotConfig(
        server=_load_server(path, document),
        controllers=_load_controllers(path, document),
        runtime=_load_runtime(path, document),
    )
