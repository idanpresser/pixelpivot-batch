"""Windows service wrapper for PixelPivot Batch Engine.

Manages FastAPI (uvicorn) and Streamlit GUI as child subprocesses.
The service binary re-spawns itself with --mode api / --mode gui so
each subsystem runs in its own process under the SCM-controlled lifetime.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

if sys.platform != "win32":
    raise ImportError("app.windows.service requires Windows")

import win32event
import win32service
import win32serviceutil
import servicemanager

SERVICE_NAME = "PixelPivotBatchEngine"
SERVICE_DISPLAY = "PixelPivot Batch Engine"
SERVICE_DESC = "High-throughput image conversion API and web GUI server."


def _log_dir() -> Path:
    data_env = os.environ.get("PIXELPIVOT_DATA_DIR")
    if data_env:
        return Path(data_env) / "logs"
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / "data" / "logs"
    return Path(__file__).parent.parent.parent / "data" / "logs"


class PixelPivotService(win32serviceutil.ServiceFramework):
    _svc_name_ = SERVICE_NAME
    _svc_display_name_ = SERVICE_DISPLAY
    _svc_description_ = SERVICE_DESC

    def __init__(self, args: list[str]) -> None:
        super().__init__(args)
        self._stop_event = win32event.CreateEvent(None, 0, 0, None)
        self._procs: list[subprocess.Popen] = []

    # ------------------------------------------------------------------
    # SCM entry points
    # ------------------------------------------------------------------

    def SvcStop(self) -> None:
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        win32event.SetEvent(self._stop_event)
        self._terminate_children()

    def SvcDoRun(self) -> None:
        servicemanager.LogMsg(
            servicemanager.EVENTLOG_INFORMATION_TYPE,
            servicemanager.PYS_SERVICE_STARTED,
            (self._svc_name_, ""),
        )
        self._start_children()
        self._monitor_until_stop()
        servicemanager.LogMsg(
            servicemanager.EVENTLOG_INFORMATION_TYPE,
            servicemanager.PYS_SERVICE_STOPPED,
            (self._svc_name_, ""),
        )

    # ------------------------------------------------------------------
    # Child lifecycle
    # ------------------------------------------------------------------

    def _start_children(self) -> None:
        log = _log_dir()
        log.mkdir(parents=True, exist_ok=True)
        exe = sys.executable
        env = {**os.environ, "PIXELPIVOT_SERVICE_MODE": "1"}

        for mode, stem in (("api", "service_api"), ("gui", "service_gui")):
            stdout = open(log / f"{stem}_stdout.log", "a", encoding="utf-8", buffering=1)
            stderr = open(log / f"{stem}_stderr.log", "a", encoding="utf-8", buffering=1)
            proc = subprocess.Popen(
                [exe, "--mode", mode],
                env=env,
                stdout=stdout,
                stderr=stderr,
            )
            self._procs.append(proc)

    def _monitor_until_stop(self) -> None:
        while True:
            rc = win32event.WaitForSingleObject(self._stop_event, 5000)
            if rc == win32event.WAIT_OBJECT_0:
                break
            for proc in self._procs:
                if proc.poll() is not None:
                    servicemanager.LogErrorMsg(
                        f"PixelPivot child PID {proc.pid} exited unexpectedly "
                        f"(rc={proc.returncode}); stopping service."
                    )
                    win32event.SetEvent(self._stop_event)
                    return

    def _terminate_children(self) -> None:
        for proc in reversed(self._procs):
            try:
                proc.terminate()
            except OSError:
                pass
        deadline = time.monotonic() + 15.0
        for proc in self._procs:
            remaining = max(0.1, deadline - time.monotonic())
            try:
                proc.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                except OSError:
                    pass
        self._procs.clear()
