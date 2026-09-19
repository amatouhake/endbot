#!/usr/bin/env python3
"""Report which native Windows build prerequisites for patched Endstone are missing.

The pinned Endstone Conan profile compiles with clang-cl and links with lld-link
(LLVM 18+) on Windows, builds sentry-native with MSVC cl, and generates Ninja.
This script only inspects the machine; it never installs anything.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

VS_LLVM_COMPONENT = "Microsoft.VisualStudio.Component.VC.Llvm.Clang"
VS_LLVM_TOOLSET_COMPONENT = "Microsoft.VisualStudio.Component.VC.Llvm.ClangToolset"
MINIMUM_LLVM = 18
DEVELOPER_PROMPT = "run from a Visual Studio 2022 'x64 Native Tools' developer prompt"


@dataclass(frozen=True)
class Requirement:
    name: str
    executable: str
    remedy: str
    version_arguments: tuple[str, ...] = ("--version",)
    minimum: tuple[int, ...] | None = None


REQUIREMENTS = (
    Requirement("Python 3.10+", "python", "install CPython 3.10+ from python.org", minimum=(3, 10)),
    Requirement("Git", "git", "install Git for Windows"),
    Requirement("Node.js 22+", "node", "install Node.js 22+", minimum=(22,)),
    Requirement("npm", "npm", "npm ships with Node.js"),
    Requirement("CMake 3.29+", "cmake", "pip install cmake in the project .venv, or install CMake", minimum=(3, 29)),
    Requirement("Ninja", "ninja", "pip install ninja in the project .venv"),
    Requirement("Conan 2+", "conan", "pip install conan in the project .venv", minimum=(2,)),
    Requirement(
        "MSVC cl (Visual Studio 2022 C++ build tools)",
        "cl",
        "install the 'Desktop development with C++' workload of Visual Studio 2022",
        version_arguments=(),
    ),
    Requirement(
        f"clang-cl (LLVM {MINIMUM_LLVM}+)",
        "clang-cl",
        f"install Visual Studio 2022 component {VS_LLVM_COMPONENT} ('C++ Clang Compiler for Windows'); "
        f"{VS_LLVM_TOOLSET_COMPONENT} is optional",
        minimum=(MINIMUM_LLVM,),
    ),
    Requirement(
        f"lld-link (LLVM {MINIMUM_LLVM}+)",
        "lld-link",
        f"installed together with {VS_LLVM_COMPONENT}",
        minimum=(MINIMUM_LLVM,),
    ),
)

# Tools that Visual Studio installs but only puts on PATH inside a developer prompt.
VISUAL_STUDIO_TOOLS = {
    "cl": "VC/Tools/MSVC/*/bin/Hostx64/x64/cl.exe",
    "clang-cl": "VC/Tools/Llvm/x64/bin/clang-cl.exe",
    "lld-link": "VC/Tools/Llvm/x64/bin/lld-link.exe",
}


def version_of(executable: str, arguments: tuple[str, ...]) -> str:
    try:
        completed = subprocess.run(
            (executable, *arguments), check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    match = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", completed.stdout)
    return match.group(0) if match else ""


def parse_version(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.split(".") if part.isdigit())


def visual_studio_installations(*required_components: str) -> list[str]:
    """Return Visual Studio installations, optionally filtered by installed component IDs."""
    if platform.system() != "Windows":
        return []
    program_files = os.environ.get("ProgramFiles(x86)") or os.environ.get("ProgramFiles")
    if not program_files:
        return []
    vswhere = Path(program_files) / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
    if not vswhere.is_file():
        return []
    arguments = [str(vswhere), "-products", "*", "-property", "installationPath"]
    if required_components:
        arguments[3:3] = ["-requires", *required_components]
    completed = subprocess.run(arguments, check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    return [line.strip() for line in completed.stdout.splitlines() if line.strip()]


def locate_in_visual_studio(executable: str) -> str | None:
    pattern = VISUAL_STUDIO_TOOLS.get(executable)
    if pattern is None:
        return None
    for installation in visual_studio_installations():
        matches = sorted(Path(installation).glob(pattern))
        if matches:
            return str(matches[-1])
    return None


def inspect(
    which: Callable[[str], str | None] = shutil.which,
    fallback: Callable[[str], str | None] = locate_in_visual_studio,
    version: Callable[[str, tuple[str, ...]], str] = version_of,
) -> list[dict[str, str | None]]:
    results = []
    for requirement in REQUIREMENTS:
        path = which(requirement.executable)
        status = "ok"
        if path is None:
            path = fallback(requirement.executable)
            status = "not-on-PATH" if path else "missing"
        found = version(path, requirement.version_arguments) if path and requirement.version_arguments else ""
        if path and requirement.minimum and parse_version(found) < requirement.minimum:
            status = "too-old"
        remedy = DEVELOPER_PROMPT if status == "not-on-PATH" else requirement.remedy
        results.append(
            {
                "name": requirement.name,
                "executable": requirement.executable,
                "path": path,
                "version": found,
                "status": status,
                "remedy": remedy if status != "ok" else "",
            }
        )
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="print machine-readable results")
    args = parser.parse_args()

    results = inspect()
    llvm_installations = visual_studio_installations(VS_LLVM_COMPONENT)
    if args.json:
        print(json.dumps({"requirements": results, "vs_llvm_installations": llvm_installations}, indent=2))
    else:
        for result in results:
            detail = result["path"] or "not found"
            if result["version"]:
                detail = f"{result['version']} ({result['path']})"
            print(f"[{result['status']:>11}] {result['name']}: {detail}")
            if result["remedy"]:
                print(f"              remedy: {result['remedy']}")
        if platform.system() == "Windows":
            if llvm_installations:
                print(f"[         ok] Visual Studio component {VS_LLVM_COMPONENT}: {', '.join(llvm_installations)}")
            else:
                print(f"[    missing] Visual Studio component {VS_LLVM_COMPONENT} in every Visual Studio installation")
    return 1 if any(result["status"] != "ok" for result in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
