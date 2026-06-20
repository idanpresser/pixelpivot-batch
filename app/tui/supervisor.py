# app/tui/supervisor.py
"""ProcessSupervisor — spawn, stop, restart, and tail child processes.

The TUI is the parent of the FastAPI API and (on demand) the sharp node daemon.
Each child's stdout/stderr is drained by a reader thread into a bounded, tagged
ring buffer that the log panel renders.
"""
from __future__ import annotations

import subprocess
import threading
import time
from collections import deque
from typing import Deque, Dict, List, Optional

import httpx


class ProcessSupervisor:
    """Manages named child processes and a merged, bounded log ring buffer."""

    def __init__(self, log_capacity: int = 2000) -> None:
        self._procs: Dict[str, subprocess.Popen] = {}
        self._threads: Dict[str, threading.Thread] = {}
        self._logs: Deque[str] = deque(maxlen=log_capacity)
        self._lock = threading.Lock()

    def start(self, name: str, cmd: List[str]) -> None:
        """Spawn a named child and begin draining its output."""
        if self._procs.get(name) and self._procs[name].poll() is None:
            return  # already running
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
        )
        self._procs[name] = proc
        t = threading.Thread(target=self._drain, args=(name, proc), daemon=True)
        t.start()
        self._threads[name] = t

    def _drain(self, name: str, proc: subprocess.Popen) -> None:
        if proc.stdout is None:
            return
        tag = name.upper()
        for line in proc.stdout:
            with self._lock:
                self._logs.append(f"[{tag}] {line.rstrip()}")

    def stop(self, name: str, timeout: float = 5.0) -> None:
        """Terminate a named child, escalating to kill on timeout."""
        proc = self._procs.get(name)
        if not proc:
            return
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=timeout)

    def restart(self, name: str, cmd: List[str]) -> None:
        self.stop(name)
        self.start(name, cmd)

    def status(self) -> Dict[str, str]:
        """Return {name: 'running'|'stopped'} for every known child."""
        out: Dict[str, str] = {}
        for name, proc in self._procs.items():
            out[name] = "running" if proc.poll() is None else "stopped"
        return out

    def get_logs(self) -> List[str]:
        with self._lock:
            return list(self._logs)

    def wait_ready(self, url: str, timeout: float = 15.0, interval: float = 0.25) -> bool:
        """Poll an HTTP URL until it returns 200 or the timeout elapses."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if httpx.get(url, timeout=interval).status_code == 200:
                    return True
            except Exception:
                pass
            time.sleep(interval)
        return False

    def shutdown(self) -> None:
        for name in list(self._procs):
            self.stop(name)
