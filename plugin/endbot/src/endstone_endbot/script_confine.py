"""Per-run OS resource limits, not a filesystem/network security sandbox.

Windows assigns immediately after Popen, before any script input is written.
The trusted runner can execute native startup code during that interval; it
blocks reading its load frame. Assignment failure always kills/reaps it.
Linux confinement is applied by the native runner before reading stdin; the
multithreaded server must never run Python callbacks between fork and exec.
"""

import ctypes
import os
import signal
import sys
from ctypes import wintypes

DWORD = ctypes.c_uint32  # Windows DWORD stays 32-bit when structures are tested on Linux.


class _BasicLimits(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", DWORD),
        ("Affinity", ctypes.c_size_t), ("PriorityClass", DWORD), ("SchedulingClass", DWORD),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
    )]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimits), ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _Accounting(ctypes.Structure):
    _fields_ = [
        ("TotalUserTime", ctypes.c_int64), ("TotalKernelTime", ctypes.c_int64),
        ("ThisPeriodTotalUserTime", ctypes.c_int64), ("ThisPeriodTotalKernelTime", ctypes.c_int64),
        ("TotalPageFaultCount", DWORD), ("TotalProcesses", DWORD),
        ("ActiveProcesses", DWORD), ("TotalTerminatedProcesses", DWORD),
    ]


def _kernel32():
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    signatures = {
        "CreateJobObjectW": ([ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
        "SetInformationJobObject": ([wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD], wintypes.BOOL),
        "AssignProcessToJobObject": ([wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
        "QueryInformationJobObject": (
            [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p], wintypes.BOOL
        ),
        "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
    }
    for name, (args, result) in signatures.items():
        function = getattr(api, name)
        function.argtypes, function.restype = args, result
    return api


class WindowsJob:
    def __init__(self, memory, cpu):
        self.api, self.handle, self.cpu = _kernel32(), None, cpu
        self.handle = self.api.CreateJobObjectW(None, None)  # Unnamed and non-inheritable.
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            limits = _ExtendedLimits()
            basic = limits.BasicLimitInformation
            basic.LimitFlags = 0x2000 | 0x8  # KILL_ON_JOB_CLOSE | ACTIVE_PROCESS (no breakaway).
            basic.ActiveProcessLimit = 1
            if memory:
                basic.LimitFlags |= 0x100  # PROCESS_MEMORY, committed bytes (not RSS).
                limits.ProcessMemoryLimit = memory
            if cpu:
                basic.LimitFlags |= 0x2  # PROCESS_TIME, cumulative user CPU time.
                basic.PerProcessUserTimeLimit = cpu * 10_000_000
            if not self.api.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                raise ctypes.WinError(ctypes.get_last_error())
        except BaseException:
            self.close()
            raise

    def assign(self, process):
        # Popen owns this handle; do not open a PID (which could be reused) or close it here.
        if not self.api.AssignProcessToJobObject(self.handle, int(process._handle)):
            raise ctypes.WinError(ctypes.get_last_error())

    def cpu_exceeded(self):
        info = _Accounting()
        return bool(self.cpu and self.api.QueryInformationJobObject(
            self.handle, 1, ctypes.byref(info), ctypes.sizeof(info), None
        ) and info.TotalUserTime >= self.cpu * 10_000_000)

    def close(self):
        if self.handle is not None:
            if not self.api.CloseHandle(self.handle):
                raise ctypes.WinError(ctypes.get_last_error())
            self.handle = None


class Confinement:
    def __init__(self, limits):
        self.enabled = limits.get("osConfinement", True)
        self.memory = limits.get("processMemoryBytes", 256 * 1024 * 1024)
        self.cpu = limits.get("cpuSeconds", 0)
        self.platform, self.job = sys.platform, None
        self.popen_kwargs = {}
        self.runner_args = []
        if not self.enabled:
            return
        if self.platform == "win32":
            self.job = WindowsJob(self.memory, self.cpu)
            self.popen_kwargs = {"creationflags": 0x08000000 | 0x00004000}  # NO_WINDOW | BELOW_NORMAL_PRIORITY_CLASS
        elif self.platform == "linux":
            self.runner_args = [
                "--max-address-space", str(self.memory), "--max-cpu-seconds", str(self.cpu),
                "--parent-pid", str(os.getpid()), "--nice", "10",
            ]
        else:
            raise OSError("OS confinement is supported only on Windows and Linux")

    def attach(self, process):
        if self.job:
            self.job.assign(process)

    def failure(self, returncode):
        if not self.enabled or returncode is None:
            return None
        if self.platform == "linux" and returncode == -getattr(signal, "SIGXCPU", 24):
            return "cpu"
        if self.job:
            # Allocation failures are not necessarily fatal; recognize only explicit native statuses.
            if self.memory and returncode & 0xFFFFFFFF in {0xC0000017, 0xC000012D}:  # NO_MEMORY / COMMITMENT_LIMIT
                return "memory"
            if self.cpu and returncode != 0 and self.job.cpu_exceeded():
                return "cpu"
        # SIGKILL, SIGSEGV, abort/bad_alloc and ENOMEM without a native status are ambiguous.
        return None

    def close(self):
        if self.job:
            self.job.close()
