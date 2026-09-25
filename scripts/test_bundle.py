#!/usr/bin/env python3
"""Self-test for the Endbot operator bundle.

Runs against the builder staging tree (before archiving) and, identically,
against an extracted release archive, so CI can call it in both places::

    python scripts/test_bundle.py --bundle-root <dir> [--version V] [--allow-missing-endstone]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class BundleTestError(RuntimeError):
    pass


def run(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, check=True, text=True, cwd=str(cwd) if cwd else None,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


def launcher_path(bundle_root: Path) -> Path:
    if os.name == "nt":
        launcher = bundle_root / "endbot.cmd"
    else:
        launcher = bundle_root / "endbot"
    if not launcher.is_file():
        raise BundleTestError(f"launcher {launcher} is missing")
    return launcher


def bundled_python(bundle_root: Path, version: str) -> Path:
    candidates = (
        [bundle_root / "app" / version / "python" / "python.exe"]
        if os.name == "nt"
        else [bundle_root / "app" / version / "python" / "bin" / "python3"]
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise BundleTestError(f"bundled python not found under {bundle_root / 'app' / version / 'python'}")


def bundled_node(bundle_root: Path, version: str) -> Path:
    candidate = (
        bundle_root / "app" / version / "node" / "node.exe"
        if os.name == "nt"
        else bundle_root / "app" / version / "node" / "bin" / "node"
    )
    if not candidate.is_file():
        raise BundleTestError(f"bundled node not found under {bundle_root / 'app' / version / 'node'}")
    return candidate


def check_launcher_help(launcher: Path) -> None:
    if os.name == "nt":
        completed = run("cmd", "/c", str(launcher), "--help")
    else:
        completed = run(str(launcher), "--help")
    if "usage:" not in completed.stdout.lower():
        raise BundleTestError(f"launcher --help printed unexpected output: {completed.stdout[:500]!r}")
    print("bundle-test: launcher --help works")


def check_doctor_empty_instance(launcher: Path) -> None:
    # An empty instance directory must report FAIL for the missing config,
    # not crash: exit 1 with a FAIL line and no traceback.
    with tempfile.TemporaryDirectory(prefix="endbot-bundle-doctor-") as temporary:
        if os.name == "nt":
            completed = subprocess.run(
                ["cmd", "/c", str(launcher), "--instance", temporary, "doctor"],
                text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
            )
        else:
            completed = subprocess.run(
                [str(launcher), "--instance", temporary, "doctor"],
                text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
            )
    if completed.returncode != 1:
        raise BundleTestError(
            f"doctor on an empty instance exited {completed.returncode}, expected 1: {completed.stdout[:1000]!r}"
        )
    if "FAIL" not in completed.stdout or "config" not in completed.stdout:
        raise BundleTestError(f"doctor on an empty instance did not report a config FAIL: {completed.stdout[:1000]!r}")
    if "Traceback" in completed.stdout:
        raise BundleTestError(f"doctor on an empty instance crashed: {completed.stdout[:1000]!r}")
    print("bundle-test: doctor on an empty instance reports FAIL for missing config")


def check_python_imports(python: Path, allow_missing_endstone: bool) -> None:
    lock = json.loads((ROOT / "endstone.lock").read_text(encoding="utf-8"))
    expected = lock["endstone"]["package_version"]
    if allow_missing_endstone:
        script = "import endbot_cli, cryptography, tomlkit; print('cli-ok')"
    else:
        script = (
            "import endstone, endstone_endbot, endbot_cli, cryptography, tomlkit; "
            "import importlib.metadata; "
            f"assert importlib.metadata.version('endstone') == {expected!r}, importlib.metadata.version('endstone'); "
            "print('imports-ok')"
        )
    completed = run(str(python), "-c", script)
    if "ok" not in completed.stdout:
        raise BundleTestError(f"bundled python import check failed: {completed.stdout[:1000]!r}")
    if not allow_missing_endstone:
        script = (
            "import importlib.metadata; "
            f"assert importlib.metadata.version('endstone') == {expected!r}; "
            "print(importlib.metadata.version('endstone'))"
        )
        completed = run(str(python), "-c", script)
        if expected not in completed.stdout:
            raise BundleTestError("bundled endstone version does not match endstone.lock")
    print(f"bundle-test: bundled python imports work (endstone {'skipped' if allow_missing_endstone else expected})")


def check_node_syntax(node: Path, runtime_dir: Path) -> None:
    script = runtime_dir / "scripts" / "check-syntax.js"
    if not script.is_file():
        raise BundleTestError(f"{script} is missing from the bundled runtime")
    run(str(node), str(script))
    print("bundle-test: bundled node runs runtime check-syntax.js")


def check_node_runtime_imports(node: Path, runtime_dir: Path) -> None:
    # The NetherNet path must resolve without the pruned modules: requiring
    # bedrock-protocol/nethernet and importing the runtime entry modules
    # (the cli.js closure, minus cli.js itself which would start the runtime)
    # must never touch typescript or raknet-node, which are asserted absent
    # below. raknet-native is deliberately kept (bedrock-protocol ping()
    # requires it even for NetherNet discovery fallback) and must be present.
    script = (
        "const { createRequire } = require('node:module');"
        "const bundleRequire = createRequire(process.argv[1]);"
        "bundleRequire('bedrock-protocol');"
        "bundleRequire('nethernet');"
        "const sep = require('node:path').sep;"
        "const cache = Object.keys(bundleRequire.cache).filter(k =>"
        "  k.split(sep).includes('typescript') ||"
        "  k.split(sep).includes('raknet-node'));"
        "if (cache.length) throw new Error('pruned modules loaded: ' + cache);"
        "console.log('node-runtime-ok');"
    )
    completed = run(str(node), "-e", script, str(runtime_dir / "package.json"), cwd=runtime_dir)
    if "node-runtime-ok" not in completed.stdout:
        raise BundleTestError(f"bundled node runtime import check failed: {completed.stdout[:1000]!r}")
    print("bundle-test: bundled node loads bedrock-protocol without pruned modules")


def check_tree(bundle_root: Path, version: str) -> None:
    if (bundle_root / "app" / "current").read_text(encoding="utf-8").strip() != version:
        raise BundleTestError("app/current does not contain the bundle version")
    freeze = bundle_root / "app" / version / "python-freeze.txt"
    if not freeze.is_file() or not freeze.read_text(encoding="utf-8").strip():
        raise BundleTestError("app/<version>/python-freeze.txt is missing or empty")
    for name in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
        if not (bundle_root / "licenses" / name).is_file():
            raise BundleTestError(f"licenses/{name} is missing")
    wanted = json.loads((bundle_root / "app" / version / "runtime" / "package.json").read_text(encoding="utf-8"))
    expected_protocol = (wanted.get("dependencies") or {}).get("bedrock-protocol")
    installed = json.loads(
        (bundle_root / "app" / version / "runtime" / "node_modules" / "bedrock-protocol" / "package.json")
        .read_text(encoding="utf-8")
    )["version"]
    if installed != expected_protocol:
        raise BundleTestError(f"bundled bedrock-protocol {installed!r} != locked {expected_protocol!r}")
    print("bundle-test: staging tree layout, freeze, licenses, and runtime pin look right")


def check_pruned(bundle_root: Path, version: str) -> None:
    # The slim-bundle prune step (scripts/build_bundle.py prune_bundle) must
    # have removed build-time-only files; the kept runtime must still be there.
    missing: list[str] = []

    def absent(path: Path) -> None:
        if path.exists() or path.is_symlink():
            missing.append(f"should have been pruned: {path}")

    def present(path: Path) -> None:
        if not path.exists():
            missing.append(f"should have been kept: {path}")

    app = bundle_root / "app" / version
    if os.name == "nt":
        node_dir = app / "node"
        absent(node_dir / "node_modules")
        for name in ("npm", "npm.cmd", "npm.ps1"):
            absent(node_dir / name)
        for pattern in ("npx*", "corepack*"):
            for leftover in sorted(node_dir.glob(pattern)):
                missing.append(f"should have been pruned: {leftover}")
        present(node_dir / "node.exe")
        present(node_dir / "LICENSE")
        site_packages = app / "python" / "Lib" / "site-packages"
        stdlib = app / "python" / "Lib"
        absent(app / "python" / "include")
        absent(app / "python" / "libs")
        absent(app / "python" / "tcl")
        dlls = app / "python" / "DLLs"
        for pattern in ("_tkinter*", "tcl*.dll", "tk*.dll"):
            for leftover in sorted(dlls.glob(pattern)):
                missing.append(f"should have been pruned: {leftover}")
    else:
        node_dir = app / "node"
        absent(node_dir / "lib" / "node_modules")
        for name in ("npm", "npx", "corepack"):
            absent(node_dir / "bin" / name)
        absent(node_dir / "include")
        absent(node_dir / "share" / "doc")
        present(node_dir / "bin" / "node")
        present(node_dir / "LICENSE")
        stdlib = app / "python" / "lib" / "python3.12"
        site_packages = stdlib / "site-packages"
        absent(app / "python" / "include")
        lib = app / "python" / "lib"
        for pattern in ("tcl*", "tk*", "itcl*", "libtcl*", "libtk*"):
            for leftover in sorted(lib.glob(pattern)):
                missing.append(f"should have been pruned: {leftover}")
        for leftover in sorted((stdlib / "lib-dynload").glob("_tkinter*")):
            missing.append(f"should have been pruned: {leftover}")
    if not site_packages.is_dir():
        missing.append(f"should have been kept: {site_packages}")
    else:
        absent(site_packages / "pip")
        for leftover in sorted(site_packages.glob("pip-*.dist-info")):
            missing.append(f"should have been pruned: {leftover}")
    for name in ("ensurepip", "tkinter", "idlelib", "turtledemo"):
        absent(stdlib / name)
    runtime_modules = app / "runtime" / "node_modules"
    absent(runtime_modules / "typescript")
    absent(runtime_modules / "raknet-node")
    for shim in ("tsc", "tsc.cmd", "tsc.ps1", "tsserver", "tsserver.cmd", "tsserver.ps1"):
        absent(runtime_modules / ".bin" / shim)
    for path in bundle_root.rglob("*"):
        if path.is_symlink() and not path.exists():
            missing.append(f"dangling symlink: {path} -> {os.readlink(path)}")
    present(runtime_modules / "raknet-native")
    present(runtime_modules / "bedrock-protocol")
    present(runtime_modules / "nethernet")
    present(runtime_modules / "jsp-raknet")
    if missing:
        raise BundleTestError("prune check failed:\n" + "\n".join(missing))
    print("bundle-test: pruned files are absent and the kept runtime is present")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-root", type=Path, required=True,
                        help="staging tree or extracted archive root (the endbot-<version>/ directory)")
    parser.add_argument("--version", default=None, help="bundle version (default: read from app/current)")
    parser.add_argument("--allow-missing-endstone", action="store_true",
                        help="skip Endstone import checks (local validation without the wheel)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        bundle_root = args.bundle_root.resolve()
        version = args.version or (bundle_root / "app" / "current").read_text(encoding="utf-8").strip()
        if not version:
            raise BundleTestError("bundle version is empty")
        check_tree(bundle_root, version)
        check_pruned(bundle_root, version)
        check_launcher_help(launcher_path(bundle_root))
        check_doctor_empty_instance(launcher_path(bundle_root))
        check_python_imports(bundled_python(bundle_root, version), args.allow_missing_endstone)
        check_node_syntax(bundled_node(bundle_root, version), bundle_root / "app" / version / "runtime")
        check_node_runtime_imports(bundled_node(bundle_root, version), bundle_root / "app" / version / "runtime")
        print(f"bundle-test: {bundle_root.name} passed")
    except (BundleTestError, subprocess.CalledProcessError, OSError) as error:
        print(f"bundle-test: ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
