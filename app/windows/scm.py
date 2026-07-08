"""Windows Service Control Manager helpers.

All win32 imports are lazy so this module can be imported on Windows
without pywin32 installed (returns 'not_installed' state).
"""
from __future__ import annotations

SERVICE_NAME = "PixelPivotBatchEngine"

_STATE_MAP = {
    1: "stopped",    # SERVICE_STOPPED
    2: "starting",   # SERVICE_START_PENDING
    3: "stopping",   # SERVICE_STOP_PENDING
    4: "running",    # SERVICE_RUNNING
    6: "paused",     # SERVICE_PAUSED
}


def get_state() -> str:
    """Return service state: running / stopped / starting / stopping / paused / not_installed."""
    try:
        import win32serviceutil

        status = win32serviceutil.QueryServiceStatus(SERVICE_NAME)
        return _STATE_MAP.get(status[1], "unknown")
    except Exception:
        return "not_installed"


def start_service() -> None:
    import win32serviceutil

    win32serviceutil.StartService(SERVICE_NAME)


def stop_service() -> None:
    import win32serviceutil

    win32serviceutil.StopService(SERVICE_NAME)
