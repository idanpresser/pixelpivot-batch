"""Abstract base class and telemetry harness for image converters."""

from abc import ABC, abstractmethod
import subprocess
import sys
import time
import os
import psutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, Any, List, Union, Optional
from ..telemetry import TelemetryMonitor, aggregate_telemetry
from ..logger import get_logger
from ..utils import kill_process_tree
from ..config import (
    FFMPEG_TIMEOUT,
    TELEMETRY_INTERVAL,
    MAX_LOG_BUFFER,
    CONCURRENT_ENCODES_SCALING_FACTOR,
    CONCURRENT_ENCODES_MIN_RAM_MB,
)

log = get_logger(__name__)


def _truncate(s: str | None, limit: int = 2048) -> str | None:
    if not s:
        return s
    return s if len(s) <= limit else s[:limit] + f"... ({len(s) - limit} bytes truncated)"


class BaseConverter(ABC):
    """Abstract base class defining the converter interface and circuit-breaker pattern.

    Subclasses must implement get_name(), supported_formats(), and convert().
    The default convert_batch() uses ThreadPoolExecutor; subclasses override it
    for efficiency (e.g., FFmpegConverter uses hybrid image2 + multimap paths).
    """
    def __init__(self):
        self.consecutive_failures = 0
        self.failure_threshold = 3
        self.is_broken = False
        self.broken_since = None
        self.cooldown_period = 30.0  # seconds self-healing cooldown

    def _mark_failure(self):
        self.consecutive_failures += 1
        # Increased to 10 for FFmpeg to allow more diagnostic room.
        threshold = 10 if self.get_name() == "ffmpeg" else self.failure_threshold
        if self.consecutive_failures >= threshold:
            if not self.is_broken:
                self.is_broken = True
                self.broken_since = time.time()
                log.error(f"  [CIRCUIT BREAKER] {self.get_name()} is now marked as BROKEN after {self.consecutive_failures} failures.")

    def _reset_failures(self):
        self.consecutive_failures = 0
        self.is_broken = False
        self.broken_since = None

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
    ) -> Dict[str, Any]:
        """Convert a single image file.

        Args:
            input_path: Path to input image.
            output_path: Path where output should be written.
            target_format: Output format (e.g., 'webp', 'avif', 'jxl').
            quality: Format-native quality value. Higher values indicate better quality.
            is_intermediate: Hint that this is a calibration encode (may optimize speed over quality).
            run_id: Optional batch run ID for telemetry tracking.

        Returns:
            Dict with 'success' (bool), 'error' (str or None), 'duration_ms', 'telemetry',
            'parameters_used', and 'fatal_error' keys.
        """
        pass

    def _run_subprocess(
        self,
        cmd: List[str],
        tool_name: str,
        params: List[str],
        quality: Union[int, float],
        run_id: Optional[int] = None,
    ) -> Dict[str, Any]:
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
            Dict with 'success', 'duration_ms', 'telemetry', 'parameters_used', 'error',
            and 'fatal_error' keys.
        """
        # Circuit Breaker with 30s self-healing cooldown bypass
        if self.is_broken:
            if self.broken_since and (time.time() - self.broken_since) > self.cooldown_period:
                log.warning(f"Cooldown period elapsed. Retrying broken converter: {self.get_name()}")
                self._reset_failures()
            else:
                return {"success": False, "error": f"{tool_name} is marked as broken (too many failures)."}

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

        return {
            "success": success,
            "duration_ms": duration_ms,
            "telemetry": telemetry,
            "parameters_used": {"cli_args": params, "quality_value": quality, "method": "subprocess"},
            "error": error,
            "fatal_error": fatal_error,
        }

    def _run_library(
        self,
        func,
        tool_name: str,
        quality: Union[int, float],
        *args,
        run_id: Optional[int] = None,
        **kwargs
    ) -> Dict[str, Any]:
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
            Dict with 'success', 'duration_ms', 'telemetry', 'parameters_used', and 'error' keys.
        """
        if self.is_broken:
            if self.broken_since and (time.time() - self.broken_since) > self.cooldown_period:
                log.warning(f"Cooldown period elapsed. Retrying broken library converter: {self.get_name()}")
                self._reset_failures()
            else:
                return {"success": False, "error": f"{tool_name} (library) is marked as broken."}

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

        return {
            "success": success,
            "duration_ms": duration_ms,
            "telemetry": telemetry,
            "parameters_used": params,
            "error": error,
        }

    def convert_batch(
        self,
        input_paths: List[str],
        output_dir: str,
        target_format: str,
        qualities: List[float],
        run_id: Optional[int] = None,
        suffix: str = "",
        dimensions: Optional[Dict[str, tuple[int, int]]] = None,
    ) -> Dict[str, Any]:
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
            Dict with 'success_count', 'failure_count', 'duration_ms', 'telemetry',
            and 'errors' keys.
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
    ) -> Dict[str, Any]:
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
            Dict with 'success_count', 'failure_count', 'duration_ms', 'telemetry',
            and 'errors' keys.
        """
        start = time.time()
        success_count = 0
        failure_count = 0
        errors = []

        aggregated_telemetry = {
            "cpu_avg": 0.0,
            "cpu_peak": 0.0,
            "ram_peak": 0.0,
            "gpu_peak": 0.0,
        }
        telemetry_samples = []

        os.makedirs(output_dir, exist_ok=True)

        # 1. Calculate ideal max workers based on CPU
        cpu_count = os.cpu_count() or 4
        max_workers = int(cpu_count * CONCURRENT_ENCODES_SCALING_FACTOR)

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

        max_workers = min(len(input_paths), max_workers)
        log.info(f"Starting batch conversion with {max_workers} concurrent workers (CPU cores={cpu_count})")

        def worker(args):
            in_path, q = args
            filename = Path(in_path).stem
            out_path = str(Path(output_dir) / f"{filename}{suffix}.{target_format}")
            return self.convert(in_path, out_path, target_format, q, run_id=run_id)

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            results = list(executor.map(worker, zip(input_paths, qualities)))

        summaries = []
        for in_path, res in zip(input_paths, results):
            if res.get("success"):
                success_count += 1
            else:
                failure_count += 1
                errors.append({"path": in_path, "error": res.get("error") or "Unknown error"})

            summaries.append(res.get("telemetry", {}))

        return {
            "success_count": success_count,
            "failure_count": failure_count,
            "duration_ms": (time.time() - start) * 1000,
            "telemetry": aggregate_telemetry(summaries),
            "errors": errors,
        }
