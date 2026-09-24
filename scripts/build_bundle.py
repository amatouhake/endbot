#!/usr/bin/env python3
"""Build the per-platform Endbot operator bundle (docs/OPERATIONS.md section 8).

The bundle lets an operator run Endbot without installing Python or Node.js
and without ever running pip/npm: it carries a private CPython 3.12 (from
``packaging/toolchain.lock.json``) with the patched Endstone wheel, the
``endstone-endbot`` plugin wheel and the ``endbot`` CLI wheel preinstalled, a
private Node.js 24 runtime, and the runtime tree with production
``node_modules`` prebuilt.

This script must run ON the target platform (it executes the bundled
interpreter and node); it refuses otherwise. Toolchain downloads are verified
against ``packaging/toolchain.lock.json``; a mismatch is a hard failure.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = ROOT / "packaging" / "toolchain.lock.json"
ENDSTONE_LOCK_PATH = ROOT / "endstone.lock"

PLATFORMS = ("windows-x86_64", "linux-x86_64")
VERSION_PATTERN = re.compile(r"[0-9A-Za-z][0-9A-Za-z._-]{0,63}\Z")

RUNTIME_FILES = ("package.json", "package-lock.json", "README.md", "endbot-runtime.example.json")
RUNTIME_DIRS = ("scripts", "src")


class BundleError(RuntimeError):
    pass


def check_platform(platform: str) -> None:
    """Refuse to build for any platform other than the host. Runs first."""

    if platform == "windows-x86_64":
        if os.name != "nt":
            raise BundleError(
                "refusing to build platform 'windows-x86_64' on this host "
                f"({sys.platform}): the bundle must be built ON the target platform"
            )
    elif platform == "linux-x86_64":
        if not sys.platform.startswith("linux"):
            raise BundleError(
                "refusing to build platform 'linux-x86_64' on this host "
                f"({sys.platform}): the bundle must be built ON the target platform"
            )
    else:
        raise BundleError(f"unknown platform {platform!r}; expected one of {PLATFORMS}")


def check_version(version: str) -> None:
    if not VERSION_PATTERN.fullmatch(version):
        raise BundleError(f"version {version!r} contains unsupported characters")


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_toolchain_lock() -> dict:
    try:
        return json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise BundleError(f"cannot read toolchain lock {LOCK_PATH}: {error}") from error


def fetch_toolchain(lock: dict, platform: str, download_dir: Path) -> dict[str, Path]:
    """Download (cached) and verify the python and node archives. Returns paths."""

    download_dir.mkdir(parents=True, exist_ok=True)
    fetched: dict[str, Path] = {}
    for component in ("python", "node"):
        try:
            entry = lock[component]["platforms"][platform]
            url, expected = entry["url"], entry["sha256"]
        except KeyError as error:
            raise BundleError(f"toolchain lock is missing {component}/{platform}: {error}") from error
        if not re.fullmatch(r"[0-9a-f]{64}", expected or ""):
            raise BundleError(f"toolchain lock has a malformed sha256 for {component}/{platform}")
        if not (url or "").startswith("https://"):
            raise BundleError(f"toolchain lock has a non-https URL for {component}/{platform}")
        filename = urllib.request.unquote(url.rsplit("/", 1)[-1])
        target = download_dir / filename
        if target.is_file():
            print(f"bundle: reusing cached {filename}")
        else:
            print(f"bundle: downloading {url}")
            try:
                urllib.request.urlretrieve(url, target)
            except OSError as error:
                raise BundleError(f"download failed for {url}: {error}") from error
        found = sha256_of(target)
        if found != expected:
            raise BundleError(
                f"sha256 mismatch for {filename}: expected {expected}, found {found}"
            )
        fetched[component] = target
    return fetched


def run(*args: str, cwd: Path | None = None, capture: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        check=True,
        text=True,
        cwd=str(cwd) if cwd is not None else None,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.STDOUT if capture else None,
    )


def extract_single_top_dir(archive: Path, destination: Path, wanted: str) -> Path:
    """Extract an archive whose members share one top directory into destination/wanted."""

    with tempfile.TemporaryDirectory(prefix="endbot-bundle-extract-") as temporary:
        scratch = Path(temporary)
        if archive.suffix == ".zip":
            with zipfile.ZipFile(archive) as bundle:
                bundle.extractall(scratch)
        else:
            with tarfile.open(archive, "r:*") as bundle:
                bundle.extractall(scratch, filter="data")
        top_dirs = [child for child in sorted(scratch.iterdir()) if child.is_dir()]
        if len(top_dirs) != 1:
            # python-build-standalone uses a single top-level "python" directory.
            names = [child.name for child in scratch.iterdir()]
            raise BundleError(f"{archive.name} has an unexpected layout: {names}")
        source = top_dirs[0]
        destination.mkdir(parents=True, exist_ok=True)
        target = destination / wanted
        if target.exists():
            shutil.rmtree(target)
        shutil.move(str(source), str(target))
        return target


def extract_node(archive: Path, destination: Path) -> Path:
    """Extract the official Node.js archive into destination (renamed to "node")."""

    with tempfile.TemporaryDirectory(prefix="endbot-bundle-node-") as temporary:
        scratch = Path(temporary)
        if archive.suffix == ".zip":
            with zipfile.ZipFile(archive) as bundle:
                bundle.extractall(scratch)
        else:
            with tarfile.open(archive, "r:*") as bundle:
                bundle.extractall(scratch, filter="data")
        top_dirs = [child for child in sorted(scratch.iterdir()) if child.is_dir()]
        if len(top_dirs) != 1:
            raise BundleError(f"{archive.name} has an unexpected layout")
        source = top_dirs[0]
        destination.mkdir(parents=True, exist_ok=True)
        target = destination / "node"
        if target.exists():
            shutil.rmtree(target)
        shutil.move(str(source), str(target))
        return target


def bundled_python(python_dir: Path, platform: str) -> Path:
    candidates = (
        [python_dir / "python.exe"]
        if platform == "windows-x86_64"
        else [python_dir / "bin" / "python3", python_dir / "bin" / "python"]
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise BundleError(f"bundled python interpreter not found under {python_dir}")


def bundled_node(node_dir: Path, platform: str) -> Path:
    candidate = node_dir / "node.exe" if platform == "windows-x86_64" else node_dir / "bin" / "node"
    if not candidate.is_file():
        raise BundleError(f"bundled node binary not found under {node_dir}")
    return candidate


def find_npm_cli(node_dir: Path) -> Path:
    for candidate in (
        node_dir / "node_modules" / "npm" / "bin" / "npm-cli.js",
        node_dir / "lib" / "node_modules" / "npm" / "bin" / "npm-cli.js",
    ):
        if candidate.is_file():
            return candidate
    raise BundleError(f"bundled npm-cli.js not found under {node_dir}")


def npm_supports_allow_remote(node: Path, npm_cli: Path) -> bool:
    completed = run(str(node), str(npm_cli), "ci", "--help", capture=True)
    return "--allow-remote" in completed.stdout


def install_python_wheels(python: Path, endstone_wheel: Path | None, plugin_wheel: Path, cli_wheel: Path, freeze_path: Path) -> None:
    # Install the release wheels themselves with no index so the exact local
    # files win, then resolve their remaining third-party dependencies
    # (the CLI needs cryptography; Endstone has its own) from the index
    # without ever compiling: binary wheels only. The local wheel files are
    # passed again so intra-bundle pins (the plugin's exact endstone pin,
    # which is not on any index) resolve to the bundled wheels.
    wheels = [str(wheel) for wheel in (endstone_wheel, plugin_wheel, cli_wheel) if wheel is not None]
    run(str(python), "-m", "pip", "install", "--no-index", "--no-deps", *wheels)
    if endstone_wheel is not None:
        run(str(python), "-m", "pip", "install", "--only-binary=:all:", *wheels)
    else:
        # Local-validation path only (never CI): without the Endstone wheel
        # the plugin's exact endstone pin cannot resolve, so only the CLI
        # wheel's third-party dependencies are installed here.
        run(str(python), "-m", "pip", "install", "--only-binary=:all:", str(cli_wheel))
    completed = run(str(python), "-m", "pip", "freeze", capture=True)
    freeze_path.write_text(completed.stdout, encoding="utf-8")


def build_runtime(node: Path, npm_cli: Path, runtime_dir: Path) -> None:
    runtime_dir.mkdir(parents=True, exist_ok=True)
    for name in RUNTIME_FILES:
        source = ROOT / "runtime" / name
        if not source.is_file():
            raise BundleError(f"runtime file {source} is missing")
        shutil.copy2(source, runtime_dir / name)
    for name in RUNTIME_DIRS:
        source = ROOT / "runtime" / name
        if not source.is_dir():
            raise BundleError(f"runtime directory {source} is missing")
        shutil.copytree(source, runtime_dir / name)
    command = [str(node), str(npm_cli), "ci", "--omit=dev"]
    if npm_supports_allow_remote(node, npm_cli):
        # The lockfile pins one HTTPS tarball root dependency
        # (prismarine-xbox-services); recent npm builds refuse non-registry
        # remotes unless the root project explicitly allows them.
        command.append("--allow-remote=root")
    run(*command, cwd=runtime_dir)

    lock = json.loads((runtime_dir / "package-lock.json").read_text(encoding="utf-8"))
    for key, entry in (lock.get("packages") or {}).items():
        resolved = (entry or {}).get("resolved") or ""
        if resolved.startswith(("git+", "git://")):
            raise BundleError(f"runtime lock resolves {key} from git: {resolved}")
    wanted = json.loads((runtime_dir / "package.json").read_text(encoding="utf-8"))
    expected_protocol = (wanted.get("dependencies") or {}).get("bedrock-protocol")
    installed_protocol = json.loads(
        (runtime_dir / "node_modules" / "bedrock-protocol" / "package.json").read_text(encoding="utf-8")
    )["version"]
    if installed_protocol != expected_protocol:
        raise BundleError(
            f"installed bedrock-protocol {installed_protocol!r} does not match locked {expected_protocol!r}"
        )
    prune_minecraft_data(node, runtime_dir)


def prune_minecraft_data(node: Path, runtime_dir: Path) -> None:
    """Keep only the minecraft-data files the runtime's Bedrock version resolves.

    minecraft-data ships every Java and Bedrock version (~440 MB). Its data
    getters are lazy, so removing the other version directories is safe as long
    as every path listed for the runtime's game version survives; the bundled
    node then loads each of them to prove it.
    """
    game_version = json.loads((runtime_dir / "endbot-runtime.example.json").read_text(encoding="utf-8"))["gameVersion"]
    data_root = runtime_dir / "node_modules" / "minecraft-data" / "minecraft-data" / "data"
    data_paths = json.loads((data_root / "dataPaths.json").read_text(encoding="utf-8"))
    entry = (data_paths.get("bedrock") or {}).get(game_version)
    if not entry:
        raise BundleError(f"minecraft-data has no Bedrock {game_version} entry")
    keep = {"bedrock/common", "pc/common"} | {str(value) for value in entry.values()}
    removed = 0
    for edition in ("bedrock", "pc"):
        for directory in sorted((data_root / edition).iterdir()):
            if directory.is_dir() and f"{edition}/{directory.name}" not in keep:
                shutil.rmtree(directory)
                removed += 1
    script = (
        "const data = require('minecraft-data')('bedrock_' + process.argv[1]);"
        "if (!data) throw new Error('minecraft-data cannot load ' + process.argv[1]);"
        "for (const key of JSON.parse(process.argv[2])) {"
        "  if (data[key] === undefined) throw new Error('minecraft-data lost ' + key);"
        "}"
        "if (!data.defaultSkin) throw new Error('minecraft-data lost defaultSkin');"
        "require('bedrock-protocol');"
    )
    # These dataPaths entries have no same-named accessor (steve backs
    # defaultSkin, checked explicitly; proto/types are schema sources).
    keys = sorted(key for key in entry if key not in {"proto", "types", "steve", "blocksB2J", "blocksJ2B"})
    run(str(node), "-e", script, game_version, json.dumps(keys), cwd=runtime_dir)
    print(f"bundle: pruned {removed} unused minecraft-data version directories (kept {sorted(keep)})")


WINDOWS_LAUNCHER = """@echo off
rem Endbot operator launcher: runs the bundled private CPython with the endbot CLI.
rem The instance directory is the directory holding this launcher.
set "ENDBOT_INSTANCE=%~dp0"
set /p ENDBOT_VERSION=<"%~dp0app\\current"
"%~dp0app\\%ENDBOT_VERSION%\\python\\python.exe" -m endbot_cli %*
"""

LINUX_LAUNCHER = """#!/bin/sh
# Endbot operator launcher: runs the bundled private CPython with the endbot CLI.
# The instance directory is the directory holding this launcher.
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ENDBOT_INSTANCE="$here"
export ENDBOT_INSTANCE
exec "$here/app/$(cat "$here/app/current")/python/bin/python3" -m endbot_cli "$@"
"""


def write_launchers(bundle_root: Path, platform: str, version: str) -> None:
    (bundle_root / "app" / "current").write_text(version + "\n", encoding="utf-8")
    if platform == "windows-x86_64":
        launcher = bundle_root / "endbot.cmd"
        launcher.write_text(WINDOWS_LAUNCHER, encoding="utf-8", newline="")
    else:
        launcher = bundle_root / "endbot"
        launcher.write_text(LINUX_LAUNCHER, encoding="utf-8", newline="\n")
        launcher.chmod(0o755)


def copy_licenses(bundle_root: Path, python_dir: Path, node_dir: Path, platform: str, endstone_license: Path | None) -> None:
    target = bundle_root / "licenses"
    target.mkdir(parents=True, exist_ok=True)
    for name in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
        source = ROOT / name
        if not source.is_file():
            raise BundleError(f"required license file {source} is missing")
        shutil.copy2(source, target / name)
    if endstone_license is not None:
        if not endstone_license.is_file():
            raise BundleError(f"--endstone-license {endstone_license} does not exist")
        shutil.copy2(endstone_license, target / "ENDSTONE_LICENSE")
    python_license = (
        python_dir / "LICENSE.txt"
        if platform == "windows-x86_64"
        else python_dir / "lib" / "python3.12" / "LICENSE.txt"
    )
    if not python_license.is_file():
        raise BundleError(f"CPython license {python_license} is missing from the toolchain archive")
    shutil.copy2(python_license, target / "CPYTHON_LICENSE.txt")
    node_license = node_dir / "LICENSE"
    if not node_license.is_file():
        raise BundleError(f"Node license {node_license} is missing from the toolchain archive")
    shutil.copy2(node_license, target / "NODE_LICENSE")


def archive_bundle(bundle_root: Path, platform: str, version: str, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    if platform == "windows-x86_64":
        archive = output_dir / f"endbot-{version}-{platform}.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
            for directory, _subdirs, files in os.walk(bundle_root):
                for name in sorted(files):
                    full = Path(directory) / name
                    bundle.write(full, full.relative_to(bundle_root.parent).as_posix())
    else:
        archive = output_dir / f"endbot-{version}-{platform}.tar.gz"
        members: list[Path] = []
        for directory, _subdirs, files in os.walk(bundle_root):
            for name in sorted(files):
                members.append(Path(directory) / name)
        members.sort(key=lambda path: path.relative_to(bundle_root.parent).as_posix())
        with open(archive, "wb") as raw, tempfile.TemporaryFile(prefix="endbot-bundle-gzip-") as plain:
            with tarfile.open(fileobj=plain, mode="w") as bundle:
                for full in members:
                    info = bundle.gettarinfo(str(full), full.relative_to(bundle_root.parent).as_posix())
                    info.uid = info.gid = 0
                    info.uname = info.gname = ""
                    with full.open("rb") as handle:
                        bundle.addfile(info, handle)
            plain.seek(0)
            import gzip

            with gzip.GzipFile(filename="", mode="wb", compresslevel=9, mtime=0, fileobj=raw) as compressed:
                shutil.copyfileobj(plain, compressed)
    digest = sha256_of(archive)
    print(f"bundle: {archive} sha256={digest}")
    return archive


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True, help="Endbot version stamped into app/<version> and app/current")
    parser.add_argument("--platform", required=True, choices=PLATFORMS)
    parser.add_argument("--endstone-wheel", type=Path, help="patched Endstone wheel for the target platform")
    parser.add_argument("--plugin-wheel", type=Path, required=True, help="endstone-endbot plugin wheel (pure Python)")
    parser.add_argument("--cli-wheel", type=Path, required=True, help="endbot CLI wheel (pure Python)")
    parser.add_argument("--endstone-license", type=Path, default=None, help="ENDSTONE_LICENSE file for licenses/")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--download-dir",
        type=Path,
        default=ROOT / "build" / "bundle-downloads",
        help="cache directory for toolchain downloads (default: build/bundle-downloads)",
    )
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=ROOT / "build" / "bundle-work",
        help="staging directory (default: build/bundle-work)",
    )
    parser.add_argument(
        "--allow-missing-endstone",
        action="store_true",
        help="local validation only: build without the Endstone wheel (never used in CI)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        check_platform(args.platform)
        check_version(args.version)
        endstone_wheel = args.endstone_wheel.resolve() if args.endstone_wheel is not None else None
        if endstone_wheel is not None:
            if not endstone_wheel.is_file():
                raise BundleError(f"--endstone-wheel {args.endstone_wheel} does not exist")
        elif not args.allow_missing_endstone:
            raise BundleError("an --endstone-wheel is required (or pass --allow-missing-endstone for local validation)")
        plugin_wheel = args.plugin_wheel.resolve()
        cli_wheel = args.cli_wheel.resolve()
        for label, wheel in (("plugin", plugin_wheel), ("cli", cli_wheel)):
            if not wheel.is_file():
                raise BundleError(f"--{label}-wheel {wheel} does not exist")

        lock = load_toolchain_lock()
        fetched = fetch_toolchain(lock, args.platform, args.download_dir.resolve())

        work = args.work_dir.resolve()
        staging = work / "stage"
        if staging.exists():
            shutil.rmtree(staging)
        bundle_root = staging / f"endbot-{args.version}"
        app_version = bundle_root / "app" / args.version

        python_dir = extract_single_top_dir(fetched["python"], app_version, "python")
        node_dir = extract_node(fetched["node"], app_version)
        python = bundled_python(python_dir, args.platform)
        node = bundled_node(node_dir, args.platform)

        install_python_wheels(python, endstone_wheel, plugin_wheel, cli_wheel, app_version / "python-freeze.txt")
        build_runtime(node, find_npm_cli(node_dir), app_version / "runtime")
        write_launchers(bundle_root, args.platform, args.version)
        copy_licenses(bundle_root, python_dir, node_dir, args.platform, args.endstone_license)

        test_script = ROOT / "scripts" / "test_bundle.py"
        test_command = [sys.executable, str(test_script), "--bundle-root", str(bundle_root)]
        if args.allow_missing_endstone:
            test_command.append("--allow-missing-endstone")
        run(*test_command)

        archive_bundle(bundle_root, args.platform, args.version, args.output_dir.resolve())
    except (BundleError, subprocess.CalledProcessError, OSError) as error:
        import traceback

        traceback.print_exc()
        print(f"build-bundle: ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
