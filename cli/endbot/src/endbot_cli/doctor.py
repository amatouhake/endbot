"""Read-only health checks for one Endbot instance (docs/OPERATIONS.md section 7).

Every check prints one ``PASS`` / ``WARN`` / ``FAIL`` / ``SKIP`` line and
nothing is ever written. Exit code is 0 when no check FAILs and 1 otherwise;
WARN marks items the operator must act on (for example a pending controller)
but that do not violate a safety invariant.
"""

from __future__ import annotations

import importlib.metadata
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from endbot_cli.allowlist import AllowlistError, entry_matches, read_allowlist
from endbot_cli.compat import tomllib
from endbot_cli.config import ConfigError, EndbotConfig, load_config
from endbot_cli.control_client import RuntimeControlClient, RuntimeControlError
from endbot_cli.controllers import ControllersError, ControllerState, load_controllers
from endbot_cli.instance import InstancePaths
from endbot_cli.leveldat import LevelDatError, parse_level_dat
from endbot_cli.lock import LockData, LockError, bds_versions_match, load_lock
from endbot_cli.properties import PreflightError, parse_properties, verify_server_properties

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


def check_server_properties(server: Path) -> tuple[CheckResult, dict[str, str] | None]:
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
            CheckResult(FAIL, name, f"{error}; fix {properties_path} (required: online-mode=true, allow-cheats=false)"),
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
    return CheckResult(PASS, name, f"online-mode=true, allow-cheats=false in {properties_path}"), properties


def check_world(server: Path, properties: dict[str, str] | None) -> CheckResult:
    name = "world"
    if properties is None:
        return CheckResult(SKIP, name, "server.properties is unreadable")
    level_name = properties.get("level-name")
    if not level_name:
        return CheckResult(SKIP, name, "key 'level-name' is missing from server.properties; world cannot be located")
    level_dat = server / "worlds" / level_name / "level.dat"
    if not level_dat.is_file():
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


def check_plugin_port(server: Path, config: EndbotConfig) -> CheckResult:
    name = "plugin-port"
    plugin_config = server / "plugins" / "endbot" / "config.toml"
    error, document = _load_plugin_config(server)
    if error is not None:
        return CheckResult(FAIL, name, error)
    if document is None:
        return CheckResult(SKIP, name, "plugins/endbot/config.toml is not generated yet (generated on every start)")
    runtime = document.get("runtime")
    port = runtime.get("port") if isinstance(runtime, dict) else None
    if isinstance(port, bool) or not isinstance(port, int):
        return CheckResult(
            FAIL,
            name,
            f"key 'runtime.port' in {plugin_config} must be an integer; delete the file to regenerate on start",
        )
    if port != config.runtime.control_port:
        return CheckResult(
            FAIL,
            name,
            f"plugin port {port} does not match 'runtime.control-port' {config.runtime.control_port} in endbot.toml; "
            "delete plugins/endbot/config.toml to regenerate it on start",
        )
    return CheckResult(PASS, name, f"plugin and runtime agree on control port {port}")


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


def check_controllers(config: EndbotConfig, state: ControllerState) -> list[CheckResult]:
    name = "controllers"
    if not config.controllers.gamertags:
        return [CheckResult(WARN, name, "no controllers configured; nobody can control Bots")]
    results: list[CheckResult] = []
    for gamertag in config.controllers.gamertags:
        binding = state.find(gamertag)
        if binding is None:
            results.append(
                CheckResult(WARN, name, f"{gamertag} is pending; join once with this GamerTag to bind it (section 3)")
            )
        else:
            results.append(CheckResult(PASS, name, f"{gamertag} is bound (since {binding.bound_at})"))
    configured = {tag.casefold() for tag in config.controllers.gamertags}
    for binding in state.bindings:
        if binding.gamertag.casefold() not in configured:
            results.append(
                CheckResult(
                    WARN,
                    name,
                    f"{binding.gamertag} has a binding but is not in [controllers].gamertags; "
                    "it is revoked on the next start (section 3)",
                )
            )
    return results


def check_controllers_legacy(server: Path) -> CheckResult:
    name = "controllers-legacy"
    error, document = _load_plugin_config(server)
    if error is not None:
        return CheckResult(SKIP, name, "plugins/endbot/config.toml cannot be parsed (see plugin-port)")
    if document is None:
        return CheckResult(SKIP, name, "plugins/endbot/config.toml is not generated yet (generated on every start)")
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
    else:
        properties_result, properties = check_server_properties(server)
        results.append(properties_result)
        results.append(check_world(server, properties))

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
            results.append(check_plugin_port(server, config))
        results.append(check_controllers_legacy(server))

    results.append(check_endstone_version(context))
    results.append(check_control_token(paths))

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

    if server is None:
        results.append(CheckResult(SKIP, "allowlist", "server directory is unavailable"))
    elif config is None:
        results.append(CheckResult(SKIP, "allowlist", "endbot.toml is invalid"))
    elif properties is None:
        results.append(CheckResult(SKIP, "allowlist", "server.properties is unreadable"))
    else:
        results.append(check_allowlist(server, properties, config, state or ControllerState()))

    results.append(check_runtime(context, config, paths))
    return results


def exit_code(results: list[CheckResult]) -> int:
    return 1 if any(result.status == FAIL for result in results) else 0
