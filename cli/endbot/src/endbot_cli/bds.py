"""BDS acquisition through Endstone's own bootstrap (docs/OPERATIONS.md section 6).

Endbot never bundles, vendors, or redistributes the Bedrock Dedicated Server
(AGENTS.md). The only acquisition path is the pinned Endstone's own bootstrap:
``endstone.cli.base.Bootstrap`` instantiated exactly the way
``endstone.cli.__init__`` instantiates it (``no_confirm=True``, the target
``server_folder``, the default remote, ``interactive=False``), with only its
install/update step ``_install()`` called. ``_install()`` downloads when no
server executable exists and otherwise runs ``_update()``; nothing here ever
starts BDS.

``endstone`` is a private, exactly pinned API, so it is confined to this one
module and executed in the bundled/active interpreter as a subprocess
(``<python> -m endbot_cli.bds``): the operator CLI itself never imports
``endstone`` and keeps working when Endstone is broken. The test suite drives
the helper with a stub ``endstone`` package and asserts the exact bootstrap
arguments, and ``tests/test_bds_contract.py`` asserts the private methods exist
(skipped when Endstone is not installed) so an Endstone bump fails loudly.
"""

from __future__ import annotations

import argparse
import importlib
import platform
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

# The default remote of `endstone.cli.__init__`'s `-r/--remote` option.
DEFAULT_REMOTE = "https://raw.githubusercontent.com/EndstoneMC/bedrock-server-data/v2"

_PLATFORM_BOOTSTRAPS = {
    "Windows": ("endstone.cli.windows", "WindowsBootstrap"),
    "Linux": ("endstone.cli.linux", "LinuxBootstrap"),
}


class BdsError(RuntimeError):
    """BDS could not be acquired through Endstone's bootstrap."""


def build_bootstrap(
    server_folder: str | Path,
    *,
    remote: str = DEFAULT_REMOTE,
    system: str | None = None,
    importer: Callable[[str], object] = importlib.import_module,
):
    """Instantiate the pinned Endstone bootstrap for ``server_folder``.

    This is the single call site of the private ``endstone.cli`` API; the
    keyword arguments below must match ``Bootstrap.__init__`` exactly.
    """

    system = platform.system() if system is None else system
    if system not in _PLATFORM_BOOTSTRAPS:
        raise BdsError(f"{system} is not supported by Endstone's bootstrap (Windows and Linux only)")
    module_name, class_name = _PLATFORM_BOOTSTRAPS[system]
    module = importer(module_name)
    bootstrap_class = getattr(module, class_name, None)
    if bootstrap_class is None:
        raise BdsError(f"{module_name} has no {class_name}; the pinned Endstone layout changed (see endstone.lock)")
    return bootstrap_class(
        server_folder=str(Path(server_folder).absolute()),
        no_confirm=True,
        remote=remote,
        interactive=False,
    )


def install_locked_bds(
    server_folder: str | Path,
    *,
    remote: str = DEFAULT_REMOTE,
    system: str | None = None,
    importer: Callable[[str], object] = importlib.import_module,
) -> None:
    """Run Endstone's install/update step for ``server_folder`` (never starts BDS)."""

    bootstrap = build_bootstrap(server_folder, remote=remote, system=system, importer=importer)
    bootstrap._install()


def install_command(python: str | Path, server_folder: str | Path, *, remote: str = DEFAULT_REMOTE) -> list[str]:
    """The subprocess command that runs this module's helper in ``python``."""

    return [str(python), "-m", "endbot_cli.bds", "--server-folder", str(server_folder), "--remote", remote]


def run_install(
    python: str | Path,
    server_folder: str | Path,
    *,
    remote: str = DEFAULT_REMOTE,
    runner: Callable[..., object] = subprocess.run,
) -> None:
    """Acquire BDS in ``python`` via :func:`install_command`; raise on failure.

    The subprocess inherits stdout/stderr so the operator sees Endstone's own
    download progress and messages.
    """

    command = install_command(python, server_folder, remote=remote)
    try:
        completed = runner(command, check=False)
    except OSError as error:
        raise BdsError(f"cannot run {command[0]} for BDS acquisition: {error}") from error
    returncode = getattr(completed, "returncode", 1)
    if returncode != 0:
        raise BdsError(
            f"Endstone's BDS acquisition step failed with exit code {returncode} for {server_folder}; "
            "see its output above (nothing was started)"
        )


def main(argv: list[str] | None = None) -> int:
    """Helper entry point (``<python> -m endbot_cli.bds``); runs inside the subprocess."""

    parser = argparse.ArgumentParser(
        prog="endbot-bds",
        description="internal helper: download/update BDS through Endstone's own bootstrap (never starts BDS)",
    )
    parser.add_argument("--server-folder", required=True, help="the BDS server directory")
    parser.add_argument("--remote", default=DEFAULT_REMOTE, help="Endstone's bedrock-server-data remote")
    args = parser.parse_args(argv)
    try:
        install_locked_bds(args.server_folder, remote=args.remote)
    except SystemExit as error:  # _install() exits instead of raising when a download is declined
        return error.code if isinstance(error.code, int) else 1
    except Exception as error:  # noqa: BLE001 - any Endstone failure must become a clean exit code here
        print(f"FAIL bds: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
