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
import win32job
import signal

SERVICE_NAME = "PixelPivotBatchEngine"
SERVICE_DISPLAY = "PixelPivot Batch Engine"
SERVICE_DESC = "High-throughput image conversion API and web GUI server."


def _rotate_log_file(log_path: Path, max_bytes: int = 10 * 1024 * 1024) -> None:
    """Rotate log file if it exceeds max_bytes, preserving one backup (.log.1)."""
    try:
        if log_path.exists():
            size = getattr(log_path.stat(), "st_size", 0)
            if isinstance(size, (int, float)) and size >= max_bytes:
                backup = log_path.with_suffix(".log.1")
                if backup.exists():
                    backup.unlink()
                log_path.rename(backup)
    except Exception:
        try:
            with open(log_path, "w") as f:
                f.truncate(0)
        except Exception:
            pass


def _log_dir() -> Path:
    from app.windows._settings import resolve_data_dir
    return resolve_data_dir() / "logs"


class PixelPivotService(win32serviceutil.ServiceFramework):
    _svc_name_ = SERVICE_NAME
    _svc_display_name_ = SERVICE_DISPLAY
    _svc_description_ = SERVICE_DESC

    def __init__(self, args: list[str]) -> None:
        super().__init__(args)
        self._stop_event = win32event.CreateEvent(None, 0, 0, None)
        self._shutdown_named_event_name = f"PixelPivotStop_{os.getpid()}"
        self._shutdown_named_event = win32event.CreateEvent(None, 1, 0, self._shutdown_named_event_name)
        self._procs: list[subprocess.Popen] = []
        self._procs_lock = threading.Lock()

        # Create Job Object to automatically terminate child processes if the service is killed
        self._job = win32job.CreateJobObject(None, "")
        extended_info = win32job.QueryInformationJobObject(
            self._job, win32job.JobObjectExtendedLimitInformation
        )
        extended_info["BasicLimitInformation"]["LimitFlags"] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        win32job.SetInformationJobObject(
            self._job,
            win32job.JobObjectExtendedLimitInformation,
            extended_info,
        )

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
        
        env = {
            **os.environ,
            "PIXELPIVOT_SERVICE_MODE": "1",
            "PIXELPIVOT_STOP_EVENT_NAME": self._shutdown_named_event_name,
        }

        for mode, stem in (("api", "service_api"), ("gui", "service_gui")):
            out_path = log / f"{stem}_stdout.log"
            err_path = log / f"{stem}_stderr.log"
            _rotate_log_file(out_path)
            _rotate_log_file(err_path)
            stdout = open(out_path, "a", encoding="utf-8", buffering=1)
            stderr = open(err_path, "a", encoding="utf-8", buffering=1)
            
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
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            )
            try:
                win32job.AssignProcessToJobObject(self._job, proc._handle)
            except Exception:
                pass
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
        
        # 1. Signal named stop event to initiate graceful shutdown in session 0
        if getattr(self, "_shutdown_named_event", None):
            try:
                win32event.SetEvent(self._shutdown_named_event)
            except Exception:
                pass

        # Also attempt CTRL_BREAK_EVENT
        for proc in reversed(procs):
            try:
                os.kill(proc.pid, signal.CTRL_BREAK_EVENT)
            except OSError:
                pass
        
        # 2. Wait up to grace period for graceful shutdown
        grace_s = float(os.environ.get("PIXELPIVOT_SHUTDOWN_GRACE_S", "30.0"))
        deadline = time.monotonic() + grace_s
        for proc in procs:
            remaining = max(0.1, deadline - time.monotonic())
            try:
                proc.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                # 3. Fallback to hard kill
                try:
                    proc.kill()
                except OSError:
                    pass
        
        with self._procs_lock:
            self._procs.clear()
