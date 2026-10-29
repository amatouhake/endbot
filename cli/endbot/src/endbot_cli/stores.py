"""The Group and script stores between the plugin data folder and ``state/`` (docs/ENDBOTD.md P7, S1).

Two layouts exist:

- **plugin** (stores 1, every version before endbotd): ``<server>/plugins/endbot/groups.json``,
  ``scripts.json`` and ``scripts/*.luau``;
- **state** (stores 2, endbotd): ``state/groups.json``, ``state/scripts.json`` and
  ``<instance>/scripts/*.luau``.

``state/migrations.json`` (``{"stores": 2}``) says which one is authoritative; without it the plugin
layout is. :func:`migrate` (update, setup and the first ``endbot start``) and :func:`rollback`
(``endbot update --rollback``) are the same carry in opposite directions:

1. back up both layouts under ``backups/stores-<direction>-<time>/``;
2. copy the data files from the authoritative layout to the other one (atomically, overwriting);
3. move the script files (copy, then remove the source); the update leaves a ``README.txt`` in the
   old folder pointing to the new one, the rollback removes it;
4. write the marker last.

The source stays authoritative until step 4, so an interrupted carry is finished by running it
again, and running it once more after that changes nothing. The data files of the source are
left in place.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from endbot_cli.fsutil import atomic_write_text
from endbot_cli.instance import InstancePaths

PLUGIN_STORES = 1
STATE_STORES = 2
DATA_FILES = ("groups.json", "scripts.json")
SCRIPT_SUFFIX = ".luau"
README_NAME = "README.txt"


class StoreError(RuntimeError):
    """The stores cannot be carried; the message names the problem and the fix."""


@dataclass(frozen=True, slots=True)
class StoreLayout:
    plugin_data: Path  # <server>/plugins/endbot
    state: Path  # <instance>/state
    scripts: Path  # <instance>/scripts (S1)
    backups: Path

    @classmethod
    def for_instance(cls, paths: InstancePaths, server: Path) -> StoreLayout:
        return cls(
            plugin_data=Path(server) / "plugins" / "endbot",
            state=paths.root / "state",
            scripts=paths.root / "scripts",
            backups=paths.backups,
        )

    @property
    def plugin_scripts(self) -> Path:
        return self.plugin_data / "scripts"

    @property
    def marker(self) -> Path:
        return self.state / "migrations.json"


def read_marker(layout: StoreLayout) -> int | None:
    """The ``stores`` value of ``state/migrations.json``; None when the file does not exist."""

    try:
        document = json.loads(layout.marker.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise StoreError(f"{layout.marker} cannot be read ({error}); fix or delete it") from error
    value = document.get("stores") if isinstance(document, dict) else None
    if type(value) is not int or value < PLUGIN_STORES:
        raise StoreError(f"{layout.marker} has no valid 'stores' number; fix or delete it")
    if value > STATE_STORES:
        raise StoreError(
            f"{layout.marker} names store layout {value}, written by a newer Endbot; "
            "update to that version again, or roll back with it"
        )
    return value


def script_files(directory: Path) -> list[Path]:
    """The ``.luau`` files directly in ``directory``, as the script loader sees them."""

    try:
        return sorted(
            entry for entry in directory.iterdir() if entry.suffix.lower() == SCRIPT_SUFFIX and entry.is_file()
        )
    except (FileNotFoundError, NotADirectoryError):
        return []


def describe(data_dir: Path, scripts_dir: Path) -> str:
    present = [name for name in DATA_FILES if (data_dir / name).is_file()]
    count = len(script_files(scripts_dir))
    return f"{', '.join(present) or 'no data files'}, {count} script file{'' if count == 1 else 's'}"


def migrate(layout: StoreLayout) -> list[str]:
    """Make ``state/`` authoritative (the update direction); return what was done."""

    return _carry(layout, up=True)


def rollback(layout: StoreLayout) -> list[str]:
    """Make the plugin data folder authoritative again (the rollback direction); return what was done."""

    return _carry(layout, up=False)


def _readme(layout: StoreLayout) -> str:
    return (
        "Endbot no longer reads script files from this folder.\n"
        f"They moved to {layout.scripts} (next to endbot.toml); put new .luau files there.\n"
        "`endbot update --rollback` moves them back here.\n"
    )


def _carry(layout: StoreLayout, *, up: bool) -> list[str]:
    marker = read_marker(layout)
    if up and marker == STATE_STORES:
        return [f"stores: state/ is already authoritative ({layout.marker})"]
    if not up and marker != STATE_STORES:
        return [f"stores: the plugin data folder is already authoritative ({layout.plugin_data})"]

    if up:
        data = (layout.plugin_data, layout.state)
        scripts = (layout.plugin_scripts, layout.scripts)
    else:
        data = (layout.state, layout.plugin_data)
        scripts = (layout.scripts, layout.plugin_scripts)
    try:
        messages = _backup(layout, "update" if up else "rollback")
        for name in DATA_FILES:
            source, target = data[0] / name, data[1] / name
            if source.is_file():
                _copy(source, target)
            elif target.exists():
                target.unlink()  # the authoritative layout has none; the backup keeps the stale copy
        scripts[1].mkdir(parents=True, exist_ok=True)
        moved = script_files(scripts[0])
        for source in moved:
            _copy(source, scripts[1] / source.name)
            source.unlink()
        if up and layout.plugin_scripts.is_dir():
            atomic_write_text(layout.plugin_scripts / README_NAME, _readme(layout))
        if not up:
            with contextlib.suppress(FileNotFoundError):
                (layout.plugin_scripts / README_NAME).unlink()
        atomic_write_text(layout.marker, json.dumps({"stores": STATE_STORES if up else PLUGIN_STORES}) + "\n")
    except OSError as error:
        raise StoreError(
            f"carrying the stores {'into state/' if up else 'back to the plugin data folder'} stopped: {error}; "
            "fix the cause and run the same command again (it continues where it stopped)"
        ) from error
    messages.append(
        f"stores: copied {', '.join(name for name in DATA_FILES if (data[1] / name).is_file()) or 'no data files'} "
        f"into {data[1]} and moved {len(moved)} script file{'' if len(moved) == 1 else 's'} into {scripts[1]}"
    )
    messages.append(
        f"stores: {'state/' if up else 'the plugin data folder'} is now authoritative "
        f"({layout.marker}: stores {STATE_STORES if up else PLUGIN_STORES})"
    )
    return messages


def _backup(layout: StoreLayout, direction: str) -> list[str]:
    sources = {
        "plugin": [layout.plugin_data / name for name in DATA_FILES],
        "state": [layout.state / name for name in DATA_FILES],
        "plugin-scripts": script_files(layout.plugin_scripts),
        "instance-scripts": script_files(layout.scripts),
    }
    files = [(group, path) for group, paths in sources.items() for path in paths if path.is_file()]
    if not files:
        return []
    stamp = time.strftime("%Y%m%d-%H%M%S")
    directory = layout.backups / f"stores-{direction}-{stamp}"
    suffix = 1
    while directory.exists():
        suffix += 1
        directory = layout.backups / f"stores-{direction}-{stamp}-{suffix}"
    for group, path in files:
        target = directory / group / path.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    return [f"stores: backed up {len(files)} store files into {directory}"]


def _copy(source: Path, target: Path) -> None:
    """Copy complete-before-publish: a reader never sees a partial target."""

    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=str(target.parent), prefix=f"{target.name}.", suffix=".tmp")
    os.close(descriptor)
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, target)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temporary)
        raise
