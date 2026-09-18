#!/usr/bin/env python3
"""Prepare a disposable, exactly pinned Endstone checkout with Endbot patches."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = ROOT / "endstone.lock"
SERIES_PATH = ROOT / "patches" / "endstone" / "series"


class PreparationError(RuntimeError):
    pass


def run(*args: str, cwd: Path | None = None, capture: bool = False, env: dict[str, str] | None = None) -> str:
    completed = subprocess.run(
        args,
        cwd=cwd,
        check=True,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        env=env,
    )
    return completed.stdout.strip() if capture else ""


def load_lock() -> dict:
    lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    endstone = lock.get("endstone", {})
    required = ("upstream_url", "version", "tag", "commit", "package_version")
    missing = [key for key in required if not endstone.get(key)]
    if missing:
        raise PreparationError(f"endstone.lock is missing: {', '.join(missing)}")
    commit = endstone["commit"]
    if len(commit) != 40 or any(character not in "0123456789abcdef" for character in commit):
        raise PreparationError("endstone.lock contains an invalid commit SHA")
    package_version = endstone["package_version"]
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+){2}\+endbot\.[0-9]+", package_version):
        raise PreparationError("endstone.lock contains an invalid Endbot-local package version")
    return lock


def load_series() -> list[Path]:
    names = [line.strip() for line in SERIES_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not names:
        raise PreparationError("Endstone patch series is empty")
    patches: list[Path] = []
    for name in names:
        if Path(name).name != name or not name.endswith(".patch"):
            raise PreparationError(f"unsafe patch-series entry: {name!r}")
        patch = SERIES_PATH.parent / name
        if not patch.is_file():
            raise PreparationError(f"patch-series entry does not exist: {name}")
        patches.append(patch)
    unlisted = sorted(path.name for path in SERIES_PATH.parent.glob("*.patch") if path not in patches)
    if unlisted:
        raise PreparationError(f"unlisted Endstone patches: {', '.join(unlisted)}")
    return patches


def patch_revision(patches: list[Path]) -> str:
    digest = hashlib.sha256()
    for patch in patches:
        digest.update(patch.read_bytes())
    return digest.hexdigest()


def ensure_cache(cache: Path, upstream_url: str, tag: str, commit: str, offline: bool) -> None:
    partial_cache = False
    if cache.exists():
        try:
            is_bare = run("git", "rev-parse", "--is-bare-repository", cwd=cache, capture=True)
        except subprocess.CalledProcessError as error:
            raise PreparationError(f"cache is not a git repository: {cache}") from error
        if is_bare != "true":
            raise PreparationError(f"cache must be a bare git repository: {cache}")
        promisor = subprocess.run(
            ("git", "config", "--get", "remote.origin.promisor"),
            cwd=cache,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
        )
        partial_cache = promisor.stdout.strip() == "true"
        if partial_cache and offline:
            raise PreparationError("offline mode requires a complete cache; this cache was created with a blob filter")
    elif offline:
        raise PreparationError(f"offline cache does not exist: {cache}")
    else:
        cache.parent.mkdir(parents=True, exist_ok=True)
        run("git", "clone", "--bare", upstream_url, str(cache))

    if not offline:
        if partial_cache:
            run("git", "remote", "set-url", "origin", upstream_url, cwd=cache)
            run(
                "git",
                "fetch",
                "--force",
                "--refetch",
                "--no-filter",
                "origin",
                "+refs/heads/*:refs/heads/*",
                "+refs/tags/*:refs/tags/*",
                cwd=cache,
            )
            for key in ("remote.origin.promisor", "remote.origin.partialclonefilter", "extensions.partialclone"):
                subprocess.run(("git", "config", "--unset-all", key), cwd=cache, check=False)
            run("git", "fsck", "--full", cwd=cache)
        else:
            run("git", "remote", "set-url", "origin", upstream_url, cwd=cache)
            run("git", "fetch", "--force", "origin", f"refs/tags/{tag}:refs/tags/{tag}", cwd=cache)

    try:
        resolved_tag = run("git", "rev-parse", f"refs/tags/{tag}^{{commit}}", cwd=cache, capture=True)
        run("git", "cat-file", "-e", f"{commit}^{{commit}}", cwd=cache)
    except subprocess.CalledProcessError as error:
        raise PreparationError(f"pinned Endstone revision is unavailable in cache: {commit}") from error
    if resolved_tag != commit:
        raise PreparationError(f"tag {tag} resolved to {resolved_tag}, expected {commit}")


def prepare(cache: Path, output: Path, offline: bool) -> dict[str, str | int]:
    lock = load_lock()
    patches = load_series()
    endstone = lock["endstone"]

    if output.exists():
        raise PreparationError(f"output already exists; choose or remove the disposable directory: {output}")

    ensure_cache(cache, endstone["upstream_url"], endstone["tag"], endstone["commit"], offline)
    output.parent.mkdir(parents=True, exist_ok=True)
    run("git", "clone", "--no-checkout", str(cache), str(output))
    run("git", "remote", "set-url", "origin", endstone["upstream_url"], cwd=output)
    run("git", "checkout", "--detach", endstone["commit"], cwd=output)
    run("git", "config", "user.name", "Endbot Patch Builder", cwd=output)
    run("git", "config", "user.email", "endbot-patches@users.noreply.github.com", cwd=output)

    environment = os.environ.copy()
    environment.update(
        {
            "GIT_COMMITTER_NAME": "Endbot Patch Builder",
            "GIT_COMMITTER_EMAIL": "endbot-patches@users.noreply.github.com",
        }
    )
    try:
        run(
            "git",
            "am",
            "--committer-date-is-author-date",
            "--no-gpg-sign",
            *(str(patch) for patch in patches),
            cwd=output,
            env=environment,
        )
    except subprocess.CalledProcessError as error:
        subprocess.run(("git", "am", "--abort"), cwd=output, check=False)
        raise PreparationError("Endbot patch series did not apply cleanly") from error

    # setuptools_scm reads this exact local tag when building the wheel. Keeping
    # it in the lock decouples the package contract from the number of patches.
    package_tag = f"v{endstone['package_version']}"
    run("git", "tag", package_tag, cwd=output)

    status = run("git", "status", "--porcelain", cwd=output, capture=True)
    if status:
        raise PreparationError(f"prepared checkout is unexpectedly dirty:\n{status}")

    return {
        "upstream_commit": endstone["commit"],
        "patched_commit": run("git", "rev-parse", "HEAD", cwd=output, capture=True),
        "patch_revision": patch_revision(patches),
        "patch_count": len(patches),
        "package_version": endstone["package_version"],
        "output": str(output),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, default=ROOT / ".cache" / "endstone.git")
    parser.add_argument("--output", type=Path, default=ROOT / "build" / "endstone-patched")
    parser.add_argument("--offline", action="store_true", help="do not fetch; require the pinned tag in the cache")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = prepare(args.cache.resolve(), args.output.resolve(), args.offline)
    except (PreparationError, OSError, json.JSONDecodeError, subprocess.CalledProcessError) as error:
        print(f"prepare-endstone: ERROR: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
