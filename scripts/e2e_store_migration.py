#!/usr/bin/env python3
"""End-to-end check of the Group and script stores across update and rollback.

Extracts a pre-endbotd operator bundle (0.4.0), enables scripting, creates a
Group and a stored script from a ``.luau`` file, then updates to an endbotd
candidate. The candidate's first ``endbot start`` must carry both stores into
``state/`` (docs/ENDBOTD.md P7): the marker, the data files, the moved script
files, the ``README.txt`` left behind, and the backup under ``backups/``. The
check then removes the Group member and edits the script on the candidate,
rolls back, and starts the old version again to prove the changed stores came
back.

Groups are driven through the same BDS console channel that
``scripts/e2e_consumer.py`` uses. The report is written to ``report.json`` in
the work directory; tokens are never printed or recorded.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from e2e_consumer import (
    CONTROLLER,
    ConsumerError,
    assert_no_toolchain,
    build_linux_shim,
    control_request,
    current_version,
    dump_diagnostics,
    extract,
    kill_tree,
    launcher_command,
    repack_as_next_version,
    run_launcher,
    sanitized_environment,
    server_log_text,
    wait_for,
)

LOG_PREFIX = "e2e-store-migration"
GROUP = "migration"
FILE_SCRIPT = "migration_file"
STORED_SCRIPT = "migration_saved"
ORIGINAL_SOURCE = 'params {}\nlog("migration e2e original")\n'
CHANGED_SOURCE = 'params {}\n-- changed-on-new-version\nlog("migration e2e changed")\n'
CHANGED_MARKER = "changed-on-new-version"
CONTROL_PORT = 19142
_TABLE = re.compile(r"^\s*\[([^\[\]]+)\]\s*(?:#.*)?$")


def log(message: str) -> None:
    print(f"{LOG_PREFIX}: {message}", flush=True)


# ---------------------------------------------------------------------------
# Bundle and version helpers


def _archive_names(archive: Path) -> list[str]:
    if archive.name.lower().endswith(".zip"):
        with zipfile.ZipFile(archive) as bundle:
            return bundle.namelist()
    with tarfile.open(archive, "r:gz") as bundle:
        return bundle.getnames()


def bundle_version(archive: Path) -> str:
    """The single ``app/<version>/`` named by a release bundle."""

    versions: set[str] = set()
    for name in _archive_names(archive):
        parts = [part for part in name.replace("\\", "/").split("/") if part]
        if len(parts) >= 3 and parts[0] == "app" and parts[1] != "current":
            versions.add(parts[1])
        elif (
            len(parts) >= 4
            and parts[0].startswith("endbot-")
            and parts[1] == "app"
            and parts[2] != "current"
        ):
            versions.add(parts[2])
    if len(versions) != 1:
        raise ConsumerError(
            f"{archive.name} should hold exactly one app/<version>/ directory, found "
            f"{sorted(versions) or 'none'}"
        )
    return versions.pop()


def version_key(value: str) -> tuple[int, ...]:
    """A numeric release key for comparing bundle version names.

    Pre-release/build suffixes (``0.4.0-rc1``, ``0.4.0+e2e``) compare by their
    numeric release; that is enough to decide whether an archive must be
    repacked as a synthetic next version.
    """

    match = re.match(r"^v?(\d+(?:\.\d+)*)", value.strip())
    if match is None:
        raise ConsumerError(f"cannot compare version {value!r}")
    return tuple(int(part) for part in match.group(1).split("."))


# ---------------------------------------------------------------------------
# endbot.toml and file helpers


def enable_scripting(path: Path) -> None:
    """Set ``[scripting] enabled = true`` in an instance's endbot.toml.

    The 0.4.0 setup writes ``enabled = false`` and has no CLI switch; its
    docs/SCRIPTING.md says to edit the value and restart. Comments and the
    rest of the file are kept.
    """

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise ConsumerError(f"{path} cannot be read: {error}") from error
    lines = text.splitlines()
    table: str | None = None
    changed = False
    for index, line in enumerate(lines):
        header = _TABLE.match(line)
        if header is not None:
            table = header.group(1).strip()
            continue
        if table != "scripting":
            continue
        match = re.match(r"^(\s*)enabled\s*=\s*(true|false)(\s*(?:#.*)?)$", line)
        if match is not None:
            lines[index] = f"{match.group(1)}enabled = true{match.group(3)}"
            changed = True
            break
    if not changed:
        header_index = None
        for index, line in enumerate(lines):
            header = _TABLE.match(line)
            if header is not None and header.group(1).strip() == "scripting":
                header_index = index
                break
        if header_index is None:
            lines.extend(["", "[scripting]", "enabled = true"])
        else:
            lines.insert(header_index + 1, "enabled = true")
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write("\n".join(lines))
        if text.endswith(("\n", "\r")):
            stream.write("\n")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(text)


def read_text(path: Path, description: str) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise ConsumerError(f"{description} {path} cannot be read: {error}") from error


def load_json(path: Path, description: str) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ConsumerError(f"{description} {path} cannot be read: {error}") from error


def assert_text(path: Path, expected: str, description: str) -> None:
    actual = read_text(path, description)
    if actual != expected:
        raise ConsumerError(f"{description} {path} does not hold the expected text")


def assert_marker(path: Path, expected: dict[str, int]) -> None:
    document = load_json(path, "store migration marker")
    if document != expected:
        raise ConsumerError(f"{path} holds {document!r}, expected {expected!r}")


def group_members(path: Path, name: str) -> tuple[str, ...]:
    """The member identities of one Group in a Groups store."""

    document = load_json(path, "Groups store")
    if not isinstance(document, dict) or not isinstance(document.get("groups"), list):
        raise ConsumerError(f"Groups store {path} has no valid groups list")
    for row in document["groups"]:
        if not isinstance(row, dict) or not isinstance(row.get("name"), str):
            continue
        if row["name"].casefold() != name.casefold():
            continue
        members = row.get("members")
        if not isinstance(members, list) or not all(isinstance(member, str) for member in members):
            raise ConsumerError(f"Group #{row['name']} in {path} has no valid members list")
        return tuple(members)
    raise ConsumerError(f"Group #{name} is missing from {path}")


def assert_group_members(path: Path, name: str, expected: tuple[str, ...]) -> tuple[str, ...]:
    members = group_members(path, name)
    if members != expected:
        raise ConsumerError(f"Group #{name} in {path} has members {members}, expected {expected}")
    return members


def script_source(path: Path, name: str) -> str:
    """The source of one stored script in a scripts store."""

    document = load_json(path, "scripts store")
    if (
        not isinstance(document, dict)
        or document.get("version") not in {1, 2}
        or not isinstance(document.get("scripts"), list)
    ):
        raise ConsumerError(f"scripts store {path} has no valid scripts list")
    for row in document["scripts"]:
        if not isinstance(row, dict) or not isinstance(row.get("name"), str):
            continue
        if row["name"].casefold() != name.casefold():
            continue
        source = row.get("source")
        if not isinstance(source, str):
            raise ConsumerError(f"script {row['name']} in {path} has no source")
        return source
    raise ConsumerError(f"script {name} is missing from {path}")


def assert_script_source(path: Path, name: str, expected: str) -> str:
    source = script_source(path, name)
    if source != expected:
        raise ConsumerError(f"script {name} in {path} does not hold the expected source")
    return source


def assert_clean_exit(instance: Path, *, endbotd: bool) -> None:
    record = load_json(instance / "state" / "run" / "last-exit.json", "last exit record")
    if not isinstance(record, dict):
        raise ConsumerError("last exit record is not a JSON object")
    keys = ("runtimeExit", "endbotdExit", "serverExit") if endbotd else ("runtimeExit", "serverExit")
    if record.get("unclean") or any(record.get(key) != 0 for key in keys):
        raise ConsumerError(f"stop was not clean: {record}")


def _group_has(path: Path, name: str, count: int) -> bool:
    try:
        return len(group_members(path, name)) == count
    except ConsumerError:
        return False


def _script_is(path: Path, name: str, source: str) -> bool:
    try:
        return script_source(path, name) == source
    except ConsumerError:
        return False


# ---------------------------------------------------------------------------
# The two layouts


def plugin_data(instance: Path) -> Path:
    return instance / "server" / "plugins" / "endbot"


def old_scripts(instance: Path) -> Path:
    return plugin_data(instance) / "scripts"


def state(instance: Path) -> Path:
    return instance / "state"


def new_scripts(instance: Path) -> Path:
    return instance / "scripts"


def _backups(instance: Path, direction: str) -> list[Path]:
    root = instance / "backups"
    return sorted(entry for entry in root.glob(f"stores-{direction}-*") if entry.is_dir())


def assert_migrated(
    instance: Path,
    group: str,
    stored_script: str,
    stored_source: str,
    file_script: str,
    file_source: str,
    member: str,
) -> Path:
    """Assert the candidate's first start carried the old layout into ``state/``."""

    state_dir = state(instance)
    assert_marker(state_dir / "migrations.json", {"stores": 2})
    assert_group_members(state_dir / "groups.json", group, (member,))
    assert_script_source(state_dir / "scripts.json", stored_script, stored_source)
    moved = new_scripts(instance) / f"{file_script}.luau"
    assert_text(moved, file_source, "moved script file")
    if (old_scripts(instance) / f"{file_script}.luau").exists():
        raise ConsumerError("the old script folder still holds a moved .luau file")
    readme = old_scripts(instance) / "README.txt"
    if not readme.is_file():
        raise ConsumerError(f"the old script folder has no {readme.name} pointing at {moved}")
    # P7 leaves the plugin data files in place; only the scripts move.
    for name in ("groups.json", "scripts.json"):
        if not (plugin_data(instance) / name).is_file():
            raise ConsumerError(f"update removed the original {plugin_data(instance) / name}")
    backups = _backups(instance, "update")
    if not backups:
        raise ConsumerError(f"no stores-update-* backup under {instance / 'backups'}")
    latest = backups[-1]
    for relative in (
        Path("plugin") / "groups.json",
        Path("plugin") / "scripts.json",
        Path("plugin-scripts") / f"{file_script}.luau",
    ):
        if not (latest / relative).is_file():
            raise ConsumerError(f"update backup {latest} is missing {relative}")
    return latest


def assert_rolled_back(
    instance: Path,
    group: str,
    stored_script: str,
    stored_source: str,
    file_script: str,
    changed_source: str,
) -> None:
    """Assert the rollback wrote the changed stores back into the old layout."""

    assert_marker(state(instance) / "migrations.json", {"stores": 1})
    data = plugin_data(instance)
    assert_group_members(data / "groups.json", group, ())
    assert_script_source(data / "scripts.json", stored_script, stored_source)
    assert_text(old_scripts(instance) / f"{file_script}.luau", changed_source, "rolled-back script file")
    if (new_scripts(instance) / f"{file_script}.luau").exists():
        raise ConsumerError("the rollback left the script file in the new scripts folder")
    if (old_scripts(instance) / "README.txt").exists():
        raise ConsumerError("the rollback left README.txt in the old script folder")
    if not _backups(instance, "rollback"):
        raise ConsumerError(f"no stores-rollback-* backup under {instance / 'backups'}")


# ---------------------------------------------------------------------------
# Report


def new_report() -> dict[str, object]:
    return {"ok": False, "stages": []}


@contextlib.contextmanager
def stage(report: dict[str, object], name: str):
    entry: dict[str, object] = {"stage": name, "ok": False}
    stages = report["stages"]
    assert isinstance(stages, list)
    stages.append(entry)
    yield entry
    entry["ok"] = True


def write_report(report: dict[str, object], path: Path) -> None:
    write_text(path, json.dumps(report, indent=2) + "\n")


# ---------------------------------------------------------------------------
# Supervised sessions


@dataclass(frozen=True)
class Live:
    instance: Path
    environment: dict[str, str]
    supervisor: subprocess.Popen
    token: str = field(repr=False)
    port: int = CONTROL_PORT


def _runtime_answers(port: int, token: str) -> bool:
    try:
        control_request(port, token, "ping")
        return True
    except (OSError, ValueError, ConsumerError):
        return False


@contextlib.contextmanager
def running(
    instance: Path,
    environment: dict[str, str],
    bot: str,
    *,
    transport: str,
    label: str,
    endbotd: bool,
    spawn: bool = True,
):
    """One supervised start, with a version-aware clean stop.

    ``e2e_consumer.session`` assumes an endbotd-era log line and last-exit key,
    so the pre-endbotd side needs this small wrapper around the same helpers.
    """

    log(f"$ endbot start (supervisor, {label}, app {current_version(instance)}, {transport})")
    supervisor_log = (instance.parent / f"supervisor-{label}.log").open("w", encoding="utf-8", errors="replace")
    supervisor = subprocess.Popen(
        launcher_command(instance, "start"),
        cwd=instance,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=supervisor_log,
        stderr=subprocess.STDOUT,
    )
    stopped = False
    try:
        token_file = instance / "state" / "secrets" / "control.token"
        wait_for("runtime control token", token_file.is_file, 120, supervisor)
        token = token_file.read_text(encoding="utf-8").strip()
        port = CONTROL_PORT
        wait_for("runtime ping", lambda: _runtime_answers(port, token), 120, supervisor)
        generated = load_json(instance / "state/generated/endbot-runtime.json", "runtime config")
        if not isinstance(generated, dict) or generated.get("transport", "nethernet") != transport:
            raise ConsumerError("generated runtime transport differs from setup selection")
        wait_for(
            "Endbot plugin enabled in BDS", lambda: "Enabling endbot" in server_log_text(instance), 300, supervisor
        )
        if endbotd:
            wait_for(
                "Endbot plugin linked to endbotd",
                lambda: "Endbot linked to endbotd" in server_log_text(instance),
                60,
                supervisor,
            )
        if spawn:
            control_request(port, token, "spawn", name=bot)
        try:
            wait_for(
                f"{bot} online in the runtime",
                lambda: control_request(port, token, "status", name=bot)["connectionState"] == "online",
                120,
                supervisor,
            )
        except ConsumerError:
            status = control_request(port, token, "status", name=bot)
            log(f"Bot diagnostic: state={status['connectionState']}, lastError={status.get('lastError')}")
            raise
        wait_for(
            f"BDS accepted {bot} through local-bot trust",
            lambda: (
                f"Accepted local bot '{bot}'" in server_log_text(instance)
                and f"{bot} joined the game" in server_log_text(instance)
            ),
            60,
            supervisor,
        )
        yield Live(instance=instance, environment=environment, supervisor=supervisor, token=token, port=port)

        run_launcher(instance, environment, "stop", timeout=300)
        stopped = True
        supervisor.wait(timeout=60)
        if supervisor.returncode != 0:
            raise ConsumerError(f"supervisor exited {supervisor.returncode}")
        assert_clean_exit(instance, endbotd=endbotd)
        log(f"ok: clean stop ({label})")
    finally:
        if not stopped and supervisor.poll() is None:
            try:
                run_launcher(instance, environment, "stop", timeout=300)
            except (ConsumerError, subprocess.TimeoutExpired) as error:
                log(f"cleanup stop failed: {error}")
            try:
                supervisor.wait(timeout=60)
            except subprocess.TimeoutExpired:
                kill_tree(supervisor)
        supervisor_log.close()


def console(live: Live, line: str, *expected: str, timeout: float = 60) -> str:
    """Queue one BDS console line and wait for its reply in the server log."""

    offset = len(server_log_text(live.instance))
    run_launcher(live.instance, live.environment, "console", line)
    if expected:
        wait_for(
            f"console reply to {line!r}",
            lambda: all(text in server_log_text(live.instance)[offset:] for text in expected),
            timeout,
            live.supervisor,
        )
    return server_log_text(live.instance)[offset:]


# ---------------------------------------------------------------------------
# Scenario


def assert_stores_pass(doctor_output: str) -> None:
    if not any(line.startswith("PASS stores:") for line in doctor_output.splitlines()):
        raise ConsumerError("doctor did not report PASS for the stores check")


def exercise(
    instance: Path,
    environment: dict[str, str],
    bot: str,
    new_archive: Path,
    transport: str,
    work: Path,
    report: dict[str, object],
) -> None:
    data = plugin_data(instance)
    old_file = old_scripts(instance) / f"{FILE_SCRIPT}.luau"

    with stage(report, "old-setup") as entry:
        run_launcher(
            instance,
            environment,
            "setup",
            "--fresh",
            "--controller",
            CONTROLLER,
            "--transport",
            transport,
            "--apply",
        )
        enable_scripting(instance / "endbot.toml")
        run_launcher(instance, environment, "doctor")
        old_version = current_version(instance)
        report["old_version"] = old_version
        entry["old_version"] = old_version

    with stage(report, "old-content") as entry:
        write_text(old_file, ORIGINAL_SOURCE)
        with running(
            instance, environment, bot, transport=transport, label="old", endbotd=False, spawn=True
        ) as live:
            console(live, f"botgroup create #{GROUP}", f"#{GROUP}: group created")
            console(live, f"botgroup add #{GROUP} {bot}", f"#{GROUP}: add: {bot}")
            console(
                live,
                f"botscript copy {FILE_SCRIPT} {STORED_SCRIPT}",
                f"Script {FILE_SCRIPT} copied to {STORED_SCRIPT}",
            )
            wait_for(
                "old Groups store written",
                lambda: _group_has(data / "groups.json", GROUP, 1),
                30,
                live.supervisor,
            )
            wait_for(
                "old scripts store written",
                lambda: _script_is(data / "scripts.json", STORED_SCRIPT, ORIGINAL_SOURCE),
                30,
                live.supervisor,
            )
        member = group_members(data / "groups.json", GROUP)[0]
        assert_script_source(data / "scripts.json", STORED_SCRIPT, ORIGINAL_SOURCE)
        assert_text(old_file, ORIGINAL_SOURCE, "old script file")
        entry["group_member"] = member

    with stage(report, "update") as entry:
        candidate_version = bundle_version(new_archive)
        if version_key(candidate_version) <= version_key(old_version):
            archive, new_version = repack_as_next_version(new_archive.resolve(), work / "next")
            entry["repacked"] = True
        else:
            archive, new_version = new_archive.resolve(), candidate_version
        run_launcher(instance, environment, "update", str(archive))
        actual = current_version(instance)
        if actual != new_version:
            raise ConsumerError(f"update left app/current at {actual}, expected {new_version}")
        report["new_version"] = new_version
        entry["from"] = old_version
        entry["to"] = new_version

    with stage(report, "new-version") as entry:
        with running(
            instance, environment, bot, transport=transport, label="updated", endbotd=True, spawn=True
        ) as live:
            doctor_output = run_launcher(instance, environment, "doctor")
            assert_stores_pass(doctor_output)
            backup = assert_migrated(
                instance, GROUP, STORED_SCRIPT, ORIGINAL_SOURCE, FILE_SCRIPT, ORIGINAL_SOURCE, member
            )
            entry["migration"] = True
            entry["backup"] = str(backup)
            console(live, f"botgroup remove #{GROUP} {bot}", f"#{GROUP}: remove: {bot}")
            wait_for(
                "candidate Groups store changed",
                lambda: _group_has(state(instance) / "groups.json", GROUP, 0),
                30,
                live.supervisor,
            )
            write_text(new_scripts(instance) / f"{FILE_SCRIPT}.luau", CHANGED_SOURCE)
            assert_text(new_scripts(instance) / f"{FILE_SCRIPT}.luau", CHANGED_SOURCE, "candidate script file")
            entry["content_changed"] = True
        assert_group_members(state(instance) / "groups.json", GROUP, ())
        assert_text(new_scripts(instance) / f"{FILE_SCRIPT}.luau", CHANGED_SOURCE, "candidate script file")

    with stage(report, "rollback") as entry:
        run_launcher(instance, environment, "update", "--rollback")
        actual = current_version(instance)
        if actual != old_version:
            raise ConsumerError(f"rollback left app/current at {actual}, expected {old_version}")
        assert_rolled_back(
            instance, GROUP, STORED_SCRIPT, ORIGINAL_SOURCE, FILE_SCRIPT, CHANGED_SOURCE
        )
        entry["from"] = new_version
        entry["to"] = old_version

    with stage(report, "old-version-restored") as entry:
        with running(
            instance, environment, bot, transport=transport, label="rolled-back", endbotd=False, spawn=True
        ) as live:
            console(live, "botgroup list", f"#{GROUP} (0)")
            console(live, f"botscript show {FILE_SCRIPT}", CHANGED_MARKER)
            assert_group_members(data / "groups.json", GROUP, ())
            assert_text(old_file, CHANGED_SOURCE, "restored old script file")
        assert_script_source(data / "scripts.json", STORED_SCRIPT, ORIGINAL_SOURCE)
        entry["group_change_present"] = True
        entry["script_change_present"] = True


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("old_archive", type=Path, help="pre-endbotd (0.4.0) platform bundle")
    parser.add_argument("new_archive", type=Path, help="endbotd candidate platform bundle")
    parser.add_argument("--work-dir", type=Path, help="extraction directory (default: a new temporary directory)")
    parser.add_argument("--transport", choices=("nethernet", "raknet"), default="raknet")
    parser.add_argument("--bot", default="MigrationBot")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    work = (args.work_dir or Path(tempfile.mkdtemp(prefix="endbot-store-migration-"))).resolve()
    work.mkdir(parents=True, exist_ok=True)
    report = new_report()
    report["old_archive"] = str(args.old_archive.resolve())
    report["new_archive"] = str(args.new_archive.resolve())
    instance = None
    try:
        instance = extract(args.old_archive.resolve(), work / "bundle")
        shim = build_linux_shim(work / "shim") if os.name != "nt" else None
        environment = sanitized_environment(dict(os.environ), shim)
        assert_no_toolchain(environment)
        exercise(
            instance,
            environment,
            args.bot,
            args.new_archive.resolve(),
            args.transport,
            work,
            report,
        )
        report["ok"] = True
        log("PASS")
        return 0
    except (
        ConsumerError,
        OSError,
        subprocess.SubprocessError,
        ValueError,
        KeyError,
        zipfile.BadZipFile,
        tarfile.TarError,
    ) as error:
        report["error"] = str(error)
        print(f"{LOG_PREFIX}: FAIL: {error}", file=sys.stderr)
        dump_diagnostics(instance)
        return 1
    finally:
        try:
            write_report(report, work / "report.json")
        except OSError as error:
            print(f"{LOG_PREFIX}: WARN: could not write {work / 'report.json'}: {error}", file=sys.stderr)
        print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
