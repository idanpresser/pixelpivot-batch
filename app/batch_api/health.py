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


@dataclass(frozen=True)
class Check:
    """One named readiness probe result."""
    name: str
    ok: bool
    detail: str = ""


LIVE_BODY = {"status": "alive"}
