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

    The current process does NOT exit — the caller decides whether to wait
    or continue.  On Windows < Vista or headless sessions this silently fails.
    """
    import os
    import win32con
    from win32com.shell import shell

    if not os.path.exists(exe):
        raise FileNotFoundError(f"Target service executable not found at: {exe}")

    shell.ShellExecuteEx(
        fMask=64,            # SEE_MASK_NOCLOSEPROCESS — don't close handle immediately
        lpVerb="runas",
        lpFile=exe,
        lpParameters=" ".join(args),
        nShow=win32con.SW_SHOWNORMAL,
    )
