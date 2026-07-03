"""E6 Telemetry Reliability — nonzero telemetry for fast/native conversions.

These tests lock in the contract that a TelemetryMonitor must yield at least
one real (nonzero) CPU/RAM sample per conversion, even when the conversion is
faster than the sampling interval. They replace the previous
``test_telemetry_fast_conversion_optimisation`` behavior, whose 200ms sampling
skip caused fast tools (vips ~40ms) to report all-zero telemetry.
"""

import time

from app.core.telemetry import TelemetryMonitor


def _burn_cpu(seconds: float) -> int:
    """Keep the current process busy for `seconds` so cpu_percent() is nonzero."""
    end = time.monotonic() + seconds
    x = 0
    while time.monotonic() < end:
        x += 1
    return x


def test_fast_conversion_yields_nonzero_cpu_and_ram():
    """A sub-interval CPU-active conversion still reports cpu_avg>0 and ram_peak>0.

    The interval is set far larger than the workload so the producer loop takes
    no intermediate tick — only start() priming and the stop() final tick run.
    This is exactly the fast-tool path that regressed to all-zero telemetry.
    """
    monitor = TelemetryMonitor(interval_ms=5000)
    monitor.start()
    _burn_cpu(0.12)  # fast (<200ms) but CPU-active conversion
    summary = monitor.stop()

    assert summary["ram_peak"] > 0
    assert summary["cpu_avg"] > 0
    assert summary["cpu_peak"] > 0
