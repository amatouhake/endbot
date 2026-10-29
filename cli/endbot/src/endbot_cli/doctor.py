"""Read-only health checks for one Endbot instance (docs/OPERATIONS.md section 7).

Every check prints one ``PASS`` / ``WARN`` / ``FAIL`` / ``SKIP`` line and
configuration, policy and world data are never changed. Live queries use transient IPC files.
Exit code is 0 when no check FAILs and 1 otherwise;
WARN marks items the operator must act on (for example a pending controller)
but that do not violate a safety invariant.
"""

from __future__ import annotations

import importlib.metadata
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from endbot_cli.allowlist import AllowlistError, entry_matches, read_allowlist
from endbot_cli.compat import tomllib
from endbot_cli.config import ConfigError, EndbotConfig, load_config
from endbot_cli.configgen import BDS_DEFAULT_LEVEL_NAME
from endbot_cli.control_client import RuntimeControlClient, RuntimeControlError
from endbot_cli.controllers import ControllersError, ControllerState, load_controllers
from endbot_cli.instance import InstancePaths
from endbot_cli.leveldat import LevelDatError, parse_level_dat
from endbot_cli.lock import LockData, LockError, bds_versions_match, load_lock
from endbot_cli.properties import PreflightError, parse_properties, verify_runtime_properties, verify_server_properties

PASS = "PASS"
WARN = "WARN"
FAIL = "FAIL"
SKIP = "SKIP"


@dataclass(frozen=True, slots=True)
class CheckResult:
    status: str
    name: str
    message: str

    def line(self) -> str:
        return f"{self.status} {self.name}: {self.message}"


@dataclass(frozen=True, slots=True)
class DoctorContext:
    paths: InstancePaths
    live: bool = False
    timeout: float = 5.0
    installed_endstone_version: Callable[[], str | None] | None = None
    lock: LockData = field(default_factory=load_lock)

    def endstone_version(self) -> str | None:
        if self.installed_endstone_version is not None:
            return self.installed_endstone_version()
        return detect_endstone_version()


def detect_endstone_version() -> str | None:
    """Installed ``endstone`` distribution version, or None when not installed."""

    try:
        return importlib.metadata.version("endstone")
    except importlib.metadata.PackageNotFoundError:
        return None


def check_config(paths: InstancePaths) -> tuple[CheckResult, EndbotConfig | None]:
    try:
        config = load_config(paths.endbot_toml)
    except ConfigError as error:
        return CheckResult(FAIL, "config", str(error)), None
    return CheckResult(PASS, "config", f"{paths.endbot_toml} loads"), config


def check_server_dir(config: EndbotConfig, paths: InstancePaths) -> tuple[CheckResult, Path | None]:
    server = config.server.resolve(paths.root)
    if not server.is_dir():
        return (
            CheckResult(
                FAIL,
                "server-dir",
                f"{server} does not exist; fix key 'server.path' in {paths.endbot_toml} or run `endbot setup`",
            ),
            None,
        )
    return CheckResult(PASS, "server-dir", f"{server} exists"), server


def check_server_properties(server: Path, transport: str = "nethernet") -> tuple[CheckResult, dict[str, str] | None]:
    name = "server-properties"
    properties_path = server / "server.properties"
    try:
        properties = parse_properties(properties_path)
    except FileNotFoundError:
        return (
            CheckResult(
                FAIL,
                name,
                f"{properties_path} is missing; restore it or run `endbot setup` (it writes the safety defaults)",
            ),
            None,
        )
    except (PreflightError, OSError) as error:
        return (
            CheckResult(
                FAIL,
                name,
                f"{error}; fix {properties_path} "
                f"(required: online-mode=true, allow-cheats=false, transport={transport})",
            ),
            None,
        )
    try:
        verify_server_properties(properties_path)
    except PreflightError as error:
        return (
            CheckResult(
                FAIL,
                name,
                f"{properties_path}: {error}; set online-mode=true and allow-cheats=false (safety invariants)",
            ),
            properties,
        )
    try:
        verify_runtime_properties(properties_path, transport)
    except PreflightError as error:
        return (
            CheckResult(
                FAIL,
                name,
                f"{properties_path}: {error}",
            ),
            properties,
        )
    return (
        CheckResult(PASS, name, f"online-mode=true, allow-cheats=false, transport={transport} in {properties_path}"),
        properties,
    )


def check_world(server: Path, properties: dict[str, str] | None) -> CheckResult:
    name = "world"
    if properties is None:
        return CheckResult(SKIP, name, "server.properties is unreadable")
    # A missing level-name is valid BDS: the server uses the default world,
    # and configgen resolves the same default when locating level.dat.
    level_name = properties.get("level-name") or BDS_DEFAULT_LEVEL_NAME
    level_dat = server / "worlds" / level_name / "level.dat"
    if not level_dat.is_file():
        if level_dat.parent.is_dir():
            return CheckResult(
                FAIL,
                name,
                f"{level_dat} is missing but {level_dat.parent} exists; "
                "config generation falls back to the directory name and discovery may miss the server; "
                "restore level.dat from a backup or remove the incomplete world directory",
            )
        return CheckResult(SKIP, name, f"{level_dat} does not exist yet; world history is checked after world creation")
    try:
        summary = parse_level_dat(level_dat.read_bytes())
    except (LevelDatError, OSError) as error:
        return CheckResult(FAIL, name, f"{error}; restore the world from a backup before certifying it")
    unsafe = summary.unsafe_flags()
    if unsafe:
        return CheckResult(
            FAIL,
            name,
            f"{level_dat} has achievement-disabling history ({', '.join(unsafe)}); Endbot does not change world flags",
        )
    details = f"{level_dat}: no creative/experiment history"
    if summary.game_type is not None:
        details += f", GameType={summary.game_type}"
    if summary.cheats_enabled:
        return CheckResult(WARN, name, f"{details}, but cheatsEnabled is set")
    if summary.game_type not in (None, 0):
        return CheckResult(WARN, name, f"{details}, but GameType is not survival (0)")
    if summary.other_experiment_keys:
        return CheckResult(
            WARN,
            name,
            f"{details}, but unknown experiment keys are recorded: {', '.join(summary.other_experiment_keys)}",
        )
    return CheckResult(PASS, name, details)


def check_capacity(server: Path, properties: dict[str, str] | None) -> CheckResult:
    """Report the Bot slot budget: max-players minus the slots kept for players."""

    name = "capacity"
    if properties is None:
        return CheckResult(SKIP, name, "server.properties is unreadable")
    raw = properties.get("max-players", "10")
    try:
        max_players = int(raw)
    except (TypeError, ValueError):
        return CheckResult(WARN, name, f"max-players={raw!r} is not an integer; Bot capacity is unchecked")
    error, document = _load_plugin_config(server)
    if error is not None:
        return CheckResult(WARN, name, error)
    limits = (document or {}).get("limits", {})
    if not isinstance(limits, dict):
        return CheckResult(WARN, name, "[limits] must be a table; Bot capacity is unchecked")
    max_bots = limits.get("max-bots", 0)
    reserved = limits.get("reserved-player-slots", 1)
    if any(isinstance(value, bool) or not isinstance(value, int) for value in (max_bots, reserved)):
        return CheckResult(WARN, name, "[limits] values must be integers (max-bots, reserved-player-slots)")
    if max_bots < 0 or reserved < 0:
        return CheckResult(WARN, name, "[limits] values must be 0 or more (max-bots, reserved-player-slots)")
    config_path = server / "plugins" / "endbot" / "config.toml"
    if max_bots == 0 and reserved == 0:
        return CheckResult(
            WARN,
            name,
            f"no Bot limit and no reserved slots in {config_path}; a controller can fill all {max_players} slots",
        )
    room = max_players - reserved
    if room <= 0:
        return CheckResult(
            WARN,
            name,
            f"max-players={max_players} with reserved-player-slots={reserved} leaves no slot for a Bot",
        )
    if max_bots > room:
        return CheckResult(
            WARN,
            name,
            f"max-bots={max_bots} cannot apply: {max_players} slots minus {reserved} reserved = {room}",
        )
    budget = f"at most {max_bots} Bots" if max_bots > 0 else f"Bots up to {room}"
    return CheckResult(PASS, name, f"{max_players} slots, {reserved} reserved for players, {budget}")


def check_bds_version(server: Path, lock: LockData) -> CheckResult:
    name = "bds-version"
    version_file = server / "version.txt"
    try:
        found = version_file.read_text(encoding="utf-8-sig").strip()
    except FileNotFoundError:
        return CheckResult(
            FAIL,
            name,
            f"{version_file} is missing; Endstone treats a server without version.txt as older than supported and "
            "would re-download BDS and the vanilla packs over the existing ones (docs/OPERATIONS.md section 6); "
            "restore version.txt or run `endbot setup` before ever starting the server",
        )
    except OSError as error:
        return CheckResult(FAIL, name, f"{version_file} cannot be read: {error}; fix the file permissions")
    if not found:
        return CheckResult(FAIL, name, f"{version_file} is empty; write the installed BDS version into it")
    try:
        matches = bds_versions_match(found, lock.bds_version)
    except LockError as error:
        return CheckResult(FAIL, name, f"{version_file} {error}; restore it to the installed BDS version")
    if not matches:
        return CheckResult(
            FAIL,
            name,
            f"{version_file} says {found!r} but endstone.lock locks {lock.bds_version!r}; "
            "run `endbot update` to move to the locked pair (never let Endstone re-download BDS during start)",
        )
    return CheckResult(PASS, name, f"{version_file} says {found!r}, matching locked BDS {lock.bds_version!r}")


def check_endstone_version(context: DoctorContext) -> CheckResult:
    name = "endstone-version"
    installed = context.endstone_version()
    if installed is None:
        return CheckResult(
            FAIL,
            name,
            f"the endstone distribution is not installed; install locked {context.lock.endstone_package_version!r}",
        )
    if installed != context.lock.endstone_package_version:
        return CheckResult(
            FAIL,
            name,
            f"installed endstone {installed!r} does not match locked {context.lock.endstone_package_version!r}; "
            "install the locked package",
        )
    return CheckResult(PASS, name, f"installed endstone {installed!r} matches the lock")


def check_local_bot_auth(server: Path, paths: InstancePaths) -> CheckResult:
    name = "local-bot-auth"
    try:
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.serialization import load_pem_private_key, load_pem_public_key
    except ImportError as error:  # pragma: no cover - cryptography is a hard dependency
        return CheckResult(FAIL, name, f"cryptography is not installed ({error}); reinstall the endbot CLI package")

    endstone_toml = server / "endstone.toml"
    try:
        document = tomllib.loads(endstone_toml.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return CheckResult(
            FAIL,
            name,
            f"{endstone_toml} is missing; create it with [local-bot-auth] (generated on every start, section 2) "
            "or run `endbot setup`",
        )
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        return CheckResult(FAIL, name, f"{endstone_toml} cannot be parsed: {error}; fix or regenerate it")

    section = document.get("local-bot-auth")
    if not isinstance(section, dict):
        return CheckResult(
            FAIL, name, f"key '[local-bot-auth]' is missing from {endstone_toml}; add it (see OPERATIONS.md section 2)"
        )
    if section.get("enabled") is not True:
        return CheckResult(
            FAIL,
            name,
            f"key 'local-bot-auth.enabled' in {endstone_toml} must be true (found {section.get('enabled')!r}); "
            "set enabled = true to allow local Bots",
        )
    key_file = section.get("public-key-file")
    if not isinstance(key_file, str) or not key_file:
        return CheckResult(
            FAIL,
            name,
            f"key 'local-bot-auth.public-key-file' in {endstone_toml} must be a non-empty string path "
            "to owner-public.pem",
        )
    public_path = Path(key_file)
    if not public_path.is_absolute():
        public_path = endstone_toml.parent / public_path
    try:
        public_key = load_pem_public_key(public_path.read_bytes())
    except FileNotFoundError:
        return CheckResult(FAIL, name, f"{public_path} does not exist; point 'public-key-file' at owner-public.pem")
    except (OSError, ValueError) as error:
        return CheckResult(
            FAIL, name, f"{public_path} cannot be loaded as a PEM public key: {error}; regenerate the key pair"
        )
    if not isinstance(public_key, ec.EllipticCurvePublicKey) or public_key.curve.name != "secp384r1":
        return CheckResult(
            FAIL, name, f"{public_path} is not an EC P-384 public key; generate the owner key pair again"
        )

    try:
        private_bytes = paths.owner_private_key.read_bytes()
    except FileNotFoundError:
        return CheckResult(
            WARN,
            name,
            f"[local-bot-auth] is enabled with a P-384 key ({public_path}), but {paths.owner_private_key} is missing; "
            "key match is not verified",
        )
    except OSError as error:
        return CheckResult(FAIL, name, f"{paths.owner_private_key} cannot be read: {error}; fix the file permissions")
    try:
        private_key = load_pem_private_key(private_bytes, password=None)
    except (ValueError, TypeError) as error:
        return CheckResult(
            FAIL, name, f"{paths.owner_private_key} cannot be loaded as a PEM private key: {error}; restore it"
        )
    if not isinstance(private_key, ec.EllipticCurvePrivateKey):
        return CheckResult(
            FAIL, name, f"{paths.owner_private_key} is not an EC private key; restore the owner key pair"
        )
    if private_key.public_key().public_numbers() != public_key.public_numbers():
        return CheckResult(
            FAIL,
            name,
            f"{public_path} does not match {paths.owner_private_key}; restore the pair or regenerate both keys",
        )
    return CheckResult(PASS, name, f"enabled, {public_path} is a P-384 key matching {paths.owner_private_key}")


def check_control_token(paths: InstancePaths) -> CheckResult:
    name = "control-token"
    try:
        token = paths.control_token.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return CheckResult(
            FAIL,
            name,
            f"{paths.control_token} is missing; the runtime creates it on first start (docs/OPERATIONS.md section 1)",
        )
    except (OSError, UnicodeDecodeError) as error:
        return CheckResult(FAIL, name, f"{paths.control_token} cannot be read: {error}; fix the file permissions")
    if len(token) < 32:
        return CheckResult(
            FAIL, name, f"{paths.control_token} holds only {len(token)} characters; at least 32 are required"
        )
    return CheckResult(PASS, name, f"{paths.control_token} is present and readable ({len(token)} characters)")


def check_grant_revisions(paths: InstancePaths) -> CheckResult:
    """``state/grant-revisions.json`` (docs/ENDBOTD.md P4); the plugin refuses to load when it is corrupt."""

    from uuid import UUID

    name = "grant-revisions"
    path = paths.state_controllers.parent / "grant-revisions.json"
    remedy = "the plugin will not load; stop Endbot and delete it, which safely resets every revision to 0"
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return CheckResult(PASS, name, "no revocations recorded; every grant revision is 0")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        return CheckResult(FAIL, name, f"{path} cannot be read ({error}); {remedy}")
    entries = document.get("revisions", {}) if isinstance(document, dict) else None
    try:
        valid = (
            document.get("version") == 1
            and not set(document) - {"version", "revisions"}
            and isinstance(entries, dict)
            and len({str(UUID(key)) for key in entries}) == len(entries)
            and all(type(value) is int and value >= 1 for value in entries.values())
        )
    except (AttributeError, ValueError):
        valid = False
    if not valid:
        return CheckResult(FAIL, name, f"{path} is not a version 1 grant revisions file; {remedy}")
    count = f"{len(entries)} revoked identit{'y' if len(entries) == 1 else 'ies'}"
    return CheckResult(PASS, name, f"{path} records {count}")


def check_stores(paths: InstancePaths, server: Path | None) -> CheckResult:
    """Which store layout is authoritative (docs/ENDBOTD.md P7): the marker and both layouts."""

    from endbot_cli import stores

    name = "stores"
    layout = stores.StoreLayout.for_instance(paths, server if server is not None else paths.root / "server")
    try:
        marker = stores.read_marker(layout)
    except stores.StoreError as error:
        return CheckResult(FAIL, name, str(error))
    state = f"state/ ({stores.describe(layout.state, layout.scripts)})"
    if server is None:
        plugin = "the plugin data folder (server directory unavailable)"
    else:
        plugin = f"the plugin data folder ({stores.describe(layout.plugin_data, layout.plugin_scripts)})"
    if marker == stores.STATE_STORES:
        left = stores.script_files(layout.plugin_scripts)
        if left:
            return CheckResult(
                WARN, name, f"state/ is authoritative, but {len(left)} script files in {layout.plugin_scripts} "
                f"are ignored; move them to {layout.scripts}",
            )
        return CheckResult(PASS, name, f"{state} is authoritative (stores 2); {plugin} is kept but not read")
    # WARN, not FAIL: an update from a version before endbotd runs this doctor through the old CLI,
    # which would refuse to switch on a FAIL it does not know (P7 amendment of 2026-10-29).
    return CheckResult(
        WARN, name, f"{plugin} is authoritative ({'stores 1' if marker else 'no ' + layout.marker.name}); "
        "the next `endbot start` or `endbot setup` moves the stores into state/",
    )


def _load_plugin_config(server: Path) -> tuple[str | None, dict | None]:
    """Return ``(parse_error, document)``; both are None when the file does not exist yet."""

    plugin_config = server / "plugins" / "endbot" / "config.toml"
    try:
        document = tomllib.loads(plugin_config.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, None
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        return f"{plugin_config} cannot be parsed: {error}; delete it to regenerate on start", None
    return None, document


def check_plugin_port(server: Path, config: EndbotConfig, paths: InstancePaths) -> CheckResult:
    """The plugin dials endbotd: same link port and the same token file as endbotd.toml."""

    name = "plugin-port"
    plugin_config = server / "plugins" / "endbot" / "config.toml"
    error, document = _load_plugin_config(server)
    if error is not None:
        return CheckResult(FAIL, name, error)
    if document is None:
        return CheckResult(SKIP, name, "plugins/endbot/config.toml is not generated yet (generated on every start)")
    link = document.get("endbotd")
    if link is None and "runtime" in document:
        # Written by a version before endbotd; start regenerates it. WARN so that version's update can switch.
        return CheckResult(WARN, name, f"{plugin_config} predates endbotd; `endbot start` regenerates it")
    port = link.get("port") if isinstance(link, dict) else None
    if isinstance(port, bool) or not isinstance(port, int):
        return CheckResult(
            FAIL,
            name,
            f"key 'endbotd.port' in {plugin_config} must be an integer; delete the file to regenerate on start",
        )
    if port != config.endbotd.link_port:
        return CheckResult(
            FAIL,
            name,
            f"plugin link port {port} does not match 'endbotd.link-port' {config.endbotd.link_port} in endbot.toml; "
            "delete plugins/endbot/config.toml to regenerate it on start",
        )
    token_file = link.get("token-file")
    if not isinstance(token_file, str) or Path(token_file).resolve() != paths.endbotd_token.resolve():
        return CheckResult(
            FAIL, name, f"the plugin does not read {paths.endbotd_token}; delete {plugin_config} to regenerate it"
        )
    if "runtime" in document:
        return CheckResult(
            FAIL, name, f"{plugin_config} still carries the runtime token settings; delete it to regenerate it"
        )
    return CheckResult(PASS, name, f"the plugin links to endbotd on port {port}")


def check_endbotd_token(paths: InstancePaths) -> CheckResult:
    name = "endbotd-token"
    try:
        token = paths.endbotd_token.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        # WARN: an update from a version before endbotd must not stop on a file only `endbot start` creates.
        return CheckResult(WARN, name, f"{paths.endbotd_token} is missing; `endbot start` creates it")
    except (OSError, UnicodeDecodeError) as error:
        return CheckResult(FAIL, name, f"{paths.endbotd_token} cannot be read: {error}; fix the file permissions")
    if len(token) < 32:
        return CheckResult(FAIL, name, f"{paths.endbotd_token} is too short; delete it and run `endbot start`")
    return CheckResult(PASS, name, f"{paths.endbotd_token} is present and readable ({len(token)} characters)")


def _load_endbotd_config(paths: InstancePaths) -> tuple[str | None, dict | None]:
    target = paths.state_generated / "endbotd.toml"
    try:
        return None, tomllib.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, None
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        return f"{target} cannot be parsed: {error}; delete it to regenerate on start", None


def check_endbotd_config(config: EndbotConfig, paths: InstancePaths) -> CheckResult:
    name = "endbotd-config"
    target = paths.state_generated / "endbotd.toml"
    error, document = _load_endbotd_config(paths)
    if error is not None:
        return CheckResult(FAIL, name, error)
    if document is None:
        return CheckResult(SKIP, name, f"{target} is not generated yet (generated on every start)")
    link, runtime = document.get("link"), document.get("runtime")
    if not isinstance(link, dict) or not isinstance(runtime, dict):
        return CheckResult(FAIL, name, f"{target} lacks [link] or [runtime]; delete it to regenerate on start")
    if link.get("port") != config.endbotd.link_port or runtime.get("port") != config.runtime.control_port:
        return CheckResult(
            FAIL, name, f"{target} does not match the ports in endbot.toml; delete it to regenerate on start"
        )
    return CheckResult(
        PASS, name, f"endbotd listens on port {link['port']} and drives the runtime on port {runtime['port']}"
    )


def check_endbotd_link(paths: InstancePaths) -> CheckResult:
    """Read the latest endbotd.log: a refused plugin link means mixed versions (docs/ENDBOTD.md P2)."""

    name = "endbotd-link"
    logs = paths.state_run / "logs"
    try:
        latest = max((entry for entry in logs.iterdir() if (entry / "endbotd.log").is_file()),
                     key=lambda entry: entry.name, default=None)
    except OSError:
        latest = None
    if latest is None:
        return CheckResult(SKIP, name, "no endbotd.log yet (written on every start)")
    try:
        lines = (latest / "endbotd.log").read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as error:
        return CheckResult(WARN, name, f"{latest / 'endbotd.log'} cannot be read: {error}")
    refused = [line for line in lines if "Endbot plugin link refused" in line]
    if any("another link protocol" in line or "different operation registries" in line or "speaks link protocol"
           in line for line in refused):
        return CheckResult(
            FAIL, name, f"endbotd refused the plugin: {refused[-1].split('refused: ', 1)[-1]}; the plugin and "
            "endbotd come from different Endbot versions (reinstall with `endbot update`)"
        )
    if refused:
        return CheckResult(WARN, name, f"endbotd refused a plugin link: {refused[-1].split('refused: ', 1)[-1]}")
    if any("linked" in line and "Endbot plugin" in line for line in lines):
        return CheckResult(PASS, name, f"the plugin linked to endbotd in the start of {latest.name}")
    return CheckResult(SKIP, name, f"the plugin had not linked yet in the start of {latest.name}")


def check_allowlist(
    server: Path, properties: dict[str, str], config: EndbotConfig, state: ControllerState
) -> CheckResult:
    """Section 7 allow-list check (read-only): humans must pass BDS's allow-list."""

    name = "allowlist"
    allowlist_path = server / "allowlist.json"
    if (properties.get("allow-list") or "").strip().lower() != "true":
        return CheckResult(PASS, name, "allow-list is off; controllers are not filtered")
    try:
        entries = read_allowlist(allowlist_path)
    except AllowlistError as error:
        return CheckResult(WARN, name, f"{error}; BDS treats allowlist.json as its own file, fix it by hand")
    if not config.controllers.gamertags:
        return CheckResult(PASS, name, "allow-list=true; no controllers are configured (nothing to allow-list)")
    missing: list[str] = []
    for tag in config.controllers.gamertags:
        binding = state.find(tag)
        xuid = binding.xuid if binding is not None else None
        if not any(entry_matches(entry, tag, xuid) for entry in entries):
            missing.append(tag)
    if not missing:
        return CheckResult(PASS, name, f"allow-list=true and every configured controller is in {allowlist_path}")
    return CheckResult(
        WARN,
        name,
        f"allow-list=true but {allowlist_path} is missing {', '.join(missing)}; while the server runs, fix each with "
        "`endbot console allowlist add <GamerTag>`, or add it to allowlist.json while the server is stopped",
    )


def check_operators(server: Path, properties: dict[str, str]) -> CheckResult:
    """Section 7 operator check (read-only): operators can cheat without disabling achievements.

    The locked Endstone lets permissions alone decide who runs a command, so an
    operator can use /give, /gamemode, or /tp with allow-cheats=false and the
    world keeps achievements enabled. That is upstream behavior, not an Endbot
    safety violation, so it is a WARN the operator can knowingly accept.
    """

    name = "operators"
    consequence = (
        "operators can run cheat commands such as /give or /gamemode while allow-cheats=false "
        "keeps achievements enabled (docs/SECURITY.md)"
    )
    if (properties.get("default-player-permission-level") or "").strip().lower() == "operator":
        return CheckResult(
            WARN,
            name,
            f"default-player-permission-level=operator makes every player an operator; {consequence}; "
            "set it to member unless that is intended",
        )
    permissions_path = server / "permissions.json"
    try:
        document = json.loads(permissions_path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return CheckResult(PASS, name, "no permissions.json; nobody is an operator")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        return CheckResult(WARN, name, f"{permissions_path} cannot be read ({error}); operators cannot be checked")
    if not isinstance(document, list):
        return CheckResult(WARN, name, f"{permissions_path} is not a JSON list; operators cannot be checked")
    operators = [
        entry
        for entry in document
        if isinstance(entry, dict) and str(entry.get("permission", "")).lower() == "operator"
    ]
    if not operators:
        return CheckResult(PASS, name, f"no operator entries in {permissions_path}")
    count = f"{len(operators)} operator entr{'y' if len(operators) == 1 else 'ies'}"
    return CheckResult(
        WARN,
        name,
        f"{count} in {permissions_path}; {consequence}; "
        "keep only operators you trust with that, or remove them while the server is stopped",
    )


def check_controllers(config: EndbotConfig, state: ControllerState) -> list[CheckResult]:
    name = "controllers"
    if not config.controllers.gamertags:
        return [
            CheckResult(
                WARN, name, "no bootstrap controllers configured; inspect runtime overrides with controllers list"
            )
        ]
    results: list[CheckResult] = []
    for gamertag in config.controllers.gamertags:
        binding = state.find(gamertag)
        if binding is None:
            results.append(
                CheckResult(WARN, name, f"{gamertag} is pending; join once with this GamerTag to bind it (section 3)")
            )
        else:
            results.append(
                CheckResult(
                    PASS,
                    name,
                    f"{gamertag} is bound (since {binding.bound_at}); bootstrap only, runtime policy may override",
                )
            )
    configured = {tag.casefold() for tag in config.controllers.gamertags}
    for binding in state.bindings:
        if binding.gamertag.casefold() not in configured:
            results.append(
                CheckResult(
                    WARN,
                    name,
                    f"{binding.gamertag} has a binding but is not in [controllers].gamertags; "
                    "bootstrap grant is revoked on next start; runtime grants are separate (section 3)",
                )
            )
    return results


def check_effective_controllers(context, config, state, server) -> list[CheckResult]:
    from endbot_cli.controller_requests import controller_request
    from endbot_cli.controller_status import load_policy, saved_report
    from endbot_cli.runstate import RUNNING, inspect_supervisor

    name = "controllers-effective"
    try:
        live = context.live and inspect_supervisor(context.paths.state_run).status == RUNNING
        if live:
            result = controller_request(context.paths.state_run, "list", timeout=context.timeout)
            if not result["ok"]:
                return [CheckResult(FAIL, name, "; ".join(result["messages"]))]
            rows = result["entries"]
        else:
            policy = load_policy(context.paths.state_controllers.parent / "controller-policy.json")
            legacy = {}
            if server is not None:
                error, document = _load_plugin_config(server)
                if error is not None:
                    raise ControllersError("cannot read legacy authorization from plugin config")
                if document is not None:
                    legacy = document.get("authorization", {})
            rows = saved_report(config.controllers.gamertags, state, policy, legacy)
        source = "live" if live else "saved configuration for next start"
        if not rows:
            return [CheckResult(WARN, name, f"{source}: no player controllers; server console is authorized")]
        return [
            CheckResult(
                PASS if row["state"] == "allowed" else WARN,
                name,
                f"{row['name']}: {row['state']} - {row['reason']} ({source})",
            )
            for row in rows
        ]
    except (OSError, ValueError, TypeError, KeyError) as error:
        return [CheckResult(FAIL, name, str(error))]


def check_controllers_legacy(server: Path, paths: InstancePaths) -> CheckResult:
    """Legacy entries live in endbotd.toml, or in the plugin config before the first endbotd start."""

    name = "controllers-legacy"
    error, document = _load_endbotd_config(paths)
    if error is not None:
        return CheckResult(SKIP, name, "state/generated/endbotd.toml cannot be parsed (see endbotd-config)")
    if document is None:
        error, document = _load_plugin_config(server)
        if error is not None:
            return CheckResult(SKIP, name, "plugins/endbot/config.toml cannot be parsed (see plugin-port)")
    if document is None:
        return CheckResult(SKIP, name, "endbotd.toml is not generated yet (generated on every start)")
    authorization = document.get("authorization")
    if not isinstance(authorization, dict):
        return CheckResult(PASS, name, "no legacy [authorization] pre-bound entries")
    legacy = sum(
        1
        for key in ("allowed-uuids", "allowed-xuids")
        for entry in (authorization.get(key) or [])
        if isinstance(entry, str) and entry
    )
    if legacy == 0:
        return CheckResult(PASS, name, "no legacy [authorization] pre-bound entries")
    return CheckResult(
        WARN,
        name,
        f"{legacy} legacy [authorization] pre-bound entries; migrate them to [controllers].gamertags (section 3)",
    )


def check_runtime(context: DoctorContext, config: EndbotConfig | None, paths: InstancePaths) -> CheckResult:
    name = "runtime"
    if not context.live:
        return CheckResult(SKIP, name, "runtime reachability is only checked with --live")
    if config is None:
        return CheckResult(SKIP, name, "endbot.toml is invalid; control port is unknown")
    client = RuntimeControlClient(
        "127.0.0.1", config.runtime.control_port, paths.control_token, timeout=context.timeout
    )
    try:
        client.request("ping")
    except RuntimeControlError as error:
        return CheckResult(
            FAIL, name, f"runtime on 127.0.0.1:{config.runtime.control_port} did not answer ping: {error}"
        )
    return CheckResult(PASS, name, f"runtime answered ping on 127.0.0.1:{config.runtime.control_port}")


def run_doctor(context: DoctorContext) -> list[CheckResult]:
    """Run every section 7 check in order and return one or more results per check."""

    paths = context.paths
    results: list[CheckResult] = []

    config_result, config = check_config(paths)
    results.append(config_result)

    server: Path | None = None
    if config is None:
        results.append(CheckResult(SKIP, "server-dir", "endbot.toml is invalid"))
    else:
        server_result, server = check_server_dir(config, paths)
        results.append(server_result)

    properties: dict[str, str] | None = None
    if server is None:
        results.append(CheckResult(SKIP, "server-properties", "server directory is unavailable"))
        results.append(CheckResult(SKIP, "world", "server directory is unavailable"))
        results.append(CheckResult(SKIP, "capacity", "server directory is unavailable"))
    else:
        properties_result, properties = check_server_properties(server, config.server.transport)
        results.append(properties_result)
        results.append(check_world(server, properties))
        results.append(check_capacity(server, properties))

    if server is None:
        results.append(CheckResult(SKIP, "bds-version", "server directory is unavailable"))
        results.append(CheckResult(SKIP, "local-bot-auth", "server directory is unavailable"))
        results.append(CheckResult(SKIP, "plugin-port", "server directory is unavailable"))
        results.append(CheckResult(SKIP, "controllers-legacy", "server directory is unavailable"))
    else:
        results.append(check_bds_version(server, context.lock))
        results.append(check_local_bot_auth(server, paths))
        if config is None:
            results.append(CheckResult(SKIP, "plugin-port", "endbot.toml is invalid"))
        else:
            results.append(check_plugin_port(server, config, paths))
        results.append(check_controllers_legacy(server, paths))

    results.append(check_endstone_version(context))
    results.append(check_control_token(paths))
    results.append(check_endbotd_token(paths))
    if config is None:
        results.append(CheckResult(SKIP, "endbotd-config", "endbot.toml is invalid"))
    else:
        results.append(check_endbotd_config(config, paths))
    results.append(check_endbotd_link(paths))

    state: ControllerState | None = None
    if config is None:
        results.append(CheckResult(SKIP, "controllers", "endbot.toml is invalid"))
    else:
        try:
            state = load_controllers(paths.state_controllers)
        except ControllersError as error:
            results.append(CheckResult(FAIL, "controllers", str(error)))
        else:
            results.extend(check_controllers(config, state))
            results.extend(check_effective_controllers(context, config, state, server))
    results.append(check_grant_revisions(paths))
    results.append(check_stores(paths, server))

    if server is None:
        results.append(CheckResult(SKIP, "allowlist", "server directory is unavailable"))
    elif config is None:
        results.append(CheckResult(SKIP, "allowlist", "endbot.toml is invalid"))
    elif properties is None:
        results.append(CheckResult(SKIP, "allowlist", "server.properties is unreadable"))
    else:
        results.append(check_allowlist(server, properties, config, state or ControllerState()))

    if server is None:
        results.append(CheckResult(SKIP, "operators", "server directory is unavailable"))
    elif properties is None:
        results.append(CheckResult(SKIP, "operators", "server.properties is unreadable"))
    else:
        results.append(check_operators(server, properties))

    results.append(check_runtime(context, config, paths))
    return results


def exit_code(results: list[CheckResult]) -> int:
    return 1 if any(result.status == FAIL for result in results) else 0
