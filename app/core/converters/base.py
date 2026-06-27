"""Abstract base class and telemetry harness for image converters."""

from abc import ABC, abstractmethod
import subprocess
import sys
import time
import os
import psutil
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, Any, List, Union, Optional
from dataclasses import dataclass, field
from ..telemetry import TelemetryMonitor, aggregate_telemetry
from ..logger import get_logger

@dataclass
class ConvertResult:
    """Standardized return structure for a single file conversion."""
    success: bool
    error: Optional[str] = None
    duration_ms: float = 0.0
    telemetry: Dict[str, Any] = field(default_factory=dict)
    parameters_used: Dict[str, Any] = field(default_factory=dict)
    fatal_error: bool = False
    bytes_written: int = 0
    total_overhead_ms: Optional[float] = None

    def __getitem__(self, key: str) -> Any:
        try:
            return getattr(self, key)
        except AttributeError:
            raise KeyError(key)

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)

    def __contains__(self, key: str) -> bool:
        return hasattr(self, key)


@dataclass
class BatchResult:
    """Standardized return structure for a batch of conversions."""
    success_count: int
    failure_count: int
    duration_ms: float
    telemetry: Dict[str, Any]
    errors: List[Dict[str, Any]]
    bytes_written: int = 0

    def __getitem__(self, key: str) -> Any:
        try:
            return getattr(self, key)
        except AttributeError:
            raise KeyError(key)

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)

    def __contains__(self, key: str) -> bool:
        return hasattr(self, key)
from ..utils import kill_process_tree
from ..config import (
    FFMPEG_TIMEOUT,
    TELEMETRY_INTERVAL,
    MAX_LOG_BUFFER,
    CONCURRENT_ENCODES_SCALING_FACTOR,
    CONCURRENT_ENCODES_MIN_RAM_MB,
    CONCURRENT_ENCODES_MAX_WORKERS,
)

log = get_logger(__name__)


def _win32_safe_path(path: str) -> str:
    """Prefix absolute Windows paths with \\\\?\\ to bypass the 260-char MAX_PATH limit.

    UNC paths (\\\\\\\\server\\\\share\\\\...) become \\\\\\\\?\\\\UNC\\\\server\\\\share\\\\...
    Already-prefixed paths and relative paths are returned unchanged.
    Non-Windows platforms receive the input unchanged.
    """
    if sys.platform != "win32":
        return path
    from pathlib import PureWindowsPath
    import os as _os
    if not _os.path.isabs(path):
        return path
    s = str(PureWindowsPath(path))
    if s.startswith("\\\\?\\"):
        return s
    if s.startswith("\\\\"):
        return "\\\\?\\UNC\\" + s[2:]
    return "\\\\?\\" + s


def _truncate(s: str | None, limit: int = 2048) -> str | None:
    if not s:
        return s
    return s if len(s) <= limit else s[:limit] + f"... ({len(s) - limit} bytes truncated)"


# Conservative per-pixel working-set estimate for an in-process decode+encode:
# a decoded RGBA frame is 4 bytes/px; encoders typically hold ~3x that in
# intermediate buffers. Tunable via env for unusual workloads.
_WORKER_BYTES_PER_PX = int(os.getenv("PIXELPIVOT_WORKER_BYTES_PER_PX", str(4 * 3)))
# Fraction of currently-available RAM we are willing to commit to frame buffers.
_WORKER_RAM_HEADROOM = float(os.getenv("PIXELPIVOT_WORKER_RAM_HEADROOM", "0.7"))
# Frame size assumed when dimensions are unknown, so missing dims never grant
# unbounded concurrency (this gap caused the system-wide OOM). ~24 MP default.
_WORKER_UNKNOWN_MP = float(os.getenv("PIXELPIVOT_WORKER_UNKNOWN_MP", "24.0"))


def memory_aware_worker_cap(
    base_workers: int,
    dimensions: dict | None,
    input_paths: list,
    available_ram_mb: float,
) -> int:
    """Bound worker count so concurrent decoded frames fit in available RAM.

    The CPU-derived worker count ignores per-image footprint. With large frames
    and many workers, all decodes land at once and exhaust RAM *after* the
    one-time availability check. This caps workers by projected peak memory,
    using the largest frame in the batch. Missing dimensions are treated as a
    conservative default frame so an unprobed batch can never grant full
    concurrency. Never returns below 1, never above base_workers.
    """
    if base_workers <= 1:
        return max(1, base_workers)

    dims = dimensions or {}
    max_area = 0
    for p in input_paths:
        wh = dims.get(p)
        if wh and wh[0] and wh[1]:
            max_area = max(max_area, wh[0] * wh[1])
    if max_area <= 0:
        # No usable dims for any input: assume a conservative frame (fail-safe).
        max_area = int(_WORKER_UNKNOWN_MP * 1_000_000)

    frame_mb = (max_area * _WORKER_BYTES_PER_PX) / (1024 * 1024)
    if frame_mb <= 0:
        return base_workers

    affordable = int((available_ram_mb * _WORKER_RAM_HEADROOM) / frame_mb)
    return max(1, min(base_workers, affordable))


class BaseConverter(ABC):
    """Abstract base class defining the converter interface and circuit-breaker pattern.

    Subclasses must implement get_name(), supported_formats(), and convert().
    The default convert_batch() uses ThreadPoolExecutor; subclasses override it
    for efficiency (e.g., FFmpegConverter uses hybrid image2 + multimap paths).
    """
    def __init__(self):
        self._breaker_lock = threading.Lock()
        self._breaker_states: Dict[Optional[int], Dict[str, Any]] = {}
        self._local = threading.local()
        self.failure_threshold = 3
        self.cooldown_period = 30.0  # seconds self-healing cooldown

    def _set_active_run_id(self, run_id: Optional[int]):
        self._local.run_id = run_id

    def _get_active_run_id(self) -> Optional[int]:
        return getattr(self._local, "run_id", None)

    def _get_state(self) -> Dict[str, Any]:
        run_id = self._get_active_run_id()
        with self._breaker_lock:
            if run_id not in self._breaker_states:
                self._breaker_states[run_id] = {
                    "consecutive_failures": 0,
                    "is_broken": False,
                    "broken_since": None
                }
            return self._breaker_states[run_id]

    @property
    def consecutive_failures(self) -> int:
        global_failures = self._breaker_states.get(None, {}).get("consecutive_failures", 0)
        if global_failures > 0:
            return global_failures
        return self._get_state()["consecutive_failures"]

    @consecutive_failures.setter
    def consecutive_failures(self, val: int):
        self._get_state()["consecutive_failures"] = val

    @property
    def is_broken(self) -> bool:
        if self._breaker_states.get(None, {}).get("is_broken"):
            return True
        return self._get_state()["is_broken"]

    @is_broken.setter
    def is_broken(self, val: bool):
        self._get_state()["is_broken"] = val

    @property
    def broken_since(self) -> Optional[float]:
        global_broken_since = self._breaker_states.get(None, {}).get("broken_since")
        if global_broken_since is not None:
            return global_broken_since
        return self._get_state()["broken_since"]

    @broken_since.setter
    def broken_since(self, val: Optional[float]):
        self._get_state()["broken_since"] = val

    @property
    def _bypass_breaker(self) -> bool:
        run_id = self._get_active_run_id()
        with self._breaker_lock:
            state = self._breaker_states.get(run_id)
            if state:
                return state.get("bypass_breaker", False)
            global_state = self._breaker_states.get(None)
            if global_state:
                return global_state.get("bypass_breaker", False)
            return False

    @_bypass_breaker.setter
    def _bypass_breaker(self, val: bool):
        run_id = self._get_active_run_id()
        with self._breaker_lock:
            if run_id not in self._breaker_states:
                self._breaker_states[run_id] = {
                    "consecutive_failures": 0,
                    "is_broken": False,
                    "broken_since": None,
                    "bypass_breaker": False
                }
            self._breaker_states[run_id]["bypass_breaker"] = val

    def _mark_failure(self):
        state = self._get_state()
        state["consecutive_failures"] += 1
        threshold = 10 if self.get_name() == "ffmpeg" else self.failure_threshold
        if state["consecutive_failures"] >= threshold:
            if not state["is_broken"]:
                state["is_broken"] = True
                state["broken_since"] = time.time()
                log.error(f"  [CIRCUIT BREAKER] {self.get_name()} is now marked as BROKEN after {state['consecutive_failures']} failures.")

    def _reset_failures(self):
        state = self._get_state()
        state["consecutive_failures"] = 0
        state["is_broken"] = False
        state["broken_since"] = None
        # Reset default/global state as well to prevent cross-test/bleed leftovers
        if self._get_active_run_id() is not None:
            global_state = self._breaker_states.get(None)
            if global_state:
                global_state["consecutive_failures"] = 0
                global_state["is_broken"] = False
                global_state["broken_since"] = None

    def _account_native_batch(self, *, failed: bool) -> None:
        """Drive the circuit breaker from one native-batch chunk's net outcome."""
        if failed:
            self._mark_failure()
        else:
            self._reset_failures()

    @abstractmethod
    def get_name(self) -> str:
        """Return the human-readable tool name."""
        pass

    @abstractmethod
    def supported_formats(self) -> List[str]:
        """Return list of formats this converter can produce."""
        pass

    @abstractmethod
    def convert(
        self,
        input_path: str,
        output_path: str,
        target_format: str,
        quality: Union[int, float],
        is_intermediate: bool = False,
        run_id: Optional[int] = None,
    ) -> ConvertResult:
        """Convert a single image file.

        Args:
            input_path: Path to input image.
            output_path: Path where output should be written.
            target_format: Output format (e.g., 'webp', 'avif', 'jxl').
            quality: Format-native quality value. Higher values indicate better quality.
            is_intermediate: Hint that this is a calibration encode (may optimize speed over quality).
            run_id: Optional batch run ID for telemetry tracking.

        Returns:
            ConvertResult containing success status, duration, telemetry, parameters used, error, and fatal status.
        """
        pass

    def _run_subprocess(
        self,
        cmd: List[str],
        tool_name: str,
        params: List[str],
        quality: Union[int, float],
        run_id: Optional[int] = None,
        output_path: Optional[str] = None,
    ) -> ConvertResult:
        """Execute a subprocess command with telemetry capture and circuit-breaker logic.

        Handles process lifecycle (timeout, cleanup), captures telemetry and stderr,
        detects fatal errors, and updates the circuit breaker state.

        Args:
            cmd: Full command argv including binary path.
            tool_name: Human-readable tool name for logging (e.g., 'ImageMagick').
            params: Encoder-specific parameters for logging and result tracking.
            quality: Quality value used (for result tracking).
            run_id: Optional batch run ID for telemetry association.

        Returns:
            ConvertResult containing success status, duration, telemetry, parameters used, error, and fatal status.
        """
        self._set_active_run_id(run_id)
        # Circuit Breaker with 30s self-healing cooldown bypass
        if self.is_broken and not getattr(self, "_bypass_breaker", False):
            if self.broken_since and (time.time() - self.broken_since) > self.cooldown_period:
                log.warning(f"Cooldown period elapsed. Retrying broken converter: {self.get_name()}")
                self._reset_failures()
            else:
                return ConvertResult(success=False, error=f"{tool_name} is marked as broken (too many failures).")

        start = time.time()
        monitor = None
        try:
            log.debug(f"Running {tool_name} subprocess: {' '.join(cmd)}")
            creationflags = (
                subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            )
            with subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                creationflags=creationflags,
            ) as proc:
                monitor = TelemetryMonitor(
                    pid=proc.pid, interval_ms=int(TELEMETRY_INTERVAL * 1000), run_id=run_id
                )
                monitor.start()
                error = None

                try:
                    stdout, stderr = proc.communicate(timeout=FFMPEG_TIMEOUT)
                    success = proc.returncode == 0
                    if success:
                        target_out = cmd[-1]
                        if not os.path.exists(target_out) or os.path.getsize(target_out) == 0:
                            success = False
                            error = f"{tool_name} claimed success but output file is missing or empty: {target_out}"
                    error = _truncate(stderr) if not success and not error else error
                except subprocess.TimeoutExpired:
                    log.warning(f"{tool_name} timed out, force cleaning process tree...")
                    kill_process_tree(proc.pid)
                    proc.communicate()
                    success = False
                    error = f"{tool_name} timed out after {FFMPEG_TIMEOUT} seconds."
                    log.error(error)
        except Exception as e:
            success = False
            error = str(e)
            log.error(f"{tool_name} conversion error: {error}")
        finally:
            duration_ms = (time.time() - start) * 1000
            telemetry = monitor.stop() if monitor else {}

        if success:
            self._reset_failures()
        else:
            self._mark_failure()

        fatal_error = False
        if error:
            from ..ffmpeg.errors import classify_stderr_line
            # Check if any line in the error output is fatal
            for line in error.splitlines():
                if classify_stderr_line(line) == "fatal":
                    fatal_error = True
                    break

            if fatal_error:
                if not self.is_broken:
                    self.is_broken = True
                    self.broken_since = time.time()
                log.error(f"  [FATAL] {tool_name} encountered an unrecoverable error: {error.splitlines()[0] if error.splitlines() else error}")

        bytes_written = 0
        if success and output_path:
            try:
                bytes_written = os.path.getsize(output_path)
            except OSError:
                pass

        return ConvertResult(
            success=success,
            duration_ms=duration_ms,
            telemetry=telemetry,
            parameters_used={"cli_args": params, "quality_value": quality, "method": "subprocess"},
            error=error,
            fatal_error=fatal_error,
            bytes_written=bytes_written,
        )

    def _run_library(
        self,
        func,
        tool_name: str,
        quality: Union[int, float],
        *args,
        run_id: Optional[int] = None,
        output_path: Optional[str] = None,
        **kwargs
    ) -> ConvertResult:
        """Execute an in-process library call with telemetry capture and circuit-breaker logic.

        Wraps library functions (pyvips, Wand, etc.) with telemetry monitoring and
        circuit-breaker state updates.

        Args:
            func: Callable to invoke (typically a library wrapper method).
            tool_name: Human-readable tool name for logging.
            quality: Quality value (for result tracking and circuit breaker context).
            *args: Positional arguments to pass to func.
            run_id: Optional batch run ID for telemetry association.
            **kwargs: Keyword arguments to pass to func.

        Returns:
            ConvertResult containing success status, duration, telemetry, parameters used, error, and fatal status.
        """
        self._set_active_run_id(run_id)
        if self.is_broken and not getattr(self, "_bypass_breaker", False):
            if self.broken_since and (time.time() - self.broken_since) > self.cooldown_period:
                log.warning(f"Cooldown period elapsed. Retrying broken library converter: {self.get_name()}")
                self._reset_failures()
            else:
                return ConvertResult(success=False, error=f"{tool_name} (library) is marked as broken.")

        start = time.time()
        monitor = TelemetryMonitor(interval_ms=int(TELEMETRY_INTERVAL * 1000), run_id=run_id)
        monitor.start()
        try:
            log.debug(f"Running {tool_name} in-process library call")
            lib_result = func(*args, **kwargs)
            success = True
            error = None
            params = {"quality_value": quality, "method": "library"}
            if isinstance(lib_result, dict):
                params.update(lib_result)
            self._reset_failures()
        except Exception as e:
            success = False
            error = str(e)
            log.error(f"{tool_name} library error: {error}")
            params = {"quality_value": quality, "method": "library", "lib_error": error}
            self._mark_failure()
        finally:
            duration_ms = (time.time() - start) * 1000
            telemetry = monitor.stop()

        bytes_written = 0
        if success and output_path:
            try:
                bytes_written = os.path.getsize(output_path)
            except OSError:
                pass

        return ConvertResult(
            success=success,
            duration_ms=duration_ms,
            telemetry=telemetry,
            parameters_used=params,
            error=error,
            bytes_written=bytes_written,
        )

    def convert_batch(
        self,
        input_paths: List[str],
        output_dir: str,
        target_format: str,
        qualities: List[float],
        run_id: Optional[int] = None,
        suffix: str = "",
        dimensions: Optional[Dict[str, tuple[int, int]]] = None,
    ) -> BatchResult:
        """Convert a batch of images efficiently.

        Default implementation delegates to _default_batch_convert(). Subclasses
        may override with specialized batch paths (e.g., image2 demuxer, multimap).

        Args:
            input_paths: List of input file paths.
            output_dir: Directory where output files are written.
            target_format: Output format.
            qualities: Per-file quality values (parallel to input_paths).
            run_id: Optional batch run ID for telemetry tracking.
            suffix: Optional suffix to insert before the file extension.
            dimensions: Optional dict mapping input paths to (width, height) tuples.

        Returns:
            BatchResult containing conversion metrics, aggregated telemetry, errors, and bytes written.
        """
        return self._default_batch_convert(input_paths, output_dir, target_format, qualities, run_id=run_id, suffix=suffix, dimensions=dimensions)

    def _default_batch_convert(
        self,
        input_paths: List[str],
        output_dir: str,
        target_format: str,
        qualities: List[float],
        run_id: Optional[int] = None,
        suffix: str = "",
        dimensions: Optional[Dict[str, tuple[int, int]]] = None,
    ) -> BatchResult:
        """Generic batch conversion using adaptive ThreadPoolExecutor.

        Dispatches each image to convert() in parallel, with thread pool size
        tuned by CPU count and available RAM.

        Args:
            input_paths: List of input file paths.
            output_dir: Directory where output files are written.
            target_format: Output format.
            qualities: Per-file quality values.
            run_id: Optional batch run ID.
            suffix: Optional filename suffix.
            dimensions: Optional pre-computed dimensions (unused in base implementation).

        Returns:
            BatchResult containing conversion metrics, aggregated telemetry, errors, and bytes written.
        """
        self._set_active_run_id(run_id)
        start = time.time()
        success_count = 0
        failure_count = 0
        errors = []

        aggregated_telemetry = {
            "cpu_avg": 0.0,
            "cpu_peak": 0.0,
            "ram_peak": 0.0,
        }
        telemetry_samples = []

        os.makedirs(output_dir, exist_ok=True)

        # 1. Calculate ideal max workers based on CPU with OS/API core reservation
        cpu_count = os.cpu_count() or 4
        if CONCURRENT_ENCODES_MAX_WORKERS is not None:
            max_workers = CONCURRENT_ENCODES_MAX_WORKERS
        else:
            reserved = 2 if cpu_count > 4 else (1 if cpu_count > 2 else 0)
            effective_cpus = max(1, cpu_count - reserved)
            max_workers = int(effective_cpus * CONCURRENT_ENCODES_SCALING_FACTOR)

        # 2. Resource Guard: throttle if RAM is low
        try:
            available_ram_mb = psutil.virtual_memory().available / (1024 * 1024)
            if available_ram_mb < CONCURRENT_ENCODES_MIN_RAM_MB:
                log.warning(f"Low memory ({available_ram_mb:.1f} MB), throttling thread pool to 1.")
                max_workers = 1
            elif available_ram_mb < CONCURRENT_ENCODES_MIN_RAM_MB * 4:
                throttle_ratio = available_ram_mb / (CONCURRENT_ENCODES_MIN_RAM_MB * 4)
                max_workers = max(1, int(max_workers * throttle_ratio))
        except Exception as e:
            log.debug(f"Resource guard check failed: {e}")

        # 3. Memory-aware cap: bound workers so concurrent decoded frames fit in
        #    RAM. The one-time check above is point-in-time; without this a batch
        #    of large frames spawns cpu_count*SCALING workers that all decode at
        #    once and exhaust RAM (root cause of the system-wide OOM).
        try:
            available_ram_mb = psutil.virtual_memory().available / (1024 * 1024)
            capped = memory_aware_worker_cap(
                max_workers, dimensions, input_paths, available_ram_mb
            )
            if capped < max_workers:
                log.info(
                    f"Memory-aware cap: {max_workers} -> {capped} workers "
                    f"(largest frame in batch, {available_ram_mb:.0f} MB free)"
                )
            max_workers = capped
        except Exception as e:
            log.debug(f"Memory-aware worker cap skipped: {e}")

        max_workers = min(len(input_paths), max_workers)
        log.info(f"Starting batch conversion with {max_workers} concurrent workers (CPU cores={cpu_count})")

        def worker(args):
            in_path, q = args
            filename = Path(in_path).stem
            out_path = str(Path(output_dir) / f"{filename}{suffix}.{target_format}")
            return self.convert(in_path, out_path, target_format, q, run_id=run_id)

        self._bypass_breaker = True
        try:
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                results = list(executor.map(worker, zip(input_paths, qualities)))
        finally:
            self._bypass_breaker = False

        summaries = []
        bytes_written = 0
        for in_path, res in zip(input_paths, results):
            if res.get("success"):
                success_count += 1
                bytes_written += res.get("bytes_written", 0)
            else:
                failure_count += 1
                errors.append({"path": in_path, "error": res.get("error") or "Unknown error"})

            summaries.append(res.get("telemetry", {}))

        return BatchResult(
            success_count=success_count,
            failure_count=failure_count,
            duration_ms=(time.time() - start) * 1000,
            telemetry=aggregate_telemetry(summaries),
            errors=errors,
            bytes_written=bytes_written,
        )
