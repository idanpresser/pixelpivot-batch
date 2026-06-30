# app/batch_api/health.py
"""Stateless health-probe helpers for /healthz endpoints.

Pure functions with no FastAPI imports so they are trivially unit-testable.
Liveness = "process is up" (no dependencies). Readiness = "can do work"
(DB connect, storage writable, encoders reachable).
"""
from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from typing import List

from app.core.db.connection import get_connection
from app.core import toolcheck


@dataclass(frozen=True)
class Check:
    """One named readiness probe result."""
    name: str
    ok: bool
    detail: str = ""


LIVE_BODY = {"status": "alive"}


def _data_dir() -> str:
    db_path = os.getenv("PIXELPIVOT_DB_PATH", os.path.join(".", "data", "pixelpivot.db"))
    return os.path.dirname(os.path.abspath(db_path)) or "."


def _check_db() -> Check:
    try:
        with get_connection() as conn:
            conn.execute("SELECT 1")
        return Check("db", True, "connected")
    except Exception as e:
        return Check("db", False, str(e))


def _check_storage() -> Check:
    d = _data_dir()
    try:
        os.makedirs(d, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=d, prefix=".healthz_")
        os.close(fd)
        os.remove(tmp)
        return Check("storage", True, d)
    except Exception as e:
        return Check("storage", False, f"{d}: {e}")


def readiness_checks(orchestrator) -> List[Check]:
    """Run every readiness probe and return their results, order stable."""
    convs = getattr(orchestrator, "converters", {})
    magick_path = getattr(convs.get("magick"), "magick_path", "magick")
    ffmpeg_path = getattr(convs.get("ffmpeg"), "ffmpeg_path", "ffmpeg")
    sharp_port = getattr(convs.get("sharp"), "port", 8765)

    def _from_status(name, status):
        return Check(name, status.ok, status.detail or "")

    return [
        _check_db(),
        _check_storage(),
        _from_status("magick", toolcheck.check_binary("magick", magick_path)),
        _from_status("ffmpeg", toolcheck.check_binary("ffmpeg", ffmpeg_path)),
        _from_status("sharp", toolcheck.check_sharp_daemon(sharp_port)),
    ]
