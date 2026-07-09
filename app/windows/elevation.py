"""UAC elevation helpers for Windows."""
from __future__ import annotations

import ctypes
import sys


def is_admin() -> bool:
    """Return True if the current process has administrator privileges."""
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
    except Exception:
        return False


def elevate(exe: str, *args: str) -> None:
    """Re-launch *exe* with *args* elevated via ShellExecuteEx / runas verb.

    Waits for the elevated process to exit (up to 30s), closes the handle,
    and raises an error if the process exited with a non-zero code or timed out.
    """
    import os
    import win32con
    import win32event
    import win32process
    from win32com.shell import shell

    if not os.path.exists(exe):
        raise FileNotFoundError(f"Target service executable not found at: {exe}")

    info = shell.ShellExecuteEx(
        fMask=64,            # SEE_MASK_NOCLOSEPROCESS — don't close handle immediately
        lpVerb="runas",
        lpFile=exe,
        lpParameters=" ".join(args),
        nShow=win32con.SW_SHOWNORMAL,
    )

    hProcess = info.get("hProcess")
    if hProcess:
        try:
            rc = win32event.WaitForSingleObject(hProcess, 30000)
            if rc == win32event.WAIT_TIMEOUT:
                raise RuntimeError(f"Elevated operation timed out: {exe} {' '.join(args)}")
            
            exit_code = win32process.GetExitCodeProcess(hProcess)
            if exit_code != 0:
                raise RuntimeError(f"Elevated operation failed with exit code {exit_code}: {exe} {' '.join(args)}")
        finally:
            hProcess.Close()

