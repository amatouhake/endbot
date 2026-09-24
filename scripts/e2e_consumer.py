#!/usr/bin/env python3
"""End-to-end check of an operator bundle as a clean consumer would use it.

Extracts a platform bundle, then drives it only through its launcher with a
sanitized environment in which no system Python or Node.js is reachable:
``setup --fresh --apply`` (BDS downloaded through Endstone), ``doctor``,
``start``, one Bot spawned over the runtime control protocol and seen joining
BDS, a console command, ``doctor --live``, and a clean ``stop``. With
``--exercise-update`` it then updates to a renamed copy of the same bundle
(the real layout, symlinks, and permission bits), restarts with the Bot
resuming, rolls back, and restarts again.

This proves the bundle does not depend on a developer toolchain. It does not
replace the human-client release gates (Xbox authentication, achievements).
"""

from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid
import zipfile
from pathlib import Path

CONTROLLER = "E2EController"
LINUX_SHIM_TOOLS = ("sh", "dirname", "cat")
STRIPPED_VARIABLES = ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "CONDA_PREFIX", "NODE_PATH", "NODE_OPTIONS")


class ConsumerError(RuntimeError):
    pass


def log(message: str) -> None:
    print(f"e2e-consumer: {message}", flush=True)


def extract(archive: Path, destination: Path) -> Path:
    """Extract the bundle and return its single top-level instance directory."""

    destination.mkdir(parents=True, exist_ok=True)
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(destination)
    elif archive.name.endswith((".tar.gz", ".tgz")):
        with tarfile.open(archive, "r:gz") as bundle:
            bundle.extractall(destination, filter="tar")
    else:
        raise ConsumerError(f"unsupported bundle archive: {archive.name}")
    roots = [entry for entry in destination.iterdir() if entry.is_dir()]
    if len(roots) != 1:
        raise ConsumerError(f"expected one top-level directory in {archive.name}, found {len(roots)}")
    return roots[0]


def sanitized_environment(base: dict[str, str], shim_dir: Path | None) -> dict[str, str]:
    """Return an environment with no developer toolchain on PATH.

    Windows keeps only the OS directories. Linux gets a directory holding just
    the few POSIX tools the launcher itself uses, so /usr/bin (which carries a
    system python3 on CI images) is not reachable.
    """

    environment = {key: value for key, value in base.items() if key.upper() not in STRIPPED_VARIABLES}
    environment.pop("ENDBOT_INSTANCE", None)
    for key in list(environment):
        if key.upper().startswith("ENDBOT_"):
            environment.pop(key)
    if os.name == "nt":
        system_root = base.get("SystemRoot", r"C:\Windows")
        # %SystemRoot% itself is left out: CI images put the py launcher there.
        environment["PATH"] = os.pathsep.join(
            [
                os.path.join(system_root, "System32"),
                os.path.join(system_root, "System32", "Wbem"),
                os.path.join(system_root, "System32", "WindowsPowerShell", "v1.0"),
            ]
        )
    else:
        if shim_dir is None:
            raise ConsumerError("a shim directory is required on POSIX")
        environment["PATH"] = str(shim_dir)
    return environment


def build_linux_shim(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    for tool in LINUX_SHIM_TOOLS:
        source = shutil.which(tool)
        if source is None:
            raise ConsumerError(f"host tool {tool} is missing")
        (directory / tool).symlink_to(source)
    return directory


def assert_no_toolchain(environment: dict[str, str]) -> None:
    for tool in ("python", "python3", "py", "node", "npm", "pip"):
        found = shutil.which(tool, path=environment["PATH"])
        if found:
            raise ConsumerError(f"sanitized PATH still exposes {tool}: {found}")
    log(f"sanitized PATH exposes no python/node/npm/pip: {environment['PATH']}")


def launcher_command(instance: Path, *arguments: str) -> list[str]:
    if os.name == "nt":
        return ["cmd.exe", "/d", "/c", str(instance / "endbot.cmd"), *arguments]
    return [str(instance / "endbot"), *arguments]


def run_launcher(instance: Path, environment: dict[str, str], *arguments: str, timeout: float = 900) -> str:
    log(f"$ endbot {' '.join(arguments)}")
    result = subprocess.run(
        launcher_command(instance, *arguments),
        cwd=instance,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    print(result.stdout, flush=True)
    if result.returncode != 0:
        raise ConsumerError(f"endbot {' '.join(arguments)} exited {result.returncode}")
    return result.stdout


def control_request(port: int, token: str, method: str, **params: object) -> object:
    request_id = str(uuid.uuid4())
    payload = json.dumps({"version": 1, "id": request_id, "token": token, "method": method, "params": params})
    with socket.create_connection(("127.0.0.1", port), timeout=15) as connection:
        connection.sendall(payload.encode() + b"\n")
        response = b""
        while b"\n" not in response:
            chunk = connection.recv(65536)
            if not chunk:
                break
            response += chunk
    decoded = json.loads(response.split(b"\n", 1)[0])
    if decoded.get("id") != request_id or not decoded.get("ok"):
        raise ConsumerError(f"control {method} failed: {decoded.get('error')}")
    return decoded.get("result")


def latest_server_log(instance: Path) -> Path | None:
    logs = instance / "state" / "run" / "logs"
    if not logs.is_dir():
        return None
    runs = sorted(entry for entry in logs.iterdir() if entry.is_dir())
    return runs[-1] / "server.log" if runs else None


def server_log_text(instance: Path) -> str:
    path = latest_server_log(instance)
    if path is None or not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def wait_for(description: str, predicate, timeout: float, supervisor: subprocess.Popen | None = None) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            log(f"ok: {description}")
            return
        if supervisor is not None and supervisor.poll() is not None:
            raise ConsumerError(f"supervisor exited ({supervisor.returncode}) while waiting for {description}")
        time.sleep(1)
    raise ConsumerError(f"timed out after {timeout:g}s waiting for {description}")


def current_version(instance: Path) -> str:
    return (instance / "app" / "current").read_text(encoding="utf-8").strip()


def session(instance: Path, environment: dict[str, str], bot: str, *, spawn: bool, label: str) -> None:
    """One supervised start → Bot in BDS → checks → clean stop."""

    log(f"$ endbot start (supervisor, {label}, app {current_version(instance)})")
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
        port = 19142

        def runtime_answers() -> bool:
            try:
                control_request(port, token, "ping")
                return True
            except (OSError, ValueError, ConsumerError):
                return False

        wait_for("runtime ping", runtime_answers, 120, supervisor)
        wait_for("Endbot plugin enabled in BDS", lambda: "Enabling endbot" in server_log_text(instance), 300, supervisor)

        if spawn:
            control_request(port, token, "spawn", name=bot)
        wait_for(
            f"{bot} online in the runtime ({'spawned' if spawn else 'resumed'})",
            lambda: control_request(port, token, "status", name=bot)["connectionState"] == "online",
            120,
            supervisor,
        )
        wait_for(
            f"BDS accepted {bot} through local-bot trust",
            lambda: f"Accepted local bot '{bot}'" in server_log_text(instance)
            and f"{bot} joined the game" in server_log_text(instance),
            60,
            supervisor,
        )
        run_launcher(instance, environment, "console", "list")
        wait_for("console command reached BDS", lambda: "players online" in server_log_text(instance), 30, supervisor)
        run_launcher(instance, environment, "doctor", "--live")

        run_launcher(instance, environment, "stop", timeout=300)
        stopped = True
        supervisor.wait(timeout=60)
        if supervisor.returncode != 0:
            raise ConsumerError(f"supervisor exited {supervisor.returncode}")
        record = json.loads((instance / "state" / "run" / "last-exit.json").read_text(encoding="utf-8"))
        if record.get("unclean") or record.get("runtimeExit") != 0 or record.get("serverExit") != 0:
            raise ConsumerError(f"stop was not clean: {record}")
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


def repack_as_next_version(archive: Path, destination_dir: Path, suffix: str = "-e2e-next") -> tuple[Path, str]:
    """Rewrite a release bundle as a synthetic next version for update/rollback checks.

    Only names change: ``endbot-<v>/`` and ``app/<v>/`` gain ``suffix`` and
    ``app/current`` names the new version. Contents, permission bits, and
    symlinks are copied as they are, so the update exercises the real layout.
    """

    destination_dir.mkdir(parents=True, exist_ok=True)
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as source:
            names = source.namelist()
            root = names[0].split("/", 1)[0]
            version = root.removeprefix("endbot-")
            new_version = version + suffix
            target = destination_dir / archive.name.replace(version, new_version, 1)
            with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as out:
                for info in source.infolist():
                    name = _rename(info.filename, root, version, new_version)
                    data = source.read(info.filename)
                    if name.endswith("/app/current"):
                        data = f"{new_version}\n".encode()
                    clone = zipfile.ZipInfo(name, date_time=info.date_time)
                    clone.external_attr = info.external_attr
                    clone.compress_type = zipfile.ZIP_DEFLATED
                    out.writestr(clone, data)
        return target, new_version
    with tarfile.open(archive, "r:gz") as source:
        members = source.getmembers()
        root = members[0].name.split("/", 1)[0]
        version = root.removeprefix("endbot-")
        new_version = version + suffix
        target = destination_dir / archive.name.replace(version, new_version, 1)
        with tarfile.open(target, "w:gz") as out:
            for member in members:
                clone = member.replace(name=_rename(member.name, root, version, new_version), deep=True)
                if member.isfile():
                    data = source.extractfile(member).read()
                    if clone.name.endswith("/app/current"):
                        data = f"{new_version}\n".encode()
                        clone.size = len(data)
                    out.addfile(clone, io.BytesIO(data))
                else:
                    out.addfile(clone)
    return target, new_version


def _rename(name: str, root: str, version: str, new_version: str) -> str:
    parts = name.split("/")
    if parts[0] == root:
        parts[0] = f"endbot-{new_version}"
    if len(parts) > 2 and parts[1] == "app" and parts[2] == version:
        parts[2] = new_version
    return "/".join(parts)


def exercise(instance: Path, environment: dict[str, str], bot: str, archive: Path, update: bool) -> None:
    run_launcher(instance, environment, "setup", "--fresh", "--controller", CONTROLLER, "--apply")
    run_launcher(instance, environment, "doctor")
    original = current_version(instance)
    session(instance, environment, bot, spawn=True, label="install")
    if not update:
        return

    next_archive, next_version = repack_as_next_version(archive, instance.parent / "next")
    run_launcher(instance, environment, "update", str(next_archive))
    if current_version(instance) != next_version:
        raise ConsumerError(f"update left app/current at {current_version(instance)}, expected {next_version}")
    session(instance, environment, bot, spawn=False, label="updated")

    run_launcher(instance, environment, "update", "--rollback")
    if current_version(instance) != original:
        raise ConsumerError(f"rollback left app/current at {current_version(instance)}, expected {original}")
    session(instance, environment, bot, spawn=False, label="rolled-back")


def kill_tree(process: subprocess.Popen) -> None:
    """Kill a launcher and everything it started (cmd.exe does not forward kills)."""

    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(process.pid)], capture_output=True, check=False)
    else:
        process.kill()
    try:
        process.wait(timeout=30)
    except subprocess.TimeoutExpired:
        pass


def dump_diagnostics(instance: Path | None) -> None:
    if instance is None:
        return
    for path in [*sorted(instance.parent.glob("supervisor-*.log")), latest_server_log(instance)]:
        if path is not None and path.is_file():
            print(f"----- tail of {path} -----")
            print("\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-80:]))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("archive", type=Path, help="platform bundle (.zip on Windows, .tar.gz on Linux)")
    parser.add_argument("--work-dir", type=Path, help="extraction directory (default: a new temporary directory)")
    parser.add_argument("--bot", default="E2EBot")
    parser.add_argument(
        "--exercise-update",
        action="store_true",
        help="after the first session, update to a renamed copy of the bundle, restart, roll back, restart",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    work = args.work_dir or Path(tempfile.mkdtemp(prefix="endbot-e2e-"))
    instance = None
    try:
        instance = extract(args.archive.resolve(), work / "bundle")
        shim = build_linux_shim(work / "shim") if os.name != "nt" else None
        environment = sanitized_environment(dict(os.environ), shim)
        assert_no_toolchain(environment)
        exercise(instance, environment, args.bot, args.archive.resolve(), args.exercise_update)
    except (ConsumerError, OSError, subprocess.SubprocessError, ValueError, KeyError) as error:
        print(f"e2e-consumer: FAIL: {error}", file=sys.stderr)
        dump_diagnostics(instance)
        return 1
    log("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
