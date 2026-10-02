"""Parent-owned Windows Job Object for one budgeted MBOX worker.

Only the standard library is used. The worker joins by a per-launch name before
importing parsers; the parent keeps the handle until the worker has been reaped.
These resource controls are not a security sandbox.
"""

from __future__ import annotations

import ctypes

# Windows uses LLP64 even on 64-bit hosts. Explicit widths also let the ABI
# contract tests run on POSIX, where ctypes.c_ulong can be eight bytes.
_DWORD = ctypes.c_uint32
_BOOL = ctypes.c_int32
_HANDLE = ctypes.c_void_p
_SIZE_T = ctypes.c_size_t
_TICKS_PER_SECOND = 10_000_000
_EXTENDED_LIMIT_INFORMATION = 9
_JOB_TIME = 0x00000004
_ACTIVE_PROCESS = 0x00000008
_PROCESS_MEMORY = 0x00000100
_KILL_ON_JOB_CLOSE = 0x00002000
_WAIT_OBJECT_0 = 0
_WAIT_TIMEOUT = 258
_ERROR_ALREADY_EXISTS = 183


class _BasicLimits(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", _DWORD),
        ("MinimumWorkingSetSize", _SIZE_T),
        ("MaximumWorkingSetSize", _SIZE_T),
        ("ActiveProcessLimit", _DWORD),
        ("Affinity", _SIZE_T),
        ("PriorityClass", _DWORD),
        ("SchedulingClass", _DWORD),
    ]


class _IOCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
    )]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimits),
        ("IoInfo", _IOCounters),
        ("ProcessMemoryLimit", _SIZE_T),
        ("JobMemoryLimit", _SIZE_T),
        ("PeakProcessMemoryUsed", _SIZE_T),
        ("PeakJobMemoryUsed", _SIZE_T),
    ]


def _kernel32():
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    for name, args, result in (
        ("CreateJobObjectW", [ctypes.c_void_p, ctypes.c_wchar_p], _HANDLE),
        ("SetInformationJobObject", [_HANDLE, ctypes.c_int, ctypes.c_void_p, _DWORD], _BOOL),
        ("QueryInformationJobObject", [_HANDLE, ctypes.c_int, ctypes.c_void_p, _DWORD,
                                       ctypes.POINTER(_DWORD)], _BOOL),
        ("WaitForSingleObject", [_HANDLE, _DWORD], _DWORD),
        ("CloseHandle", [_HANDLE], _BOOL),
    ):
        function = getattr(api, name)
        function.argtypes = args
        function.restype = result
    return api


def job_name(nonce: str) -> str:
    if len(nonce) != 32 or any(c not in "0123456789abcdef" for c in nonce):
        raise ValueError("Invalid worker nonce")
    return "Local\\dead-letter-" + nonce


def _limits(budgets: dict[str, int]) -> _ExtendedLimits:
    if not budgets or budgets.keys() - {"memory_mib", "cpu_seconds"}:
        raise ValueError("Unsupported Windows worker budget")
    limits = _ExtendedLimits()
    basic = limits.BasicLimitInformation
    basic.LimitFlags = _KILL_ON_JOB_CLOSE | _ACTIVE_PROCESS
    basic.ActiveProcessLimit = 1
    for name, value in budgets.items():
        unit = _TICKS_PER_SECOND if name == "cpu_seconds" else 1024 * 1024
        maximum = 2**63 - 1 if name == "cpu_seconds" else 2 ** (8 * ctypes.sizeof(_SIZE_T)) - 1
        if type(value) is not int or value <= 0 or value * unit > maximum:
            raise ValueError("Invalid Windows worker budget")
        if name == "cpu_seconds":
            basic.LimitFlags |= _JOB_TIME
            basic.PerJobUserTimeLimit = value * unit
        else:
            basic.LimitFlags |= _PROCESS_MEMORY
            limits.ProcessMemoryLimit = value * unit
    return limits


class WindowsJob:
    """Own a fresh job; failed setup never starts an unenforced worker."""

    def __init__(self, budgets: dict[str, int], nonce: str) -> None:
        requested = _limits(budgets)
        name = job_name(nonce)
        self._api = _kernel32()
        self._handle = None
        self._cpu = "cpu_seconds" in budgets
        ctypes.set_last_error(0)
        self._handle = self._api.CreateJobObjectW(None, name)
        error = ctypes.get_last_error()
        if not self._handle:
            raise OSError("Could not create worker job")
        try:
            # Do not adopt an existing job or change another owner's policy.
            if error == _ERROR_ALREADY_EXISTS:
                raise OSError("Worker job already exists")
            if not self._api.SetInformationJobObject(
                self._handle, _EXTENDED_LIMIT_INFORMATION,
                ctypes.byref(requested), ctypes.sizeof(requested),
            ):
                raise OSError("Could not configure worker job")
            actual = _ExtendedLimits()
            returned = _DWORD()
            if not self._api.QueryInformationJobObject(
                self._handle, _EXTENDED_LIMIT_INFORMATION, ctypes.byref(actual),
                ctypes.sizeof(actual), ctypes.byref(returned),
            ):
                raise OSError("Could not verify worker job")
            wanted, applied = requested.BasicLimitInformation, actual.BasicLimitInformation
            if (
                returned.value != ctypes.sizeof(actual)
                or applied.LimitFlags != wanted.LimitFlags
                or applied.ActiveProcessLimit != 1
                or applied.PerJobUserTimeLimit != wanted.PerJobUserTimeLimit
                or actual.ProcessMemoryLimit != requested.ProcessMemoryLimit
            ):
                raise OSError("Worker job limits were not applied")
        except BaseException:
            self.close()
            raise

    def cpu_exceeded(self) -> bool:
        if not self._cpu:
            return False
        # A job is signaled for end-of-job time, not an arbitrary process exit.
        # ERROR_NOT_ENOUGH_QUOTA (1816) alone could be an unrelated native exit.
        state = self._api.WaitForSingleObject(self._handle, 0)
        if state not in (_WAIT_OBJECT_0, _WAIT_TIMEOUT):
            raise OSError("Could not read worker job state")
        return state == _WAIT_OBJECT_0

    def close(self) -> None:
        if self._handle is not None:
            handle, self._handle = self._handle, None
            if not self._api.CloseHandle(handle):
                raise OSError("Could not close worker job")
