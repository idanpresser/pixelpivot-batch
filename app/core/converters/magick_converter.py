"""ImageMagick converter via subprocess with native batch optimization via mogrify."""

from typing import Dict, Any, List, Optional, Union
from collections import defaultdict
from pathlib import Path
import subprocess
import time
import os
import sys
from .base import BaseConverter, _win32_safe_path, ConvertResult, BatchResult, register_converter
from ..logger import get_logger
from ..telemetry import TelemetryMonitor, aggregate_telemetry
from ..utils import kill_process_tree, quality_to_jxl_distance, get_resolution_bucket_from_path
from ..config import (
    TELEMETRY_INTERVAL,
    FFMPEG_TIMEOUT,
    MAGICK_MOGRIFY_CHUNK,
    MAGICK_MOGRIFY_MAX_CMDLINE_BYTES,
    batch_subprocess_timeout,
)
from .ffmpeg_batch_helpers import pack_chunks

try:
    from wand.image import Image as WandImage
    WAND_AVAILABLE = True
except ImportError:
    WAND_AVAILABLE = False

log = get_logger(__name__)


@register_converter("magick")
class MagickConverter(BaseConverter):
    """Convert still images via ImageMagick subprocess with mogrify batch optimization.

    Uses mogrify for native batch processing (groups by quality and resolution bucket),
    with Windows safety chunking to respect CreateProcess limits. Subprocess is
    preferred over in-process Wand to avoid OpenMP/MagickCore assertion failures
    in multi-process environments. Fallback to Wand per-file on subprocess failure.
    """

    FORMAT_PARAMS = {
        "webp": lambda q: ["-quality", str(q)],
        "avif": lambda q: ["-quality", str(q)],
        "jxl": lambda q: ["-define", f"jxl:distance={quality_to_jxl_distance(q)}"],
    }

    def __init__(self, magick_path: str):
        """Initialize ImageMagick converter.

        Args:
            magick_path: Path to the magick binary.
        """
        super().__init__()
        self.magick_path = magick_path

    def get_name(self) -> str:
        """Return the converter name."""
        return "imagemagick"

    def supported_formats(self) -> List[str]:
        """Return list of supported output formats."""
        return ["webp", "avif", "jxl"]

    def _convert_via_wand(
        self,
        input_path: str,
        output_path: str,
        target_format: str,
        quality: Union[int, float],
    ) -> Dict[str, Any]:
        """In-process conversion via Wand (ImageMagick Python bindings).

        Used as fallback only; subprocess is preferred to avoid assertion failures.

        Args:
            input_path: Input image path.
            output_path: Output file path.
            target_format: Output format.
            quality: Quality value (0-100).

        Returns:
            Dict with 'method', 'quality', and any encoder-specific metadata.

        Raises:
            ImportError: If Wand is not installed.
        """
        if not WAND_AVAILABLE:
            raise ImportError("Wand library not installed.")

        with WandImage(filename=input_path) as img:
            # Note: Wand/ImageMagick format names might differ slightly (e.g., 'jpeg')
            img.format = target_format
            # Task 024: ImageMagick's `quality` field is integer-valued, but
            # we must use round() (unbiased) instead of int() (truncates).
            img.quality = round(quality)
            img.save(filename=output_path)

        return {"method": "wand", "quality": quality}

    def convert(
        self,
        input_path: str,
        output_path: str,
        target_format: str,
        quality: Union[int, float],
        is_intermediate: bool = False,
        run_id: Optional[int] = None,
    ) -> ConvertResult:
        """Convert a single image via magick subprocess, with Wand fallback.

        Subprocess is preferred in multi-process environments to avoid OpenMP
        and MagickCore assertion failures. Falls back to Wand if subprocess fails.

        Args:
            input_path: Path to input image.
            output_path: Path where output should be written.
            target_format: Output format ('webp', 'avif', or 'jxl').
            quality: Quality value 0-100 (higher is better).
            is_intermediate: Unused.
            run_id: Optional batch run ID for telemetry.

        Returns:
            ConvertResult containing success status, duration, telemetry, parameters used, error, and fatal status.
        """

        self._set_active_run_id(run_id)
        param_builder = self.FORMAT_PARAMS.get(target_format)
        if not param_builder:
            return ConvertResult(success=False, error=f"Unsupported format: {target_format}")

        params = param_builder(quality)
        cmd = [self.magick_path, _win32_safe_path(input_path)] + params + [_win32_safe_path(output_path)]

        try:
            return self._run_subprocess(cmd, "ImageMagick", params, quality, run_id=run_id, output_path=output_path)
        except Exception as e:
            # Last-ditch fallback to Wand if subprocess fails for some reason
            if WAND_AVAILABLE:
                log.warning(f"Subprocess failed, trying Wand: {e}")
                try:
                    return self._run_library(
                        self._convert_via_wand,
                        "ImageMagick-Wand",
                        quality,
                        input_path,
                        output_path,
                        target_format,
                        quality,
                        run_id=run_id,
                        output_path=output_path
                    )
                except Exception as e2:
                    log.error(f"Wand also failed: {e2}")
            raise e

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
        """Convert a batch of images via mogrify with Windows safety chunking.

        Groups by (quality, resolution_bucket), runs mogrify on each group with
        chunks bounded by MAGICK_MOGRIFY_CHUNK and CreateProcess limits, and
        falls back to per-file convert() on chunk failure.

        Args:
            input_paths: List of input file paths.
            output_dir: Directory where output files are written.
            target_format: Output format ('webp', 'avif', or 'jxl').
            qualities: Per-file quality values.
            run_id: Optional batch run ID for telemetry.
            suffix: Optional filename suffix.
            dimensions: Optional pre-computed (width, height) dict.

        Returns:
            Dict with 'success_count', 'failure_count', 'duration_ms', 'telemetry',
            and 'errors' keys.
        """
        start = time.time()
        success_count = 0
        failure_count = 0
        errors = []
        bytes_written = 0

        # 1. Group images by identical quality AND resolution bucket
        from ..utils import get_resolution_bucket
        groups = defaultdict(list)
        for path, q in zip(input_paths, qualities):
            if dimensions and path in dimensions:
                w, h = dimensions[path]
                res_bucket = get_resolution_bucket(w, h)
            else:
                res_bucket = get_resolution_bucket_from_path(path)
            groups[(q, res_bucket)].append(path)

        # 2. Execute mogrify for each quality-resolution group
        os.makedirs(output_dir, exist_ok=True)

        summaries = []
        for (q, res_bucket), paths in groups.items():
            param_builder = self.FORMAT_PARAMS.get(target_format)
            params = param_builder(q) if param_builder else ["-quality", str(q)]

            # cmd prefix: magick mogrify -path <dir> -format <fmt> -quality <q>
            cmd_prefix = [self.magick_path]
            if "magick" in self.magick_path.lower():
                cmd_prefix.append("mogrify")
            cmd_prefix += ["-path", output_dir, "-format", target_format] + params

            # Calculate fixed overhead for pack_chunks
            fixed_overhead = sum(len(arg) for arg in cmd_prefix) + len(cmd_prefix) + 128

            # Prepare pairs of (input_path, expected_output_path) for pack_chunks
            pairs = []
            for p in paths:
                stem = Path(p).stem
                suffix_str = suffix or ""
                expected_out = os.path.normpath(os.path.join(output_dir, f"{stem}{suffix_str}.{target_format}"))
                pairs.append((p, expected_out))

            # Defensive chunk-splitting for Windows' 8191-character CreateProcess limit
            chunks = pack_chunks(
                pairs,
                max_files=MAGICK_MOGRIFY_CHUNK,
                max_cmdline_bytes=MAGICK_MOGRIFY_MAX_CMDLINE_BYTES,
                fixed_overhead=fixed_overhead,
                per_pair_overhead=0, # mogrify only adds in_path per file
            )

            for chunk_index, pair_chunk in enumerate(chunks):
                chunk_paths = [p_in for p_in, _ in pair_chunk]
                cmd = list(cmd_prefix) + chunk_paths

                try:
                    log.debug(f"Running mogrify batch chunk {chunk_index + 1}: {' '.join(cmd[:10])}...")
                    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

                    # Context manager for process lifecycle
                    with subprocess.Popen(
                         cmd,
                         stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE,
                         text=True,
                         creationflags=creationflags,
                    ) as proc:
                        from ..process_registry import register_process, unregister_process
                        register_process(proc)
                        try:
                            monitor = TelemetryMonitor(pid=proc.pid, interval_ms=int(TELEMETRY_INTERVAL * 1000), run_id=run_id)
                            monitor.start()

                            try:
                                stdout, stderr = proc.communicate(timeout=batch_subprocess_timeout(len(chunk_paths)))
                                success = proc.returncode == 0
                            except subprocess.TimeoutExpired:
                                log.warning("Mogrify timed out, force cleaning process tree...")
                                kill_process_tree(proc.pid)
                                proc.communicate()
                                success = False
                                stderr = f"Mogrify timed out for chunk of size {len(chunk_paths)}"

                            summaries.append(monitor.stop())
                        finally:
                            unregister_process(proc)

                    if success:
                        self._reset_failures()
                        # Rename/verify outputs if suffix is provided
                        for p_in, p_out in pair_chunk:
                            stem = Path(p_in).stem
                            mogrify_out = Path(output_dir) / f"{stem}.{target_format}"
                            if suffix:
                                if mogrify_out.exists() and os.path.getsize(mogrify_out) > 0:
                                    try:
                                        if Path(p_out) != mogrify_out:
                                            if os.path.exists(p_out):
                                                os.unlink(p_out)
                                            os.rename(mogrify_out, p_out)
                                        success_count += 1
                                        try:
                                            bytes_written += os.path.getsize(p_out)
                                        except OSError:
                                            pass
                                    except Exception as re_err:
                                        log.error(f"Failed to rename magick output {mogrify_out} to {p_out}: {re_err}")
                                        res = self.convert(p_in, p_out, target_format, q, run_id=run_id)
                                        if res.get("success"):
                                            success_count += 1
                                            bytes_written += res.get("bytes_written", 0)
                                            summaries.append(res.get("telemetry", {}))
                                        else:
                                            failure_count += 1
                                            errors.append({"path": p_in, "error": res.get("error") or f"Failed to convert fallback {p_in}"})
                                else:
                                    # Fallback if expected output wasn't produced by mogrify
                                    res = self.convert(p_in, p_out, target_format, q, run_id=run_id)
                                    if res.get("success"):
                                        success_count += 1
                                        bytes_written += res.get("bytes_written", 0)
                                        summaries.append(res.get("telemetry", {}))
                                    else:
                                        failure_count += 1
                                        errors.append({"path": p_in, "error": res.get("error") or f"Mogrify missed output for {p_in}"})
                            else:
                                # No suffix, trust mogrify success directly
                                success_count += 1
                                try:
                                    bytes_written += os.path.getsize(p_out)
                                except OSError:
                                    pass
                        self._account_native_batch(failed=False)
                    else:
                        self._mark_failure()
                        log.warning(f"Mogrify batch chunk failed. Falling back to individual conversion.")
                        ok, fail, errs, sums, tripped, chunk_bytes = self._recover_chunk_per_file(
                            chunk_paths, output_dir, target_format, q, suffix, run_id
                        )
                        success_count += ok
                        failure_count += fail
                        bytes_written += chunk_bytes
                        errors.extend(errs)
                        summaries.extend(sums)
                        self._account_native_batch(failed=fail > 0)
                        if tripped:
                            self.is_broken = True

                except Exception as e:
                    self._mark_failure()
                    log.error(f"Magick batch error for chunk: {e}")
                    ok, fail, errs, sums, tripped, chunk_bytes = self._recover_chunk_per_file(
                        chunk_paths, output_dir, target_format, q, suffix, run_id
                    )
                    success_count += ok
                    failure_count += fail
                    bytes_written += chunk_bytes
                    errors.extend(errs)
                    summaries.extend(sums)
                    errors.append({"path": None, "error": str(e)})
                    self._account_native_batch(failed=fail > 0)
                    if tripped:
                        self.is_broken = True

        return BatchResult(
            success_count=success_count,
            failure_count=failure_count,
            duration_ms=(time.time() - start) * 1000,
            telemetry=aggregate_telemetry(summaries),
            errors=errors,
            bytes_written=bytes_written,
        )

    def _recover_chunk_per_file(self, chunk, output_dir, target_format, q, suffix, run_id):
        """Recover a failed mogrify chunk by invoking convert() per file.

        Args:
            chunk: List of input paths from the failed chunk.
            output_dir: Directory where outputs are written.
            target_format: Output format.
            q: Quality value for this group.
            suffix: Optional filename suffix.
            run_id: Optional batch run ID.

        Returns:
            Tuple of (ok_count, fail_count, errors, telemetry_samples, tripped, bytes_written),
            where tripped indicates whether the circuit breaker was triggered.
        """
        # Set the active run FIRST so the save snapshot and the restore below
        # address the same per-run breaker state key. Reading the getters before
        # setting run_id captured the entry thread's state (typically the global
        # None state) while the restore wrote the run_id state — an asymmetric
        # save/restore that corrupted the batch run's breaker fields (bd-qk1.4).
        self._set_active_run_id(run_id)
        saved_failures = self.consecutive_failures
        saved_broken = self.is_broken
        saved_broken_since = self.broken_since
        self._bypass_breaker = True
        
        ok = 0
        fail = 0
        errs: List[Dict[str, Any]] = []
        sums: List[Dict[str, Any]] = []
        bytes_written = 0
        try:
            for p in chunk:
                out_path = str(Path(output_dir) / f"{Path(p).stem}{suffix}.{target_format}")
                try:
                    res = self.convert(p, out_path, target_format, q, run_id=run_id)
                except Exception as e2:
                    fail += 1
                    errs.append({"path": p, "error": str(e2)})
                    continue
                if res.get("success"):
                    ok += 1
                    bytes_written += res.get("bytes_written", 0)
                    sums.append(res.get("telemetry", {}))
                else:
                    fail += 1
                    errs.append({"path": p, "error": res.get("error") or f"Failed to convert {p}"})
        finally:
            self._bypass_breaker = False

        tripped = self.is_broken
        self.consecutive_failures = saved_failures
        self.is_broken = saved_broken
        self.broken_since = saved_broken_since
        return ok, fail, errs, sums, tripped, bytes_written
