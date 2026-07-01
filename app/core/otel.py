# app/core/otel.py
"""Optional OpenTelemetry spans (e5.5).

Default off (PIXELPIVOT_OTEL_ENABLED=0): span() is a zero-overhead no-op and the
opentelemetry package is never imported. When enabled, the SDK/tracer is imported
lazily on first use; if the package is unavailable, it degrades back to a no-op.
"""
from __future__ import annotations

import os
from contextlib import contextmanager

_ENABLED = os.getenv("PIXELPIVOT_OTEL_ENABLED", "0") not in ("0", "false", "False")
_tracer = None
_init_failed = False


def _get_tracer():
    global _tracer, _init_failed
    if _tracer is not None or _init_failed:
        return _tracer
    try:
        from opentelemetry import trace  # imported only when enabled + first use
        _tracer = trace.get_tracer("pixelpivot")
    except Exception:
        _init_failed = True
        _tracer = None
    return _tracer


@contextmanager
def span(name: str):
    """Enter a tracing span named `name`, or a no-op context when disabled/unavailable."""
    if not _ENABLED:
        yield None
        return
    tracer = _get_tracer()
    if tracer is None:
        yield None
        return
    with tracer.start_as_current_span(name) as s:
        yield s
