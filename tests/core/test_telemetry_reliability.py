"""E6 Telemetry Reliability — nonzero telemetry for fast/native conversions.

These tests lock in the contract that a TelemetryMonitor must yield at least
one real (nonzero) CPU/RAM sample per conversion, even when the conversion is
faster than the sampling interval. They replace the previous
``test_telemetry_fast_conversion_optimisation`` behavior, whose 200ms sampling
skip caused fast tools (vips ~40ms) to report all-zero telemetry.
"""

import subprocess
import sys
import time

from app.core.telemetry import TelemetryMonitor, aggregate_telemetry


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


# CPU-active subprocess (~150ms) standing in for a native batch (mogrify/image2).
_BURN_SRC = "import time\nx=0\nend=time.time()+0.15\nwhile time.time()<end:\n x+=1\n"


def test_native_subprocess_batch_captures_nonzero_cpu_and_ram():
    """Native-batch monitoring of a short-lived subprocess yields nonzero cpu+ram.

    Mirrors the mogrify/image2 path: the monitored PID exits before stop(), so
    the capture must come from a real live sample. interval_ms is the production
    default (250ms), larger than the subprocess lifetime, so only the producer's
    live sampling can supply CPU signal.
    """
    proc = subprocess.Popen([sys.executable, "-c", _BURN_SRC])
    monitor = TelemetryMonitor(pid=proc.pid, interval_ms=250)
    monitor.start()
    proc.wait()
    summary = monitor.stop()

    assert summary["ram_peak"] > 0
    assert summary["cpu_peak"] > 0


def test_aggregate_telemetry_does_not_collapse_when_a_summary_is_empty():
    """A real per-subprocess summary must survive aggregation alongside empty ones."""
    summaries = [
        {"cpu_avg": 5.0, "cpu_peak": 40.0, "ram_peak": 120.0},
        {},  # e.g. a chunk whose monitor produced nothing
    ]
    agg = aggregate_telemetry(summaries)
    assert agg["cpu_peak"] == 40.0
    assert agg["ram_peak"] == 120.0
    assert agg["cpu_avg"] > 0
