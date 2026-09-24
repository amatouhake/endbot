"""Bundle archives shaped exactly like the release pipeline's (scripts/build_bundle.py).

The real bundles hold one top-level ``endbot-<version>/`` directory; the Linux
tar.gz also carries symlinks (python-build-standalone ``bin/python3``, Node's
``bin/npm``) and executable bits that must survive an update.
"""

from __future__ import annotations

import io
import os
import stat
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

from endbot_cli.updatecmd import UpdateError, extract_bundle, inspect_bundle, read_members

VERSION = "0.1.0-rc.3"
ROOT = f"endbot-{VERSION}"


def windows_zip(path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as bundle:
        bundle.writestr(f"{ROOT}/app/{VERSION}/python/python.exe", b"python")
        bundle.writestr(f"{ROOT}/app/{VERSION}/node/node.exe", b"node")
        bundle.writestr(f"{ROOT}/app/current", f"{VERSION}\n")
        bundle.writestr(f"{ROOT}/endbot.cmd", b"@echo off\r\n")
        bundle.writestr(f"{ROOT}/licenses/LICENSE", b"license\n")
    return path


def _add(bundle: tarfile.TarFile, name: str, data: bytes = b"", mode: int = 0o644) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mode = mode
    bundle.addfile(info, io.BytesIO(data))


def _link(bundle: tarfile.TarFile, name: str, target: str) -> None:
    info = tarfile.TarInfo(name)
    info.type = tarfile.SYMTYPE
    info.linkname = target
    bundle.addfile(info)


def linux_tar(path: Path, *, extra_link: tuple[str, str] | None = None) -> Path:
    with tarfile.open(path, "w:gz") as bundle:
        _add(bundle, f"{ROOT}/app/{VERSION}/python/bin/python3.12", b"python", 0o755)
        _link(bundle, f"{ROOT}/app/{VERSION}/python/bin/python3", "python3.12")
        _add(bundle, f"{ROOT}/app/{VERSION}/node/lib/node_modules/npm/bin/npm-cli.js", b"npm", 0o755)
        _link(bundle, f"{ROOT}/app/{VERSION}/node/bin/npm", "../lib/node_modules/npm/bin/npm-cli.js")
        _add(bundle, f"{ROOT}/app/current", f"{VERSION}\n".encode())
        _add(bundle, f"{ROOT}/endbot", b"#!/bin/sh\n", 0o755)
        if extra_link is not None:
            _link(bundle, f"{ROOT}/{extra_link[0]}", extra_link[1])
    return path


class ReleaseLayoutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def test_top_level_directory_is_stripped_for_zip_bundles(self) -> None:
        archive = windows_zip(self.root / f"{ROOT}-windows-x86_64.zip")
        members = read_members(archive)
        info = inspect_bundle(members, archive)
        self.assertEqual(info.version, VERSION)
        self.assertEqual(info.launchers, ("endbot.cmd",))
        target, launchers = self.root / "app" / VERSION, self.root / "launchers"
        extract_bundle(archive, members, info, app_target=target, launcher_dir=launchers)
        self.assertEqual((target / "python" / "python.exe").read_bytes(), b"python")
        self.assertTrue((launchers / "endbot.cmd").is_file())

    def test_linux_bundle_symlinks_and_modes_are_accepted(self) -> None:
        archive = linux_tar(self.root / f"{ROOT}-linux-x86_64.tar.gz")
        members = read_members(archive)
        info = inspect_bundle(members, archive)
        self.assertEqual(info.launchers, ("endbot",))
        links = {member.parts: member.link for member in members if member.link}
        self.assertEqual(links[("app", VERSION, "python", "bin", "python3")], "python3.12")

    @unittest.skipIf(os.name == "nt", "symlinks and POSIX modes are recreated on Linux only")
    def test_linux_extraction_keeps_symlinks_and_executable_bits(self) -> None:
        archive = linux_tar(self.root / f"{ROOT}-linux-x86_64.tar.gz")
        members = read_members(archive)
        info = inspect_bundle(members, archive)
        target, launchers = self.root / "app" / VERSION, self.root / "launchers"
        extract_bundle(archive, members, info, app_target=target, launcher_dir=launchers)
        python3 = target / "python" / "bin" / "python3"
        self.assertTrue(python3.is_symlink())
        self.assertEqual(os.readlink(python3), "python3.12")
        self.assertEqual(python3.read_bytes(), b"python")
        self.assertTrue((target / "node" / "bin" / "npm").is_symlink())
        self.assertTrue(os.stat(target / "python" / "bin" / "python3.12").st_mode & stat.S_IXUSR)
        self.assertTrue(os.stat(launchers / "endbot").st_mode & stat.S_IXUSR)

    def test_symlinks_leaving_the_application_tree_are_refused(self) -> None:
        for name, target in (
            (f"app/{VERSION}/python/bin/escape", "../../../../state/secrets/owner-private.pem"),
            (f"app/{VERSION}/python/bin/absolute", "/etc/passwd"),
            ("endbot-link", "app/current"),
        ):
            with self.subTest(name=name):
                archive = linux_tar(self.root / f"bad-{name.replace('/', '_')}.tar.gz", extra_link=(name, target))
                with self.assertRaises(UpdateError):
                    read_members(archive)

    def test_hard_links_are_refused(self) -> None:
        archive = self.root / "hardlink.tar.gz"
        with tarfile.open(archive, "w:gz") as bundle:
            _add(bundle, f"{ROOT}/app/{VERSION}/python/bin/python3.12", b"python", 0o755)
            info = tarfile.TarInfo(f"{ROOT}/app/{VERSION}/python/bin/python3")
            info.type = tarfile.LNKTYPE
            info.linkname = f"{ROOT}/app/{VERSION}/python/bin/python3.12"
            bundle.addfile(info)
        with self.assertRaises(UpdateError):
            read_members(archive)


if __name__ == "__main__":
    unittest.main()
