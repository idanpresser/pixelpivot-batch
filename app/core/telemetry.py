"""Telemetry — CPU and RAM monitoring in background threads.

TelemetryMonitor samples CPU% and RAM (MB) across a process tree in a
background producer thread, then drains the queue into batch_telemetry
via a separate consumer thread. The producer/consumer split keeps
per-tick sampling decoupled from DB I/O so a slow disk can never stall
the live converter loop.

GPU sampling (pynvml / NVML) was removed alongside ffmpeg_nvenc when the
project's deployment target moved to CPU-only servers — see the
refactor/remove-gpu-support branch. ``gpu_peak_pct`` / ``vram_peak_mb``
keys are no longer present in the dicts returned here, and the
batch_telemetry queue tuple is (run_id, timestamp, cpu_pct, ram_mb).
"""

import os
import psutil
import threading
import time
import queue
from collections import deque
from typing import Dict, List, Any, Optional
from .logger import get_logger
from .config import (
    TELEMETRY_INTERVAL,
    TELEMETRY_BATCH_SIZE,
    TELEMETRY_QUEUE_TIMEOUT,
    TELEMETRY_CHILDREN_REFRESH_S,
    TELEMETRY_MIN_SAMPLE_INTERVAL,
)

log = get_logger(__name__)


class TelemetryMonitor:
    """Monitor CPU% and RAM (MB) for the current process or a target PID tree.

    Producer thread samples; consumer thread batches inserts. Both are
    daemons so an aborted run never blocks shutdown.
    """

    def __init__(self, pid: int = None, interval_ms: int = None, run_id: int = None):
        """Initialize a telemetry monitor for process resource tracking.

        Args:
            pid: Process ID to monitor (default: os.getpid()).
            interval_ms: Sampling interval in milliseconds (default: from config).
            run_id: Batch run ID for database logging (optional).
        """
        self.target_pid = pid
        self.run_id = run_id
        actual_interval_ms = (
            interval_ms if interval_ms is not None else int(TELEMETRY_INTERVAL * 1000)
        )
        self.interval = actual_interval_ms / 1000.0
        self.is_running = False
        self.data: Dict[str, deque] = {
            "cpu_pct": deque(maxlen=1000),
            "ram_mb": deque(maxlen=1000),
        }
        self.thread = None
        self._flusher_thread = None
        self._queue = queue.Queue(maxsize=2000)
        self._proc_cache: Dict[int, psutil.Process] = {}
        self._data_lock = threading.Lock()
        # PID tree discovery is throttled: cache the descendant PID set and
        # only re-walk every TELEMETRY_CHILDREN_REFRESH_S. Per-PID sampling
        # still runs every tick to keep cpu_percent() deltas accurate.
        self._children_refresh_s = TELEMETRY_CHILDREN_REFRESH_S
        self._last_children_walk_ts = 0.0
        self._cached_pids: set[int] = set()
        self.start_time = 0.0
        # Wall-clock time of the most recent cpu_percent() read across the tree.
        # Priming at start() and every _sample() update it; stop() floors the
        # final tick against it so a fast conversion still yields a real delta.
        self._min_sample_interval = TELEMETRY_MIN_SAMPLE_INTERVAL
        self._last_cpu_read_ts = 0.0

    def _refresh_pid_tree(self, pid: int) -> None:
        """Re-walk the process tree and update the cached PID set."""
        try:
            root = psutil.Process(pid)
            children = root.children(recursive=True)
            self._cached_pids = {root.pid} | {c.pid for c in children}

            for p in [root] + children:
                if p.pid not in self._proc_cache:
                    p.cpu_percent()  # prime the delta
                    self._proc_cache[p.pid] = p

            self._last_children_walk_ts = time.monotonic()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            self._cached_pids = set()
            raise

    def _get_recursive_resources(self, pid: int) -> tuple:
        """Sum CPU and RAM across the root process and its children.

        The PID tree is re-walked only every TELEMETRY_CHILDREN_REFRESH_S
        (default 1.0 s) instead of every tick. Per-PID sampling still runs
        every tick to keep cpu_percent() deltas accurate.

        Args:
            pid: Root process ID to analyze.

        Returns:
            (total_cpu_pct, total_ram_mb) tuple.
        """
        now = time.monotonic()
        try:
            if now - self._last_children_walk_ts >= self._children_refresh_s:
                self._refresh_pid_tree(pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            self._cached_pids = set()
            self._proc_cache = {}
            return 0.0, 0.0

        new_cache: Dict[int, psutil.Process] = {}
        total_cpu = 0.0
        total_ram = 0.0

        for p_pid in self._cached_pids:
            try:
                p = self._proc_cache.get(p_pid) or psutil.Process(p_pid)
                cpu = p.cpu_percent()
                ram = p.memory_info().rss / (1024 * 1024)
                total_cpu += cpu
                total_ram += ram
                new_cache[p_pid] = p
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        self._proc_cache = new_cache
        return total_cpu, total_ram

    def _monitor(self):
        """Producer loop: samples metrics and pushes to queue.

        cpu_percent() deltas were primed in start(). The first real sample is
        taken after a short floor (not the full interval) so a short-lived
        native subprocess — which exits before stop() can sample it — still
        contributes at least one nonzero live CPU reading. Subsequent samples
        settle into the configured interval.
        """
        first_interval = min(self._min_sample_interval, self.interval)
        try:
            first = True
            while self.is_running:
                time.sleep(first_interval if first else self.interval)
                first = False
                if self.is_running:
                    self._sample()
        except Exception as e:
            log.error(f"Telemetry monitor producer thread crashed: {e}")
            self.is_running = False

    def _flush_loop(self):
        """Consumer loop: batches samples from queue and writes to DB."""
        if not self.run_id:
            return

        from .db import get_connection, insert_telemetry_batch

        batch_size = TELEMETRY_BATCH_SIZE
        buffer = []

        while True:
            try:
                sample = self._queue.get(timeout=TELEMETRY_QUEUE_TIMEOUT)
                try:
                    if sample is None:  # shutdown sentinel
                        if buffer:
                            try:
                                with get_connection() as conn:
                                    insert_telemetry_batch(conn, buffer, auto_commit=True)
                            except Exception as e:
                                log.warning(f"Final telemetry background flush failed: {e}")
                        break

                    buffer.append(sample)
                    if len(buffer) >= batch_size:
                        try:
                            with get_connection() as conn:
                                insert_telemetry_batch(conn, buffer, auto_commit=True)
                            buffer = []
                        except Exception as e:
                            log.warning(f"Telemetry background flush failed (retaining buffer): {e}")
                            # Cap buffer growth under persistent DB failure.
                            if len(buffer) > 1000:
                                buffer = buffer[-500:]
                finally:
                    self._queue.task_done()
            except queue.Empty:
                if buffer:
                    try:
                        with get_connection() as conn:
                            insert_telemetry_batch(conn, buffer, auto_commit=True)
                        buffer = []
                    except Exception as e:
                        log.warning(f"Telemetry background flush on timeout failed (retaining buffer): {e}")
                        if len(buffer) > 1000:
                            buffer = buffer[-500:]
            except Exception as e:
                log.error(f"Critical error in telemetry flusher loop: {e}")

    def _sample(self):
        """Take a single resource sample and queue for DB write.

        Every tick reads cpu_percent() so the per-PID deltas stay warm. The
        previous implementation skipped sampling for the first 200ms to save
        CPU, but that starved fast tools (vips ~40ms) of any real sample and
        left the final stop() tick as the first-ever cpu_percent() call, which
        always returns 0.0. Priming now happens in start() instead.
        """
        root_pid = self.target_pid or os.getpid()

        cpu, ram = self._get_recursive_resources(root_pid)
        self._last_cpu_read_ts = time.monotonic()

        with self._data_lock:
            if ram > 0 or not self.data["ram_mb"]:
                self.data["cpu_pct"].append(cpu)
                self.data["ram_mb"].append(ram)

        if self.run_id:
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
            try:
                self._queue.put((self.run_id, timestamp, cpu, ram), block=False)
            except queue.Full:
                log.warning("Telemetry queue is full! Dropping oldest sample to prevent memory growth.")

    def start(self):
        """Start producer and consumer threads."""
        self.data = {
            "cpu_pct": deque(maxlen=1000),
            "ram_mb": deque(maxlen=1000),
        }
        self._proc_cache = {}
        self._cached_pids = set()
        self._last_children_walk_ts = 0.0
        self.start_time = time.monotonic()
        self.is_running = True

        # Prime cpu_percent() deltas up front so the first real sample (and the
        # final stop() tick) measures against a baseline rather than returning
        # 0.0 on its first-ever call for each PID.
        root_pid = self.target_pid or os.getpid()
        try:
            self._refresh_pid_tree(root_pid)
            self._last_cpu_read_ts = time.monotonic()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            self._last_cpu_read_ts = 0.0

        self.thread = threading.Thread(target=self._monitor, daemon=True)
        self.thread.start()

        if self.run_id:
            self._flusher_thread = threading.Thread(target=self._flush_loop, daemon=True)
            self._flusher_thread.start()

    def stop(self) -> Dict[str, Any]:
        """Stop monitoring and return final resource statistics summary.

        Returns:
            Dict with keys: cpu_avg, cpu_peak, ram_peak.
        """
        if not self.is_running:
            return self._get_summary()

        self.is_running = False
        if self.thread:
            self.thread.join(timeout=1.0)

        # Floor the final measurement window: if the whole conversion was
        # faster than the minimum interval, the last cpu_percent() delta would
        # be near-zero. Sleep the remainder so the final tick reflects real CPU.
        if self._last_cpu_read_ts:
            elapsed = time.monotonic() - self._last_cpu_read_ts
            if elapsed < self._min_sample_interval:
                time.sleep(self._min_sample_interval - elapsed)

        self._sample()  # final tick for completeness

        self._queue.put(None)  # signal consumer to drain
        if self._flusher_thread:
            self._flusher_thread.join(timeout=5.0)

        return self._get_summary()

    def _get_summary(self) -> Dict[str, Any]:
        """Calculate resource statistics from captured telemetry data.

        Returns:
            Dict with keys: cpu_avg, cpu_peak, ram_peak.
        """
        with self._data_lock:
            cpu = list(self.data["cpu_pct"])
            ram = list(self.data["ram_mb"])

        return {
            "cpu_avg": sum(cpu) / len(cpu) if cpu else 0,
            "cpu_peak": max(cpu) if cpu else 0,
            "ram_peak": max(ram) if ram else 0,
        }


def aggregate_telemetry(summaries: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate multiple per-file telemetry summaries into batch-level statistics.

    Args:
        summaries: List of telemetry summary dicts from individual conversions.

    Returns:
        Aggregated batch-level dict with peak and average resource usage.
    """
    if not summaries:
        return {}

    aggregated = {
        "cpu_avg": 0.0,
        "cpu_peak": 0.0,
        "ram_peak": 0.0,
    }

    valid_summaries = [s for s in summaries if s]
    if not valid_summaries:
        return aggregated

    cpu_sum = 0.0
    for s in valid_summaries:
        try:
            aggregated["cpu_peak"] = max(aggregated["cpu_peak"], float(s.get("cpu_peak", 0.0)))
            aggregated["ram_peak"] = max(aggregated["ram_peak"], float(s.get("ram_peak", 0.0)))
            cpu_sum += float(s.get("cpu_avg", 0.0))
        except (TypeError, ValueError):
            continue

    aggregated["cpu_avg"] = cpu_sum / len(valid_summaries)
    return aggregated
