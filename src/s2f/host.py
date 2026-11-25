import ctypes
import os
from ctypes import wintypes
from pathlib import PureWindowsPath

from .safety import SafetyError, digest


def instance():
    """Bind runtime leases to the native host's PID and creation time, not a resumable session ID."""
    if os.name != "nt":
        raise SafetyError("Native grants currently require Windows host-instance verification.")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    native = ctypes.WinDLL("ntdll")
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
    ]
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4)]
    native.NtQueryInformationProcess.argtypes = [
        wintypes.HANDLE, wintypes.ULONG, ctypes.c_void_p, wintypes.ULONG, ctypes.POINTER(wintypes.ULONG),
    ]
    native.NtQueryInformationProcess.restype = wintypes.LONG
    pid = os.getpid()
    seen = set()
    for _ in range(24):
        if not pid or pid in seen:
            break
        seen.add(pid)
        handle = kernel.OpenProcess(0x1400, False, pid)
        if not handle:
            break
        try:
            name, size = ctypes.create_unicode_buffer(32768), wintypes.DWORD(32768)
            if not kernel.QueryFullProcessImageNameW(handle, 0, name, ctypes.byref(size)):
                break
            executable = PureWindowsPath(name.value).name.casefold()
            if executable in ("node.exe", "copilot.exe") or executable.startswith("copilot-"):
                times = [wintypes.FILETIME() for _ in range(4)]
                if not kernel.GetProcessTimes(handle, *(ctypes.byref(value) for value in times)):
                    break
                return digest([pid, times[0].dwHighDateTime, times[0].dwLowDateTime])
            basic, returned = (ctypes.c_void_p * 6)(), wintypes.ULONG()
            if native.NtQueryInformationProcess(handle, 0, basic, ctypes.sizeof(basic), ctypes.byref(returned)) != 0:
                break
            pid = basic[5]
        finally:
            kernel.CloseHandle(handle)
    raise SafetyError("Cannot verify the native CLI process instance; local grants/read opt-in are blocked.")
