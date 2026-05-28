"""Pin the throttled process-tree discovery in TelemetryMonitor.

The recursive ``psutil.Process.children(recursive=True)`` walk is expensive
and the result is stable for steady-state converters (Sharp daemon,
FFmpegProcess). We re-walk at most every TELEMETRY_CHILDREN_REFRESH_S; the
per-PID sampling continues every tick to keep cpu_percent() deltas honest.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

from app.core.telemetry import TelemetryMonitor


def _fake_proc(pid: int, cpu: float = 0.0, rss_mb: float = 0.0):
    p = MagicMock()
    p.pid = pid
    p.cpu_percent.return_value = cpu
    mem = MagicMock()
    mem.rss = int(rss_mb * 1024 * 1024)
    p.memory_info.return_value = mem
    p.children.return_value = []
    return p


def test_children_walk_throttled_across_quick_ticks():
    """Two samples inside the refresh window cause exactly one tree walk."""
    monitor = TelemetryMonitor(pid=9999)
    monitor._children_refresh_s = 5.0  # long window so two ticks are inside it

    root = _fake_proc(9999, cpu=10.0, rss_mb=50.0)

    with patch("app.core.telemetry.psutil.Process", return_value=root) as proc_ctor:
        monitor._get_recursive_resources(9999)
        monitor._get_recursive_resources(9999)
        monitor._get_recursive_resources(9999)

    # The tree walk happens inside _refresh_pid_tree, which constructs one
    # psutil.Process for the root. Subsequent ticks within the refresh window
    # reuse the cached PID set and the cached Process object — no new ctor.
    assert proc_ctor.call_count == 1
    assert root.children.call_count == 1


def test_children_walk_refreshes_after_window():
    """After the refresh window elapses, the tree is re-walked exactly once."""
    monitor = TelemetryMonitor(pid=9999)
    monitor._children_refresh_s = 0.05

    root = _fake_proc(9999, cpu=10.0, rss_mb=50.0)

    with patch("app.core.telemetry.psutil.Process", return_value=root):
        monitor._get_recursive_resources(9999)
        first_walks = root.children.call_count
        time.sleep(0.07)
        monitor._get_recursive_resources(9999)

    assert root.children.call_count == first_walks + 1


def test_returns_zero_when_root_pid_vanishes():
    """A dead root PID yields (0.0, 0.0) and clears the cache."""
    import psutil

    monitor = TelemetryMonitor(pid=1)
    monitor._cached_pids = {1, 2}

    with patch(
        "app.core.telemetry.psutil.Process", side_effect=psutil.NoSuchProcess(1)
    ):
        cpu, ram = monitor._get_recursive_resources(1)

    assert (cpu, ram) == (0.0, 0.0)
    assert monitor._cached_pids == set()
    assert monitor._proc_cache == {}
