# app/batch_api/metrics.py
"""Prometheus metrics for PixelPivot. Degradable: recording no-ops when disabled
or when prometheus_client is unavailable (air-gapped host without the package).
"""
from __future__ import annotations

from ..core.config import METRICS_ENABLED
from ..core.logger import get_logger

log = get_logger(__name__)

_ENABLED = METRICS_ENABLED
_registry = None
_jobs = None
_processing = None
_queue_depth = None
_compression = None

try:
    if _ENABLED:
        from prometheus_client import Counter, Gauge, Histogram, CollectorRegistry, generate_latest
        _registry = CollectorRegistry()
        _jobs = Counter("pixelpivot_jobs_total", "Batch conversions by outcome",
                        ["status", "tool", "format"], registry=_registry)
        _processing = Histogram("pixelpivot_processing_seconds", "Batch wall time (s)", registry=_registry)
        _queue_depth = Gauge("pixelpivot_queue_depth", "Queued batch runs", registry=_registry)
        _compression = Histogram("pixelpivot_compression_ratio", "output_bytes / input_bytes", registry=_registry)
        _generate_latest = generate_latest
except Exception as e:  # prometheus_client missing or import error
    log.warning("Metrics disabled (prometheus_client unavailable): %s", e)
    _ENABLED = False


def record_job(status: str, tool: str, fmt: str) -> None:
    if _ENABLED and _jobs is not None:
        _jobs.labels(status=status, tool=tool, format=fmt).inc()


def observe_processing_seconds(seconds: float) -> None:
    if _ENABLED and _processing is not None:
        _processing.observe(seconds)


def set_queue_depth(n: int) -> None:
    if _ENABLED and _queue_depth is not None:
        _queue_depth.set(n)


def observe_compression_ratio(ratio: float) -> None:
    if _ENABLED and _compression is not None:
        _compression.observe(ratio)


def render() -> bytes:
    """Return the Prometheus exposition payload (empty when disabled)."""
    if _ENABLED and _registry is not None:
        return _generate_latest(_registry)
    return b"# metrics disabled\n"
