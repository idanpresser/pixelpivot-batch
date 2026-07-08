"""Shared settings map for tray (writer) and service_main (reader).

No Qt or service dependencies — safe to import from either exe.
"""
from __future__ import annotations

from typing import Any

SETTINGS_DEFAULTS: dict[str, Any] = {
    "concurrent_encodes_scaling_factor": 2.0,
    "concurrent_encodes_max_workers":    None,
    "chunk_ram_fraction":                0.25,
    "disk_backpressure_pct":             90.0,
    "shutdown_grace_s":                  30.0,
    "calibration_enabled":               False,
    "batch_fatal_abort_threshold":       3,
    "image2_allow_lossy":                False,
    "metrics_enabled":                   True,
    "queue_poll_s":                      0.5,
}

SETTINGS_ENV_MAP: dict[str, str] = {
    "concurrent_encodes_scaling_factor": "PIXELPIVOT_CONCURRENT_ENCODES_SCALING_FACTOR",
    "concurrent_encodes_max_workers":    "PIXELPIVOT_CONCURRENT_ENCODES_MAX_WORKERS",
    "chunk_ram_fraction":                "PIXELPIVOT_CHUNK_RAM_FRACTION",
    "disk_backpressure_pct":             "PIXELPIVOT_DISK_BACKPRESSURE_PCT",
    "shutdown_grace_s":                  "PIXELPIVOT_SHUTDOWN_GRACE_S",
    "calibration_enabled":               "PIXELPIVOT_CALIBRATION_ENABLED",
    "batch_fatal_abort_threshold":       "PIXELPIVOT_BATCH_FATAL_ABORT_THRESHOLD",
    "image2_allow_lossy":                "PIXELPIVOT_IMAGE2_ALLOW_LOSSY",
    "metrics_enabled":                   "PIXELPIVOT_METRICS_ENABLED",
    "queue_poll_s":                      "PIXELPIVOT_QUEUE_POLL_S",
}
