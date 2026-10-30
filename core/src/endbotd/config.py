"""``state/generated/endbotd.toml``, generated from ``endbot.toml`` on every start.

Sections::

    [link]           host, port, token-file          the plugin link (P2)
    [runtime]        host, port, token-file, timeout-seconds
    [plugin]         hotbar-sync-interval-ticks, hotbar-sync-resend-ticks
    [stores]         directory (groups.json, scripts.json: ``state/``), scripts-folder
                     (the operator's ``.luau`` files: ``<instance>/scripts/``, S1)
    [run]            journal-file                    the run journal (P6)
    [permissions]    bots-default, groups-default    Bot and Group ownership defaults (BOT-024 4a)
    [authorization]  [limits]  [ui]  [scripting]      as the plugin config had them

Relative paths resolve against the file's directory. Developers with a manual
plugin install write this file themselves (docs/ENDBOTD.md S3).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

DEFAULT_LINK_PORT = 19143
DEFAULT_RUNTIME_PORT = 19142


def _table(document: dict, name: str) -> dict:
    value = document.get(name, {})
    if not isinstance(value, dict):
        raise ValueError(f"[{name}] must be a table")  # noqa: TRY004 - a config error, like the others
    return value


def _port(table: dict, section: str, default: int) -> int:
    value = table.get("port", default)
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 65535:
        raise ValueError(f"{section}.port must be an integer from 1 to 65535")
    return value


@dataclass(frozen=True)
class EndbotdConfig:
    base: Path
    link_host: str = "127.0.0.1"
    link_port: int = DEFAULT_LINK_PORT
    link_token_file: Path = Path("endbotd.token")
    runtime_host: str = "127.0.0.1"
    runtime_port: int = DEFAULT_RUNTIME_PORT
    runtime_token_file: Path = Path("control.token")
    runtime_timeout: float = 10.0
    stores_directory: Path = Path(".")
    scripts_folder: Path = Path("scripts")
    journal_file: Path = Path("endbotd-runs.json")
    hotbar: dict = field(default_factory=dict)
    authorization: dict = field(default_factory=dict)
    limits: dict | None = None
    ui: dict = field(default_factory=dict)
    scripting: dict = field(default_factory=dict)
    permissions: dict = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> EndbotdConfig:
        path = Path(path)
        document = tomllib.loads(path.read_text(encoding="utf-8"))
        base = path.resolve().parent

        def resolve(value: object, default: str) -> Path:
            candidate = Path(str(value if value is not None else default))
            return candidate if candidate.is_absolute() else base / candidate

        link, runtime, plugin, stores, run = (
            _table(document, name) for name in ("link", "runtime", "plugin", "stores", "run")
        )
        timeout = runtime.get("timeout-seconds", 10.0)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 300:
            raise ValueError("runtime.timeout-seconds must be a number from 0 to 300")
        return cls(
            base=base,
            link_host=str(link.get("host", "127.0.0.1")),
            link_port=_port(link, "link", DEFAULT_LINK_PORT),
            link_token_file=resolve(link.get("token-file"), "endbotd.token"),
            runtime_host=str(runtime.get("host", "127.0.0.1")),
            runtime_port=_port(runtime, "runtime", DEFAULT_RUNTIME_PORT),
            runtime_token_file=resolve(runtime.get("token-file"), "control.token"),
            runtime_timeout=float(timeout),
            stores_directory=resolve(stores.get("directory"), "."),
            scripts_folder=resolve(stores.get("scripts-folder"), "scripts"),
            journal_file=resolve(run.get("journal-file"), "endbotd-runs.json"),
            hotbar={key: plugin[key] for key in ("hotbar-sync-interval-ticks", "hotbar-sync-resend-ticks")
                    if key in plugin},
            authorization=_table(document, "authorization"),
            limits=document.get("limits"),
            ui=_table(document, "ui"),
            scripting=_table(document, "scripting"),
            permissions=_table(document, "permissions"),
        )
