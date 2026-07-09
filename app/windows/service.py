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
import threading
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
        self._procs_lock = threading.Lock()

    # ------------------------------------------------------------------
    # SCM entry points
    # ------------------------------------------------------------------

    def SvcStop(self) -> None:
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        win32event.SetEvent(self._stop_event)

    def SvcDoRun(self) -> None:
        servicemanager.LogMsg(
            servicemanager.EVENTLOG_INFORMATION_TYPE,
            servicemanager.PYS_SERVICE_STARTED,
            (self._svc_name_, ""),
        )
        self._start_children()
        self._monitor_until_stop()
        self._terminate_children()
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
        
        is_frozen = getattr(sys, "frozen", False)
        exe = sys.executable
        
        if is_frozen:
            project_root = Path(sys.executable).parent
        else:
            project_root = Path(__file__).parent.parent.parent
            if Path(exe).name.lower() == "pythonservice.exe":
                # Resolve pythonservice.exe to the virtualenv python interpreter
                parent = Path(exe).parent
                candidates = [
                    parent / "Scripts" / "python.exe",
                    parent / "python.exe",
                    parent.parent / "Scripts" / "python.exe",
                    parent.parent / "python.exe",
                ]
                for c in candidates:
                    if c.exists():
                        exe = str(c)
                        break
                else:
                    exe = "python"
        
        env = {**os.environ, "PIXELPIVOT_SERVICE_MODE": "1"}

        for mode, stem in (("api", "service_api"), ("gui", "service_gui")):
            stdout = open(log / f"{stem}_stdout.log", "a", encoding="utf-8", buffering=1)
            stderr = open(log / f"{stem}_stderr.log", "a", encoding="utf-8", buffering=1)
            
            cmd = [exe]
            if not is_frozen:
                cmd.append(str(project_root / "app" / "windows" / "service_main.py"))
            cmd.extend(["--mode", mode])
            
            proc = subprocess.Popen(
                cmd,
                cwd=str(project_root),
                env=env,
                stdout=stdout,
                stderr=stderr,
            )
            with self._procs_lock:
                self._procs.append(proc)

    def _monitor_until_stop(self) -> None:
        while True:
            rc = win32event.WaitForSingleObject(self._stop_event, 5000)
            if rc == win32event.WAIT_OBJECT_0:
                break
            with self._procs_lock:
                procs_copy = list(self._procs)
            for proc in procs_copy:
                if proc.poll() is not None:
                    # Double-check if the stop event was set in the meantime
                    if win32event.WaitForSingleObject(self._stop_event, 0) == win32event.WAIT_OBJECT_0:
                        break
                    servicemanager.LogErrorMsg(
                        f"PixelPivot child PID {proc.pid} exited unexpectedly "
                        f"(rc={proc.returncode}); stopping service."
                    )
                    win32event.SetEvent(self._stop_event)
                    return

    def _terminate_children(self) -> None:
        with self._procs_lock:
            procs = list(self._procs)
        for proc in reversed(procs):
            try:
                proc.terminate()
            except OSError:
                pass
        deadline = time.monotonic() + 15.0
        for proc in procs:
            remaining = max(0.1, deadline - time.monotonic())
            try:
                proc.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                except OSError:
                    pass
        with self._procs_lock:
            self._procs.clear()
