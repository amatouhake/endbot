"""Instance directory resolution and the docs/OPERATIONS.md section 1 layout paths.

The instance directory (``<instance>`` in section 1) is resolved in this order:

1. the ``--instance PATH`` command-line argument;
2. the ``ENDBOT_INSTANCE`` environment variable;
3. the directory containing the launcher, when the CLI runs through the
   ``endbot`` / ``endbot.cmd`` / ``endbot.exe`` launcher that setup writes into
   ``<instance>/`` (section 1); a launcher directory only counts when it looks like an
   instance (it holds ``endbot.toml`` or ``app/current``), so the identically
   named console script in a Python environment does not match;
4. the current working directory.

The chosen directory must already exist; otherwise :class:`InstanceError` names
the source the path came from so the operator can fix the right input.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

INSTANCE_ENV_VAR = "ENDBOT_INSTANCE"
LAUNCHER_NAMES = frozenset({"endbot", "endbot.cmd", "endbot.exe"})


class InstanceError(RuntimeError):
    """The instance directory cannot be resolved."""


@dataclass(frozen=True, slots=True)
class InstancePaths:
    """The fixed section 1 layout under one instance directory."""

    root: Path
    endbot_toml: Path
    app_current: Path
    state_secrets: Path
    state_profiles: Path
    state_controllers: Path
    state_generated: Path
    state_run: Path
    backups: Path
    control_token: Path
    owner_private_key: Path
    owner_public_key: Path

    @classmethod
    def for_root(cls, root: Path) -> InstancePaths:
        root = Path(root)
        state_secrets = root / "state" / "secrets"
        return cls(
            root=root,
            endbot_toml=root / "endbot.toml",
            app_current=root / "app" / "current",
            state_secrets=state_secrets,
            state_profiles=root / "state" / "profiles",
            state_controllers=root / "state" / "controllers.json",
            state_generated=root / "state" / "generated",
            state_run=root / "state" / "run",
            backups=root / "backups",
            control_token=state_secrets / "control.token",
            owner_private_key=state_secrets / "owner-private.pem",
            owner_public_key=state_secrets / "owner-public.pem",
        )


def _launcher_dir(argv0: str | None) -> Path | None:
    if not argv0:
        return None
    candidate = Path(argv0)
    if candidate.name.lower() not in LAUNCHER_NAMES:
        return None
    directory = candidate.resolve().parent
    if not (directory / "endbot.toml").exists() and not (directory / "app" / "current").exists():
        return None
    return directory


def resolve_instance_root(
    cli_instance: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
    argv0: str | None = None,
    cwd: Path | None = None,
) -> tuple[Path, str]:
    """Return ``(instance_root, source_description)`` per the module rule.

    Raises :class:`InstanceError` when the chosen directory does not exist.
    """

    environ = os.environ if environ is None else environ
    if cli_instance is not None:
        root, source = Path(cli_instance), "--instance"
    elif environ.get(INSTANCE_ENV_VAR):
        root, source = Path(environ[INSTANCE_ENV_VAR]), f"${INSTANCE_ENV_VAR}"
    else:
        launcher = _launcher_dir(argv0)
        if launcher is not None:
            root, source = launcher, "launcher directory"
        else:
            root, source = Path.cwd() if cwd is None else Path(cwd), "current working directory"
    try:
        exists = root.is_dir()
    except OSError as error:  # unreadable path, broken symlink, ...
        raise InstanceError(f"instance directory {root} ({source}) cannot be inspected: {error}") from error
    if not exists:
        raise InstanceError(f"instance directory {root} ({source}) does not exist or is not a directory")
    return root.resolve(), source
