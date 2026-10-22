"""Mocked launch contracts plus small, platform-gated real OS probes (no BDS)."""

import ctypes
import errno
import json
import os
import queue
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from endstone_endbot import script_confine as confine
from endstone_endbot.i18n import LocalizedError, localize_error
from endstone_endbot.script_config import PROCESS_HEADROOM, ScriptConfig
from endstone_endbot.script_process import RunnerProcess

MIB = 1024 * 1024


class ConfigTests(unittest.TestCase):
    def test_defaults_headroom_zero_and_strict_types(self):
        config = ScriptConfig.from_config({})
        self.assertTrue(config.os_confinement)
        self.assertEqual(config.process_memory_bytes, 256 * MIB)
        self.assertEqual(config.cpu_seconds, 0)
        for settings in (
            {"os-confinement": False}, {"process-memory-bytes": 0}, {"cpu-seconds": 0},
            {"process-memory-bytes": config.memory_bytes + PROCESS_HEADROOM}, {"cpu-seconds": 1},
        ):
            ScriptConfig.from_config(settings)
        for settings in (
            {"os-confinement": 0}, {"os-confinement": "false"}, {"cpu-seconds": True},
            {"cpu-seconds": -1}, {"cpu-seconds": 1.5}, {"cpu-seconds": 2147483648},
            {"process-memory-bytes": True}, {"process-memory-bytes": -1},
            {"process-memory-bytes": config.memory_bytes + PROCESS_HEADROOM - 1},
            {"memory-bytes": 256 * MIB},
            {"os-confinement": False, "process-memory-bytes": 1},
        ):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                ScriptConfig.from_config(settings)
        limits = config.limits()
        self.assertEqual(limits["memoryBytes"], 32 * MIB)
        self.assertEqual(limits["processMemoryBytes"], 256 * MIB)
        self.assertTrue(limits["osConfinement"])
        self.assertEqual(limits["cpuSeconds"], 0)


class WindowsJobTests(unittest.TestCase):
    def api(self):
        api = Mock()
        api.CreateJobObjectW.return_value = 123
        self.calls, self.information = [], None

        def create(*args):
            self.calls.append("create")
            return 123

        def configure(handle, kind, pointer, size):
            self.calls.append("configure")
            self.assertEqual((handle, kind, size), (123, 9, ctypes.sizeof(confine._ExtendedLimits)))
            self.information = pointer._obj
            return True

        api.CreateJobObjectW.side_effect = create
        api.SetInformationJobObject.side_effect = configure
        api.AssignProcessToJobObject.side_effect = lambda *args: self.calls.append("assign") or True
        api.CloseHandle.side_effect = lambda *args: self.calls.append("close") or True
        return api

    def test_setup_flags_values_order_and_close_once(self):
        api = self.api()
        with patch.object(confine, "_kernel32", return_value=api):
            job = confine.WindowsJob(256 * MIB, 3)
            job.assign(Mock(_handle=456))
            job.close()
            job.close()
        basic = self.information.BasicLimitInformation
        self.assertEqual(basic.LimitFlags, 0x2000 | 0x8 | 0x100 | 0x2)
        self.assertEqual(basic.ActiveProcessLimit, 1)
        self.assertEqual(basic.PerProcessUserTimeLimit, 30_000_000)
        self.assertEqual(self.information.ProcessMemoryLimit, 256 * MIB)
        self.assertEqual(self.calls, ["create", "configure", "assign", "close"])
        api.CreateJobObjectW.assert_called_once_with(None, None)
        api.AssignProcessToJobObject.assert_called_once_with(123, 456)

    def test_zero_disables_only_memory_and_cpu(self):
        with patch.object(confine, "_kernel32", return_value=self.api()):
            job = confine.WindowsJob(0, 0)
            self.addCleanup(job.close)
        self.assertEqual(self.information.BasicLimitInformation.LimitFlags, 0x2000 | 0x8)

    def test_api_signatures_keep_pointer_sized_handles(self):
        api = Mock()
        with patch.object(ctypes, "WinDLL", return_value=api, create=True):
            self.assertIs(confine._kernel32(), api)
        self.assertIs(api.CreateJobObjectW.restype, ctypes.wintypes.HANDLE)
        self.assertEqual(api.AssignProcessToJobObject.argtypes, [ctypes.wintypes.HANDLE] * 2)
        if ctypes.sizeof(ctypes.c_void_p) == 8:
            self.assertEqual(ctypes.sizeof(confine._ExtendedLimits), 144)
            self.assertEqual(confine._ExtendedLimits.ProcessMemoryLimit.offset, 112)
            self.assertEqual(ctypes.sizeof(confine._Accounting), 48)

    def test_native_false_results_raise_and_release_any_created_job(self):
        for stage in ("create", "configure", "assign"):
            api = self.api()
            with (self.subTest(stage=stage), patch.object(confine, "_kernel32", return_value=api),
                  patch.object(ctypes, "get_last_error", return_value=5, create=True),
                  patch.object(ctypes, "WinError", side_effect=lambda code: OSError(code, "denied"), create=True)):
                if stage == "create":
                    api.CreateJobObjectW.side_effect = lambda *args: 0
                elif stage == "configure":
                    api.SetInformationJobObject.side_effect = lambda *args: False
                else:
                    api.AssignProcessToJobObject.side_effect = lambda *args: False
                job = None
                try:
                    with self.assertRaises(OSError):
                        job = confine.WindowsJob(256 * MIB, 0)
                        job.assign(Mock(_handle=456))
                finally:
                    if job is not None:
                        job.close()
                if stage == "create":
                    api.CloseHandle.assert_not_called()
                else:
                    api.CloseHandle.assert_called_once_with(123)

    def test_setup_failure_closes_handle_and_assignment_failure_propagates(self):
        api = self.api()
        api.SetInformationJobObject.side_effect = OSError("job setup denied")
        with patch.object(confine, "_kernel32", return_value=api), self.assertRaises(OSError):
            confine.WindowsJob(256 * MIB, 0)
        api.CloseHandle.assert_called_once_with(123)
        api = self.api()
        with patch.object(confine, "_kernel32", return_value=api):
            job = confine.WindowsJob(256 * MIB, 0)
        self.addCleanup(job.close)
        api.AssignProcessToJobObject.side_effect = OSError("nested job denied")
        with self.assertRaises(OSError):
            job.assign(Mock(_handle=456))

    def test_cpu_accounting(self):
        api = self.api()
        with patch.object(confine, "_kernel32", return_value=api):
            job = confine.WindowsJob(0, 2)
        self.addCleanup(job.close)

        def query(handle, kind, pointer, size, returned):
            self.assertEqual((handle, kind), (123, 1))
            pointer._obj.TotalUserTime = 20_000_000
            return True

        api.QueryInformationJobObject.side_effect = query
        self.assertTrue(job.cpu_exceeded())
        api.QueryInformationJobObject.side_effect = None
        api.QueryInformationJobObject.return_value = False
        self.assertFalse(job.cpu_exceeded())


class LaunchTests(unittest.TestCase):
    def test_platform_popen_options_and_explicit_opt_out(self):
        with patch.object(confine.sys, "platform", "win32"), patch.object(confine, "WindowsJob") as job:
            limits = confine.Confinement({})
            self.assertEqual(limits.popen_kwargs, {"creationflags": 0x08004000})
            self.assertEqual(limits.runner_args, [])
            process = Mock()
            limits.attach(process)
            job.return_value.assign.assert_called_once_with(process)
            limits.close()
            job.return_value.close.assert_called_once()
        with patch.object(confine.sys, "platform", "linux"), patch.object(os, "getpid", return_value=1234):
            limits = confine.Confinement({"cpuSeconds": 2})
            self.assertEqual(limits.popen_kwargs, {})
            self.assertEqual(limits.runner_args, [
                "--max-address-space", str(256 * MIB), "--max-cpu-seconds", "2",
                "--parent-pid", "1234", "--nice", "10",
            ])
        with patch.object(confine.sys, "platform", "other"):
            with self.assertRaisesRegex(OSError, "supported only"):
                confine.Confinement({})
            self.assertEqual(confine.Confinement({"osConfinement": False}).popen_kwargs, {})

    def test_fail_closed_before_launch_or_before_any_script_input(self):
        for stage in ("setup", "popen", "attach"):
            with (self.subTest(stage=stage),
                  patch("endstone_endbot.script_process.Confinement") as factory,
                  patch("endstone_endbot.script_process.subprocess.Popen") as popen,
                  self.assertLogs("endstone_endbot.script_process", level="ERROR")):
                factory.return_value.popen_kwargs = {}
                factory.return_value.runner_args = []
                popen.return_value.poll.return_value = None
                if stage == "setup":
                    factory.side_effect = OSError("setup denied")
                elif stage == "popen":
                    popen.side_effect = subprocess.SubprocessError("spawn failed")
                else:
                    factory.return_value.attach.side_effect = OSError("assign denied")
                with self.assertRaisesRegex(LocalizedError, "OS confinement failed") as caught:
                    RunnerProcess(Mock(is_file=lambda: True), "source", "api", {}, ScriptConfig().limits())
                self.assertNotIn("OS confinement failed", localize_error(caught.exception, "ja"))
                popen.return_value.stdin.write.assert_not_called()
                if stage == "setup":
                    popen.assert_not_called()
                else:
                    factory.return_value.close.assert_called_once()
                if stage == "attach":
                    popen.return_value.kill.assert_called_once()
                    popen.return_value.wait.assert_called_once_with(timeout=5)
                    popen.return_value.stdin.close.assert_called_once()
                    popen.return_value.stdout.close.assert_called_once()

    def test_attach_precedes_load_os_settings_stay_in_host_and_close_releases(self):
        order = []
        with (patch("endstone_endbot.script_process.Confinement") as factory,
              patch("endstone_endbot.script_process.subprocess.Popen") as popen,
              patch("endstone_endbot.script_process.threading.Thread")):
            factory.return_value.popen_kwargs = {}
            factory.return_value.runner_args = ["--max-address-space", "123"]
            factory.return_value.attach.side_effect = lambda process: order.append("attach")
            popen.return_value.stdin.write.side_effect = lambda data: order.append("load")
            process = RunnerProcess(Mock(is_file=lambda: True), "source", "api", {}, ScriptConfig().limits())
            self.assertEqual(popen.call_args.args[0][1:], factory.return_value.runner_args)
            self.assertNotIn("preexec_fn", popen.call_args.kwargs)
            self.assertEqual(popen.call_args.kwargs["stdin"], subprocess.PIPE)
            self.assertEqual(order, ["attach", "load"])
            frame = json.loads(popen.return_value.stdin.write.call_args.args[0])
            self.assertNotIn("processMemoryBytes", frame["limits"])
            self.assertEqual(frame["limits"]["memoryBytes"], 32 * MIB)
            process.close()
            factory.return_value.close.assert_called_once()

    def test_exec_enomem_reports_allocation_failure_without_retry(self):
        with (patch("endstone_endbot.script_process.Confinement") as factory,
              patch("endstone_endbot.script_process.subprocess.Popen", side_effect=OSError(errno.ENOMEM, "ENOMEM")),
              self.assertLogs("endstone_endbot.script_process", level="ERROR")):
            factory.return_value.popen_kwargs = {}
            factory.return_value.runner_args = []
            with self.assertRaisesRegex(LocalizedError, "process memory allocation failed"):
                RunnerProcess(Mock(is_file=lambda: True), "source", "api", {}, ScriptConfig().limits())
            factory.return_value.close.assert_called_once()

    def test_opt_out_launches_without_platform_setup(self):
        for platform in ("linux", "win32", "unsupported"):
            with (self.subTest(platform=platform), patch.object(confine.sys, "platform", platform),
                  patch("endstone_endbot.script_process.subprocess.Popen") as popen,
                  patch("endstone_endbot.script_process.threading.Thread")):
                executable = Mock(is_file=lambda: True)
                process = RunnerProcess(
                    executable, "source", "api", {}, ScriptConfig(os_confinement=False).limits()
                )
                self.assertEqual(process._confine.popen_kwargs, {})
                self.assertEqual(popen.call_args.args[0], [str(executable)])
                self.assertNotIn("preexec_fn", popen.call_args.kwargs)
                process.close()

    def test_linux_launch_passes_limits_as_argv_without_child_callback(self):
        with (patch.object(confine.sys, "platform", "linux"), patch.object(os, "getpid", return_value=1234),
              patch("endstone_endbot.script_process.subprocess.Popen") as popen,
              patch("endstone_endbot.script_process.threading.Thread")):
            executable = Mock(is_file=lambda: True)
            process = RunnerProcess(executable, "source", "api", {}, ScriptConfig(cpu_seconds=2).limits())
            self.assertEqual(popen.call_args.args[0], [
                str(executable), "--max-address-space", str(256 * MIB), "--max-cpu-seconds", "2",
                "--parent-pid", "1234", "--nice", "10",
            ])
            self.assertNotIn("preexec_fn", popen.call_args.kwargs)
            process.close()

    def test_runner_confinement_error_fails_closed_and_is_localized(self):
        for event in (
            {"type": "error", "phase": "confinement", "message": "setrlimit denied"},
            {"type": "error", "message": "confinement failed: parent PID changed"},
        ):
            with (self.subTest(event=event), patch.object(confine.sys, "platform", "linux"),
                  patch("endstone_endbot.script_process.subprocess.Popen") as popen,
                  patch("endstone_endbot.script_process.threading.Thread")):
                process = RunnerProcess(Mock(is_file=lambda: True), "source", "api", {}, ScriptConfig().limits())
                popen.return_value.poll.return_value = None
                popen.return_value.stdout.readline.return_value = json.dumps(event).encode() + b"\n"
                with self.assertLogs("endstone_endbot.script_process", level="ERROR"):
                    process._read()
                popen.return_value.kill.assert_called_once()
                with self.assertRaisesRegex(LocalizedError, "OS confinement failed") as caught:
                    process.receive(None, threading.Event(), lambda: None)
                self.assertNotIn("OS confinement failed", localize_error(caught.exception, "ja"))
                self.assertEqual(popen.call_count, 1)  # Never retry without confinement.
                # A broken initial write must also retain the reported startup failure.
                popen.return_value.stdin.write.side_effect = BrokenPipeError()
                with self.assertRaisesRegex(LocalizedError, "OS confinement failed"):
                    process.send({"type": "load"})
                process.close()

    def test_failure_classification_and_host_kill_is_not_a_limit_claim(self):
        with patch.object(confine.sys, "platform", "linux"):
            limits = confine.Confinement({"cpuSeconds": 1})
        self.assertEqual(limits.failure(-24), "cpu")
        self.assertIsNone(limits.failure(-9))
        with patch.object(confine.sys, "platform", "win32"), patch.object(confine, "WindowsJob"):
            windows = confine.Confinement({"cpuSeconds": 1})
        windows.job.cpu_exceeded.return_value = False
        self.assertEqual(windows.failure(0xC0000017), "memory")
        self.assertIsNone(windows.failure(1))
        windows.job.cpu_exceeded.return_value = True
        self.assertEqual(windows.failure(1816), "cpu")
        process = RunnerProcess.__new__(RunnerProcess)
        process._events, process._process, process._confine = queue.Queue(), Mock(), limits
        process._host_terminated = False
        process._process.poll.return_value = -24
        process._events.put(ValueError("EOF"))
        with self.assertRaisesRegex(LocalizedError, "CPU time limit"):
            process.receive(None, threading.Event(), lambda: None)
        process._host_terminated = True
        with self.assertRaisesRegex(LocalizedError, "runner failed"):
            process._failure("host killed")


class RealOSBase(unittest.TestCase):
    def spawn(self, code, *, cpu=0, memory=256 * MIB):
        limits = confine.Confinement({"processMemoryBytes": memory, "cpuSeconds": cpu})
        self.addCleanup(limits.close)
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(filter(None, (
            str(Path(confine.__file__).resolve().parents[1]), env.get("PYTHONPATH"),
        )))
        process = subprocess.Popen(
            [sys.executable, "-u", "-c", code], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, env=env, **limits.popen_kwargs,
        )

        def cleanup():
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)

        self.addCleanup(cleanup)
        limits.attach(process)
        return limits, process


@unittest.skipUnless(sys.platform == "win32", "real Windows Job Object probe")
class RealWindowsTests(RealOSBase):
    def test_memory_cap_rejects_native_commit_and_active_limit_rejects_child(self):
        _, process = self.spawn("""
import ctypes, subprocess, sys
sys.stdin.readline()
api = ctypes.WinDLL('kernel32', use_last_error=True)
api.VirtualAlloc.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_ulong, ctypes.c_ulong]
api.VirtualAlloc.restype = ctypes.c_void_p
assert not api.VirtualAlloc(None, 512 * 1024 * 1024, 0x3000, 4), 'commit escaped memory cap'
try:
    subprocess.run([sys.executable, '-c', 'pass'], check=True, timeout=5)
except (OSError, subprocess.CalledProcessError):
    print('limited', flush=True)
else:
    raise AssertionError('child process escaped active-process limit')
""")
        out, err = process.communicate(b"go\n", timeout=10)
        self.assertEqual(process.returncode, 0, err.decode(errors="replace"))
        self.assertEqual(out.strip(), b"limited")

    def test_kill_on_job_close(self):
        limits, process = self.spawn("import sys,time; sys.stdin.readline(); time.sleep(60)")
        self.assertIsNone(process.poll())
        limits.close()
        process.wait(timeout=5)

    def test_cumulative_cpu_cap(self):
        limits, process = self.spawn("import sys; sys.stdin.readline()\nwhile True: pass", cpu=1)
        process.communicate(b"go\n", timeout=15)
        self.assertNotEqual(process.returncode, 0)
        self.assertEqual(limits.failure(process.returncode), "cpu")


class NativeRunnerTests(unittest.TestCase):
    def test_built_runner_obeys_os_cpu_cap_before_vm_budget(self):
        root = Path(__file__).resolve().parents[3]
        executable = root / "runner" / ".build" / "native" / "bin" / (
            "endbot-luau-runner.exe" if sys.platform == "win32" else "endbot-luau-runner"
        )
        if sys.platform not in {"win32", "linux"} or not executable.is_file():
            self.skipTest("matching native runner build unavailable")
        config = ScriptConfig(cpu_seconds=1, slice_ms=60000, interrupt_budget=2147483647)
        process = RunnerProcess(
            executable, "while true do end", executable.with_name("api.luau").read_text(encoding="utf-8"),
            {}, config.limits(),
        )
        self.addCleanup(process.close)
        with self.assertRaisesRegex(LocalizedError, "CPU time limit"):
            process.receive(time.monotonic() + 15, threading.Event(), lambda: None)


if __name__ == "__main__":
    unittest.main()
