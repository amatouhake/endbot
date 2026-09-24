"""Tests for `endbot update` (extract, doctor gate, switch, rollback) — section 6a.

Synthetic bundle archives and fake new-version doctor/acquisition hooks only:
no network, no real BDS, no real bundled Python.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace

from fakeinstance import build_bds_dir, simulate_bds_download

from endbot_cli.instance import InstancePaths
from endbot_cli.lock import load_lock
from endbot_cli.updatecmd import run_doctor_subprocess, run_rollback, run_update


def make_bundle_zip(path: Path, version: str, *, launcher: str | None = "endbot.cmd", with_python: bool = True) -> Path:
    with zipfile.ZipFile(path, "w") as bundle:
        if with_python:
            bundle.writestr(f"app/{version}/python/python.exe", b"fake python")
        bundle.writestr(f"app/{version}/node/node.exe", b"fake node")
        bundle.writestr(f"app/{version}/runtime/src/cli.js", b"// runtime\n")
        if launcher is not None:
            bundle.writestr(launcher, b"@echo off\r\nrem launcher v2\r\n")
        bundle.writestr("LICENSE", b"license text\n")
    return path


def make_sums(archive: Path, digest: str | None = None, name: str | None = None) -> Path:
    sums = archive.parent / "SHA256SUMS"
    actual = digest or hashlib.sha256(archive.read_bytes()).hexdigest()
    sums.write_text(f"{actual}  {name or archive.name}\n", encoding="utf-8")
    return sums


class FakeDoctor:
    """Queued fake for the new version's doctor; records (python, instance) calls."""

    def __init__(self, *responses: tuple[int, list[str]]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[Path, Path]] = []

    def __call__(self, python: Path, instance_root: Path) -> tuple[int, list[str]]:
        self.calls.append((Path(python), Path(instance_root)))
        if not self.responses:
            return 0, ["PASS config: ok"]
        return self.responses.pop(0)


class RecordingAcquire:
    def __init__(self) -> None:
        self.calls: list[tuple[Path, Path]] = []

    def __call__(self, python: Path, server: Path) -> None:
        self.calls.append((Path(python), Path(server)))
        simulate_bds_download(Path(server))


class UpdateTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.paths = InstancePaths.for_root(self.root)
        self.lock = load_lock()
        build_bds_dir(self.root / "server", version="26.51", worlds=True)
        self.paths.endbot_toml.parent.mkdir(exist_ok=True)
        self.paths.endbot_toml.write_text(
            "[server]\npath = 'server'\n[controllers]\ngamertags = []\n[runtime]\ncontrol-port = 19142\n",
            encoding="utf-8",
        )
        (self.root / "app" / "0.1.0").mkdir(parents=True)
        (self.root / "app" / "0.1.0" / "keep.txt").write_text("old app\n", encoding="utf-8")
        self.paths.app_current.write_text("0.1.0\n", encoding="utf-8")
        self.archive = make_bundle_zip(self.root / "endbot-0.2.0-test.zip", "0.2.0")

    def run_update(self, archive: Path | None = None, **kwargs) -> tuple[int, str, str]:
        kwargs.setdefault("doctor", FakeDoctor())
        kwargs.setdefault("acquire", RecordingAcquire())
        kwargs.setdefault("windows", True)
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = run_update(self.paths, self.archive if archive is None else archive, **kwargs)
        return code, stdout.getvalue(), stderr.getvalue()


class ExtractSwitchTests(UpdateTestCase):
    def test_happy_path_extracts_switches_and_replaces_the_launcher(self) -> None:
        old_endbot_toml = self.paths.endbot_toml.read_bytes()
        state_file = self.root / "state" / "secrets" / "control.token"
        state_file.parent.mkdir(parents=True)
        state_file.write_text("token\n", encoding="utf-8")

        doctor = FakeDoctor((0, ["PASS config: ok"]))
        code, out, err = self.run_update(doctor=doctor)
        self.assertEqual(code, 0, err)
        self.assertTrue((self.root / "app" / "0.2.0" / "python" / "python.exe").is_file())
        self.assertTrue((self.root / "app" / "0.2.0" / "runtime" / "src" / "cli.js").is_file())
        self.assertEqual(self.paths.app_current.read_text(encoding="utf-8"), "0.2.0\n")
        self.assertEqual((self.root / "endbot.cmd").read_bytes(), b"@echo off\r\nrem launcher v2\r\n")
        self.assertTrue((self.root / "app" / "0.1.0" / "keep.txt").is_file())
        self.assertEqual(self.paths.endbot_toml.read_bytes(), old_endbot_toml)
        self.assertEqual(state_file.read_text(encoding="utf-8"), "token\n")
        self.assertFalse((self.root / "server" / "endstone.toml").exists())
        self.assertEqual(doctor.calls, [(self.root / "app" / "0.2.0" / "python" / "python.exe", self.root)])
        self.assertIn("app/current now names 0.2.0", out)
        self.assertIn("--rollback", out)

    def test_existing_target_is_refused_without_force(self) -> None:
        (self.root / "app" / "0.2.0").mkdir()
        code, _out, err = self.run_update()
        self.assertEqual(code, 1)
        self.assertIn("--force", err)
        self.assertEqual(self.paths.app_current.read_text(encoding="utf-8"), "0.1.0\n")

    def test_force_replaces_an_existing_target(self) -> None:
        (self.root / "app" / "0.2.0").mkdir()
        (self.root / "app" / "0.2.0" / "stale.txt").write_text("stale\n", encoding="utf-8")
        code, _out, err = self.run_update(force=True)
        self.assertEqual(code, 0, err)
        self.assertFalse((self.root / "app" / "0.2.0" / "stale.txt").exists())

    def test_replacing_the_current_version_is_refused_even_with_force(self) -> None:
        current_archive = make_bundle_zip(self.root / "endbot-0.1.0-test.zip", "0.1.0")
        code, _out, err = self.run_update(current_archive, force=True)
        self.assertEqual(code, 1)
        self.assertIn("active version", err)
        self.assertEqual((self.root / "app" / "0.1.0" / "keep.txt").read_text(encoding="utf-8"), "old app\n")

    def test_bundle_without_python_is_refused(self) -> None:
        broken = make_bundle_zip(self.root / "endbot-0.3.0-test.zip", "0.3.0", with_python=False)
        code, _out, err = self.run_update(broken)
        self.assertEqual(code, 1)
        self.assertIn("python", err)
        self.assertEqual(self.paths.app_current.read_text(encoding="utf-8"), "0.1.0\n")

    def test_bundle_without_launcher_is_refused(self) -> None:
        broken = make_bundle_zip(self.root / "endbot-0.3.0-test.zip", "0.3.0", launcher=None)
        code, _out, err = self.run_update(broken)
        self.assertEqual(code, 1)
        self.assertIn("launcher", err)

    def test_zip_slip_member_is_refused(self) -> None:
        evil = self.root / "endbot-0.2.1-test.zip"
        with zipfile.ZipFile(evil, "w") as bundle:
            bundle.writestr("app/0.2.1/python/python.exe", b"x")
            bundle.writestr("../evil.txt", b"evil")
            bundle.writestr("endbot.cmd", b"launcher")
        code, _out, err = self.run_update(evil)
        self.assertEqual(code, 1)
        self.assertIn("safe relative path", err)
        self.assertFalse((self.root / "evil.txt").exists())

    def test_wrong_archive_type_for_this_platform_is_refused(self) -> None:
        tarball = self.root / "endbot-0.2.0-test.tar.gz"
        tarball.write_bytes(b"not really a tarball")
        code, _out, err = self.run_update(tarball)
        self.assertEqual(code, 1)
        self.assertIn(".zip", err)

    def test_missing_archive_is_refused(self) -> None:
        code, _out, err = self.run_update(self.root / "no-such.zip")
        self.assertEqual(code, 1)
        self.assertIn("does not exist", err)


class SumsTests(UpdateTestCase):
    def test_sums_mismatch_refuses_before_extraction(self) -> None:
        make_sums(self.archive, digest="0" * 64)
        code, _out, err = self.run_update()
        self.assertEqual(code, 1)
        self.assertIn("does not match", err)
        self.assertFalse((self.root / "app" / "0.2.0").exists())

    def test_matching_sums_beside_the_archive_are_used_by_default(self) -> None:
        make_sums(self.archive)
        code, _out, err = self.run_update()
        self.assertEqual(code, 0, err)

    def test_explicit_sums_path_is_used(self) -> None:
        sums = self.root / "elsewhere" / "SUMS"
        sums.parent.mkdir()
        sums.write_text(
            f"{hashlib.sha256(self.archive.read_bytes()).hexdigest()}  {self.archive.name}\n", encoding="utf-8"
        )
        code, _out, err = self.run_update(sums=sums)
        self.assertEqual(code, 0, err)

    def test_sums_without_an_entry_for_the_archive_refuses(self) -> None:
        sums = self.root / "SHA256SUMS"
        sums.write_text("d  other-file.zip\n", encoding="utf-8")
        code, _out, err = self.run_update(sums=sums)
        self.assertEqual(code, 1)
        self.assertIn("no entry", err)

    def test_missing_sums_warns_and_proceeds(self) -> None:
        code, out, err = self.run_update()
        self.assertEqual(code, 0, err)
        self.assertIn("integrity is NOT verified", out)


class DoctorGateTests(UpdateTestCase):
    def test_other_doctor_fail_keeps_the_old_version(self) -> None:
        doctor = FakeDoctor((1, ["FAIL config: endbot.toml is broken", "PASS server-dir: ok"]))
        code, out, _err = self.run_update(doctor=doctor)
        self.assertEqual(code, 1)
        self.assertIn("keeping app/current", out)
        self.assertEqual(self.paths.app_current.read_text(encoding="utf-8"), "0.1.0\n")
        self.assertTrue((self.root / "app" / "0.2.0").is_dir())  # kept for inspection

    def test_first_start_tolerable_failures_do_not_block(self) -> None:
        # The instance has never started: state/secrets has no keys or token yet.
        doctor = FakeDoctor(
            (1, ["FAIL control-token: missing", "FAIL local-bot-auth: missing", "FAIL endstone-version: other problem"])
        )
        code, _out, _err = self.run_update(doctor=doctor)
        self.assertEqual(code, 1)  # endstone-version still blocks
        doctor = FakeDoctor((1, ["FAIL control-token: missing", "FAIL local-bot-auth: missing"]))
        code, _out, err = self.run_update(
            doctor=doctor, archive=make_bundle_zip(self.root / "endbot-0.2.2-test.zip", "0.2.2")
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(self.paths.app_current.read_text(encoding="utf-8"), "0.2.2\n")

    def test_crashing_doctor_keeps_the_old_version(self) -> None:
        doctor = FakeDoctor((3, ["Traceback (most recent call last)"]))
        code, _out, err = self.run_update(doctor=doctor)
        self.assertEqual(code, 1)
        self.assertIn("exited with 3", err)

    def test_unparseable_doctor_failure_keeps_the_old_version(self) -> None:
        doctor = FakeDoctor((1, ["something went wrong in a future format"]))
        code, _out, err = self.run_update(doctor=doctor)
        self.assertEqual(code, 1)
        self.assertIn("unrecognized output format", err)
        self.assertEqual(self.paths.app_current.read_text(encoding="utf-8"), "0.1.0\n")


class BdsChangeTests(UpdateTestCase):
    def test_bds_version_mismatch_triggers_backup_and_install_step(self) -> None:
        doctor = FakeDoctor(
            (1, ["FAIL bds-version: version.txt says '26.51' but endstone.lock locks '26.99.1'"]),
            (0, ["PASS bds-version: matching locked BDS"]),
        )
        acquire = RecordingAcquire()
        code, out, err = self.run_update(doctor=doctor, acquire=acquire)
        self.assertEqual(code, 0, err)
        self.assertEqual(
            acquire.calls,
            [(self.root / "app" / "0.2.0" / "python" / "python.exe", self.root / "server")],
        )
        self.assertEqual(len(doctor.calls), 2)
        backups = [entry for entry in self.paths.backups.iterdir() if entry.is_dir()]
        self.assertEqual(len(backups), 1)
        self.assertTrue((backups[0] / "manifest.json").is_file())
        self.assertTrue((backups[0] / "server.properties").is_file())
        self.assertFalse((backups[0] / "worlds").exists())
        self.assertIn("re-running the new version's doctor", out)
        self.assertEqual(self.paths.app_current.read_text(encoding="utf-8"), "0.2.0\n")

    def test_bds_change_with_backup_worlds_copies_worlds(self) -> None:
        doctor = FakeDoctor((1, ["FAIL bds-version: mismatch"]), (0, ["PASS bds-version: ok"]))
        code, _out, err = self.run_update(doctor=doctor, acquire=RecordingAcquire(), backup_worlds=True)
        self.assertEqual(code, 0, err)
        backups = [entry for entry in self.paths.backups.iterdir() if entry.is_dir()]
        self.assertTrue((backups[0] / "worlds" / "world" / "level.dat").is_file())

    def test_still_failing_doctor_after_bds_change_keeps_the_old_version(self) -> None:
        doctor = FakeDoctor(
            (1, ["FAIL bds-version: mismatch"]),
            (1, ["FAIL bds-version: still mismatched"]),
        )
        code, out, _err = self.run_update(doctor=doctor, acquire=RecordingAcquire())
        self.assertEqual(code, 1)
        self.assertEqual(self.paths.app_current.read_text(encoding="utf-8"), "0.1.0\n")
        self.assertIn("keeping app/current", out)


class RollbackTests(UpdateTestCase):
    def test_rollback_switches_to_the_most_recent_previous_version(self) -> None:
        (self.root / "app" / "0.2.0").mkdir()
        (self.root / "app" / "0.2.0" / "keep.txt").write_text("new app\n", encoding="utf-8")
        os.utime(self.root / "app" / "0.1.0", (1_000_000_000, 1_000_000_000))
        os.utime(self.root / "app" / "0.2.0", (2_000_000_000, 2_000_000_000))
        self.paths.app_current.write_text("0.2.0\n", encoding="utf-8")
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = run_rollback(self.paths)
        self.assertEqual(code, 0, stderr.getvalue())
        self.assertEqual(self.paths.app_current.read_text(encoding="utf-8"), "0.1.0\n")
        self.assertIn("app/current now names 0.1.0", stdout.getvalue())

    def test_rollback_without_a_previous_version_refuses(self) -> None:
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = run_rollback(self.paths)
        self.assertEqual(code, 1)
        self.assertIn("no previous version", stderr.getvalue())

    def test_rollback_refuses_while_a_supervisor_runs(self) -> None:
        self.paths.state_run.mkdir(parents=True)
        (self.paths.state_run / "supervisor.pid").write_text(str(os.getpid()), encoding="utf-8")
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = run_rollback(self.paths)
        self.assertEqual(code, 1)
        self.assertIn("supervisor", stderr.getvalue())


class RefusalTests(UpdateTestCase):
    def test_running_supervisor_refuses_the_update(self) -> None:
        self.paths.state_run.mkdir(parents=True)
        (self.paths.state_run / "supervisor.pid").write_text(str(os.getpid()), encoding="utf-8")
        code, _out, err = self.run_update()
        self.assertEqual(code, 1)
        self.assertIn("supervisor", err)

    def test_missing_endbot_toml_refuses_the_update(self) -> None:
        self.paths.endbot_toml.unlink()
        code, _out, err = self.run_update()
        self.assertEqual(code, 1)
        self.assertIn("endbot setup", err)


class SubprocessTests(unittest.TestCase):
    def test_doctor_subprocess_uses_the_exact_documented_command(self) -> None:
        class Runner:
            def __init__(self) -> None:
                self.commands: list[list[str]] = []

            def __call__(self, command, **kwargs):
                self.commands.append(list(command))
                return SimpleNamespace(returncode=0, stdout="PASS config: ok\n", stderr="")

        runner = Runner()
        returncode, lines = run_doctor_subprocess(Path("py"), Path("inst"), runner=runner)
        self.assertEqual(returncode, 0)
        self.assertEqual(lines, ["PASS config: ok"])
        self.assertEqual(runner.commands, [["py", "-m", "endbot_cli", "--instance", "inst", "doctor"]])


if __name__ == "__main__":
    unittest.main()
