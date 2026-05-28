"""Telemetry — resource monitoring (CPU, RAM, GPU) in background threads.

TelemetryMonitor samples CPU, RAM, and GPU utilization from a process tree
in a background producer thread, decoupling sampling from DB I/O via a queue-based
consumer thread for stability under load.
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
    TELEMETRY_GPU_FAIL_LIMIT,
)

log = get_logger(__name__)

try:
    from pynvml import (
        nvmlInit,
        nvmlDeviceGetHandleByIndex,
        nvmlDeviceGetUtilizationRates,
        nvmlDeviceGetMemoryInfo,
    )

    nvmlInit()
    HAS_GPU = True
except ImportError:
    HAS_GPU = False
    nvmlInit = None
    nvmlDeviceGetHandleByIndex = None
    nvmlDeviceGetUtilizationRates = None
    nvmlDeviceGetMemoryInfo = None
    log.debug("nvidia-ml-py not found; GPU telemetry disabled.")
except Exception as e:
    HAS_GPU = False
    nvmlInit = None
    nvmlDeviceGetHandleByIndex = None
    nvmlDeviceGetUtilizationRates = None
    nvmlDeviceGetMemoryInfo = None
    log.error(f"Failed to initialize nvidia-ml-py: {e}")


class TelemetryMonitor:
    """
    Monitors CPU, RAM, and GPU utilisation in a background thread.
    Can monitor the current process or a specific target PID (including children).
    Decouples sampling from DB I/O using a producer-consumer queue for stability.
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
        # Sync with global config if not explicitly provided
        actual_interval_ms = (
            interval_ms if interval_ms is not None else int(TELEMETRY_INTERVAL * 1000)
        )
        self.interval = actual_interval_ms / 1000.0
        self.is_running = False
        self.data: Dict[str, deque] = {
            "cpu_pct": deque(maxlen=1000),
            "ram_mb": deque(maxlen=1000),
            "gpu_pct": deque(maxlen=1000),
            "vram_mb": deque(maxlen=1000),
        }
        self.thread = None
        self._flusher_thread = None
        self._queue = queue.Queue(maxsize=2000)
        self._proc_cache: Dict[int, psutil.Process] = {}
        self._data_lock = threading.Lock()
        # PID tree discovery is throttled: cache the set of descendant PIDs and
        # only re-walk every TELEMETRY_CHILDREN_REFRESH_S. Sampling per PID still
        # runs every tick.
        self._children_refresh_s = TELEMETRY_CHILDREN_REFRESH_S
        self._last_children_walk_ts = 0.0
        self._cached_pids: set[int] = set()

        self.gpu_available = HAS_GPU
        self._gpu_consecutive_fails = 0
        if self.gpu_available:
            try:
                nvmlInit()
            except Exception as e:
                log.error(f"nvidia-ml-py re-initialization failed: {e}")
                self.gpu_available = False

    def _refresh_pid_tree(self, pid: int) -> None:
        """Re-walk the process tree and update the cached PID set."""
        try:
            root = psutil.Process(pid)
            children = root.children(recursive=True)
            self._cached_pids = {root.pid} | {c.pid for c in children}

            # Seed the cache with all processes and prime their CPU deltas
            for p in [root] + children:
                if p.pid not in self._proc_cache:
                    p.cpu_percent() # Prime
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
        """Producer loop: samples metrics and pushes to queue."""
        # Initial sample to prime the cpu_percent() deltas in cached objects
        self._sample()

        try:
            while self.is_running:
                time.sleep(self.interval)
                self._sample()
        except Exception as e:
            log.error(f"Telemetry monitor producer thread crashed: {e}")
            self.is_running = False

    def _flush_loop(self):
        """Consumer loop: batches samples from queue and writes to DB."""
        if not self.run_id:
            return

        from .db import get_connection, insert_telemetry_batch

        batch_size = TELEMETRY_BATCH_SIZE  # Flush every N samples or when sentinel received
        buffer = []

        while True:
            try:
                # Wait for data or timeout to allow periodic flushing even if low volume
                sample = self._queue.get(timeout=TELEMETRY_QUEUE_TIMEOUT)
                try:
                    if sample is None:  # Sentinel for shutdown
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
                            # Limit the buffer size to avoid unbounded memory growth under permanent DB failure
                            if len(buffer) > 1000:
                                buffer = buffer[-500:]
                finally:
                    self._queue.task_done()
            except queue.Empty:
                # Periodic flush if we have anything in buffer
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
        """Take a single resource sample and queue for DB write."""
        root_pid = self.target_pid or os.getpid()
        cpu, ram = self._get_recursive_resources(root_pid)

        gpu = 0.0
        vram = 0.0

        if self.gpu_available:
            try:
                handle = nvmlDeviceGetHandleByIndex(0)
                util = nvmlDeviceGetUtilizationRates(handle)
                mem = nvmlDeviceGetMemoryInfo(handle)
                gpu = float(util.gpu)
                vram = mem.used / 1024 / 1024
                self._gpu_consecutive_fails = 0
            except Exception as e:
                self._gpu_consecutive_fails += 1
                log.debug(
                    f"Failed to sample GPU (consecutive fails={self._gpu_consecutive_fails}): {e}"
                )
                if self._gpu_consecutive_fails >= TELEMETRY_GPU_FAIL_LIMIT:
                    log.warning(
                        f"Disabling GPU telemetry for this run after {TELEMETRY_GPU_FAIL_LIMIT} consecutive failures."
                    )
                    self.gpu_available = False

        # Update local statistics for stop() summary
        with self._data_lock:
            if ram > 0 or not self.data["ram_mb"]:
                self.data["cpu_pct"].append(cpu)
                self.data["ram_mb"].append(ram)
                self.data["gpu_pct"].append(gpu)
                self.data["vram_mb"].append(vram)

        # Queue for persistent storage if run_id is active (with safety cap try-except)
        if self.run_id:
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
            try:
                self._queue.put((self.run_id, timestamp, cpu, ram, gpu, vram), block=False)
            except queue.Full:
                log.warning("Telemetry queue is full! Dropping oldest sample to prevent memory growth.")

    def start(self):
        """Start producer and consumer threads for telemetry collection and DB flushing."""
        self.data = {
            "cpu_pct": deque(maxlen=1000),
            "ram_mb": deque(maxlen=1000),
            "gpu_pct": deque(maxlen=1000),
            "vram_mb": deque(maxlen=1000),
        }
        self._proc_cache = {}
        self._cached_pids = set()
        self._last_children_walk_ts = 0.0
        self.is_running = True

        # Run producer thread
        self.thread = threading.Thread(target=self._monitor, daemon=True)
        self.thread.start()

        # Run consumer thread ONLY if run_id is provided
        if self.run_id:
            self._flusher_thread = threading.Thread(target=self._flush_loop, daemon=True)
            self._flusher_thread.start()

    def stop(self) -> Dict[str, Any]:
        """Stop monitoring and return final resource statistics summary.

        Returns:
            Dict with keys: cpu_avg, cpu_peak, ram_peak, gpu_peak, vram_peak_mb.
        """
        if not self.is_running:
            return self._get_summary()

        # Stop the producer loop
        self.is_running = False
        if self.thread:
            self.thread.join(timeout=1.0)

        # Final sample for completeness
        self._sample()

        # Signal the consumer to drain and exit
        self._queue.put(None)
        if self._flusher_thread:
            # Flusher might involve DB I/O, give it a bit more time
            self._flusher_thread.join(timeout=5.0)

        return self._get_summary()

    def _get_summary(self) -> Dict[str, Any]:
        """Calculate resource statistics from captured telemetry data.

        Returns:
            Dict with keys: cpu_avg, cpu_peak, ram_peak, gpu_peak, vram_peak_mb.
        """
        with self._data_lock:
            cpu = list(self.data["cpu_pct"])
            ram = list(self.data["ram_mb"])
            gpu = list(self.data["gpu_pct"])
            vram = list(self.data["vram_mb"])

        return {
            "cpu_avg": sum(cpu) / len(cpu) if cpu else 0,
            "cpu_peak": max(cpu) if cpu else 0,
            "ram_peak": max(ram) if ram else 0,
            "gpu_peak": max(gpu) if gpu else 0,
            "vram_peak_mb": max(vram) if vram else 0,
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
        "gpu_peak": 0.0,
        "vram_peak_mb": 0.0,
    }

    valid_summaries = [s for s in summaries if s]
    if not valid_summaries:
        return aggregated

    cpu_sum = 0.0
    for s in valid_summaries:
        try:
            aggregated["cpu_peak"] = max(aggregated["cpu_peak"], float(s.get("cpu_peak", 0.0)))
            aggregated["ram_peak"] = max(aggregated["ram_peak"], float(s.get("ram_peak", 0.0)))
            aggregated["gpu_peak"] = max(aggregated["gpu_peak"], float(s.get("gpu_peak", 0.0)))
            aggregated["vram_peak_mb"] = max(
                aggregated["vram_peak_mb"], float(s.get("vram_peak_mb", 0.0))
            )
            cpu_sum += float(s.get("cpu_avg", 0.0))
        except (TypeError, ValueError):
            continue

    aggregated["cpu_avg"] = cpu_sum / len(valid_summaries)
    return aggregated
