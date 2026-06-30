# app/core/process_registry.py
"""Thread-safe registry of live child subprocess.Popen handles.

Every encoder spawn site registers its Popen here and unregisters on exit, so a
graceful shutdown can terminate()/kill() any child that outlives a joined worker
thread instead of orphaning ffmpeg/mogrify processes that hold FDs or leave
partial output files.
"""
from __future__ import annotations

import subprocess
import threading
from typing import Set

from .config import SUBPROCESS_TERMINATE_TIMEOUT_S
from .logger import get_logger

log = get_logger(__name__)

_lock = threading.Lock()
_live: "Set[subprocess.Popen]" = set()


def register_process(proc: "subprocess.Popen") -> None:
    """Track a freshly spawned child process."""
    with _lock:
        _live.add(proc)


def unregister_process(proc: "subprocess.Popen") -> None:
    """Stop tracking a process that has finished normally."""
    with _lock:
        _live.discard(proc)


def snapshot() -> "Set[subprocess.Popen]":
    """Return a copy of the currently tracked processes."""
    with _lock:
        return set(_live)


def clear() -> None:
    """Drop all tracked handles without signalling them (tests / reset)."""
    with _lock:
        _live.clear()


def terminate_all(grace_s: float = SUBPROCESS_TERMINATE_TIMEOUT_S) -> int:
    """terminate() every live child, then kill() any that ignore the grace window.

    Returns the number of processes that were still running and got signalled.
    """
    procs = snapshot()
    signalled = 0
    for p in procs:
        if p.poll() is not None:
            unregister_process(p)
            continue
        signalled += 1
        try:
            p.terminate()
        except Exception as e:
            log.warning("terminate() failed for pid=%s: %s", getattr(p, "pid", "?"), e)
    for p in procs:
        if p.poll() is None:
            try:
                p.wait(timeout=grace_s)
            except Exception:
                try:
                    p.kill()
                    log.warning("killed surviving child pid=%s after grace window", p.pid)
                except Exception as e:
                    log.error("kill() failed for pid=%s: %s", getattr(p, "pid", "?"), e)
        unregister_process(p)
    return signalled
