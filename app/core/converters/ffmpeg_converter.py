"""
FFmpegConverter — subprocess-only encoder for webp / avif / jxl via ffmpeg.

The PyAV in-process path was removed: PyAV is a libav (video) binding and was
fighting the still-image use case (padding, pix_fmt, distance vs. crf). All
encoding now goes through ffmpeg as a subprocess, supervised by FFmpegProcess
which gives us live progress, stall detection, and three-stage cancellation.
"""

from __future__ import annotations

import os
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from ..config import (
    FFMPEG_BATCH_MAX_CMDLINE_BYTES,
    FFMPEG_BATCH_MAX_FILES,
    IMAGE2_ALLOW_LOSSY_FORMATS,
    IMAGE2_THRESHOLD,
    TELEMETRY_INTERVAL,
    ffmpeg_wall_timeout_for,
)
from ..ffmpeg import FFmpegProcess
from ..logger import get_logger
from ..telemetry import TelemetryMonitor, aggregate_telemetry
from ..utils import quality_to_jxl_distance
from .base import BaseConverter
from .ffmpeg_batch_helpers import (
    all_same_resolution,
    build_image2_args,
    build_multimap_args,
    encoder_params_for,
    group_by_dimensions,
    pack_chunks,
    stage_inputs_for_image2,
    staging_dir,
)

log = get_logger(__name__)


class FFmpegConverter(BaseConverter):
    """Convert still images via FFmpeg subprocess with hybrid native-batch acceleration.

    Uses FFmpegProcess for supervised encoding with live progress, stall detection,
    and three-stage cancellation. The convert_batch() method groups by (format, quality),
    sub-groups by (width, height), and routes to image2 demuxer (large uniform-size batches)
    or multimap chunks (mixed/smaller batches), with per-file convert() as final fallback.
    Measured speedup: ~4.9x on uniform-size batches, ~1.9x on mixed real-world batches.
    """

    def __init__(self, ffmpeg_path: str):
        """Initialize FFmpeg converter.

        Args:
            ffmpeg_path: Path to the ffmpeg binary.
        """
        super().__init__()
        self.ffmpeg_path = ffmpeg_path

    def get_name(self) -> str:
        """Return the converter name."""
        return "ffmpeg"

    def supported_formats(self) -> List[str]:
        """Return list of supported output formats."""
        return ["webp", "avif", "jxl"]

    def convert(
        self,
        input_path: str,
        output_path: str,
        target_format: str,
        quality: Union[int, float],
        use_gpu: bool = False,
        is_intermediate: bool = False,
        run_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Convert a single image file via ffmpeg subprocess.

        Args:
            input_path: Path to input image.
            output_path: Path where output should be written.
            target_format: Output format ('webp', 'avif', or 'jxl').
            quality: Format-native quality (0-100 for webp/avif/jxl).
            use_gpu: Enable CUDA hardware acceleration if available.
            is_intermediate: Hint to use faster encoder settings (AVIF only; sets cpu-used=6).
            run_id: Optional batch run ID for telemetry.

        Returns:
            Dict with conversion result including success status, duration, telemetry,
            and error details.
        """
        if self.is_broken:
            return {"success": False, "error": f"{self.get_name()} is broken"}

        params = encoder_params_for(target_format, quality)
        if params is None:
            return {"success": False, "error": f"Unsupported format: {target_format}"}

        # Faster cpu-used for intermediate calibration encodes (AVIF only).
        if target_format == "avif" and is_intermediate:
            try:
                idx = params.index("-cpu-used")
                params[idx + 1] = "6"
            except (ValueError, IndexError):
                params.extend(["-cpu-used", "6"])

        args = self._build_args(input_path, output_path, params, use_gpu)
        return self._run_ffmpeg(args, params, quality, target_format, output_path, run_id=run_id)

    def _build_args(
        self,
        input_path: str,
        output_path: str,
        encoder_params: List[str],
        use_gpu: bool,
    ) -> List[str]:
        """Build the ffmpeg command-line arguments for a single image conversion.

        Args:
            input_path: Input file path.
            output_path: Output file path.
            encoder_params: Encoder-specific parameters (e.g., codec, quality flags).
            use_gpu: Enable CUDA hwaccel if True.

        Returns:
            Complete argv list (without ffmpeg binary name).
        """
        global_opts = ["-y", "-hide_banner", "-nostats", "-progress", "pipe:1"]
        hwaccel = ["-hwaccel", "cuda"] if use_gpu else []
        padding = ["-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2"]
        return global_opts + hwaccel + ["-i", input_path] + padding + encoder_params + [output_path]

    def _run_ffmpeg(
        self,
        args: List[str],
        params: List[str],
        quality: Union[int, float],
        target_format: str,
        output_path: str,
        run_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Run ffmpeg via FFmpegProcess with progress tracking and timeout supervision.

        Args:
            args: FFmpeg argv (without binary name).
            params: Encoder parameters (for result logging).
            quality: Quality value (for result logging).
            target_format: Output format (used to determine timeout).
            output_path: Output file path (for verification).
            run_id: Optional batch run ID for telemetry.

        Returns:
            Dict with success status, duration, telemetry, parameters used, and error details.
        """
        proc = FFmpegProcess(
            self.ffmpeg_path,
            args,
            wall_timeout_s=ffmpeg_wall_timeout_for(target_format),
        )

        try:
            pid = proc.spawn()
        except FileNotFoundError as e:
            self._mark_failure()
            return {
                "success": False,
                "duration_ms": 0.0,
                "telemetry": {},
                "parameters_used": {"cli_args": params, "quality_value": quality, "method": "subprocess"},
                "error": f"ffmpeg binary not found: {e}",
                "fatal_error": True,
            }

        monitor = TelemetryMonitor(pid=pid, interval_ms=int(TELEMETRY_INTERVAL * 1000), run_id=run_id)
        monitor.start()
        try:
            result = proc.run()
        finally:
            telemetry = monitor.stop()

        success = result.success
        error = result.error

        # Output-file verification stays in the converter — the wrapper doesn't
        # know which arg is the output path.
        if success:
            if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
                success = False
                error = f"ffmpeg claimed success but output is missing or empty: {output_path}"

        if success:
            self._reset_failures()
        else:
            self._mark_failure()
            if result.fatal:
                self.is_broken = True
                log.error(
                    "[FATAL] ffmpeg encountered an unrecoverable error: %s",
                    (error or "").splitlines()[0] if error else "(no detail)",
                )

        return {
            "success": success,
            "duration_ms": result.duration_ms,
            "telemetry": telemetry,
            "parameters_used": {
                "cli_args": params,
                "quality_value": quality,
                "method": "subprocess",
                "progress_samples": len(result.progress_samples),
            },
            "error": error,
            "fatal_error": result.fatal,
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
        """Convert a batch of images via hybrid native-batch routing.

        Groups by (format, quality), sub-groups by (width, height), and routes:
        - Uniform-size sub-groups >= IMAGE2_THRESHOLD -> image2 demuxer with hardlink staging
        - Mixed/smaller sub-groups -> multi-input/multi-output chunks
        - Unsupported formats or dimension probe failures -> per-file convert() fallback

        Telemetry is averaged per ffmpeg invocation; per-image granularity is lost
        (same tradeoff as MagickConverter, required by batch_summary contract).

        Args:
            input_paths: List of input file paths.
            output_dir: Directory where output files are written.
            target_format: Output format ('webp', 'avif', or 'jxl').
            qualities: Per-file quality values.
            run_id: Optional batch run ID for telemetry.
            suffix: Optional filename suffix.
            dimensions: Optional pre-computed (width, height) dict (unused in this implementation).

        Returns:
            Dict with 'success_count', 'failure_count', 'duration_ms', 'telemetry',
            and 'errors' keys. Note: does NOT return per-image results.
        """
        start = time.time()
        os.makedirs(output_dir, exist_ok=True)

        if not input_paths:
            return {
                "success_count": 0,
                "failure_count": 0,
                "duration_ms": 0.0,
                "telemetry": {},
                "errors": [],
            }

        success_count = 0
        failure_count = 0
        errors: List[Dict[str, Any]] = []
        telemetry_samples: List[Dict[str, Any]] = []

        # Outer group by quality (encoder params depend on quality).
        quality_groups: Dict[float, List[str]] = defaultdict(list)
        for path, q in zip(input_paths, qualities):
            quality_groups[q].append(path)

        for q, group_paths in quality_groups.items():
            params = encoder_params_for(target_format, q)
            if params is None:
                # Unsupported format — surface the error per item.
                res = self._fallback_per_file(group_paths, output_dir, target_format, [q] * len(group_paths), run_id, suffix=suffix)
                success_count += res["success_count"]
                failure_count += res["failure_count"]
                errors.extend(res["errors"])
                if res["telemetry"]:
                    telemetry_samples.append(res["telemetry"])
                continue

            # Hardware-safety: limit threads when doing many-output batching
            batch_params = params + ["-threads", "1"]

            size_groups = group_by_dimensions(group_paths)

            for wh, sub_paths in size_groups.items():
                if wh is None:
                    res = self._fallback_per_file(sub_paths, output_dir, target_format, [q] * len(sub_paths), run_id, suffix=suffix)
                    success_count += res["success_count"]
                    failure_count += res["failure_count"]
                    errors.extend(res["errors"])
                    if res["telemetry"]:
                        telemetry_samples.append(res["telemetry"])
                    continue

                # image2 path is unreliable for AVIF/JXL on some libaom/heif builds; gated
                # by IMAGE2_ALLOW_LOSSY_FORMATS (env: PIXELPIVOT_IMAGE2_ALLOW_LOSSY). When
                # the flag is on, multimap remains the per-chunk safety net on failure.
                can_use_image2 = (
                    target_format not in ("avif", "jxl") or IMAGE2_ALLOW_LOSSY_FORMATS
                )

                if can_use_image2 and len(sub_paths) >= IMAGE2_THRESHOLD and all_same_resolution(sub_paths):
                    ok, fail, errs, tele, leftovers = self._run_image2_path(
                        sub_paths, output_dir, target_format, q, batch_params, run_id, suffix=suffix
                    )
                    success_count += ok
                    failure_count += fail
                    errors.extend(errs)
                    if tele:
                        telemetry_samples.append(tele)
                    if leftovers:
                        ok2, fail2, errs2, tele2 = self._run_multimap_path(
                            leftovers, output_dir, target_format, q, batch_params, run_id, suffix=suffix
                        )
                        success_count += ok2
                        failure_count += fail2
                        errors.extend(errs2)
                        if tele2:
                            telemetry_samples.append(tele2)
                else:
                    ok, fail, errs, tele = self._run_multimap_path(
                        sub_paths, output_dir, target_format, q, batch_params, run_id, suffix=suffix
                    )
                    success_count += ok
                    failure_count += fail
                    errors.extend(errs)
                    if tele:
                        telemetry_samples.append(tele)

        return {
            "success_count": success_count,
            "failure_count": failure_count,
            "duration_ms": (time.time() - start) * 1000,
            "telemetry": aggregate_telemetry(telemetry_samples),
            "errors": errors,
        }

    def _run_image2_path(
        self,
        paths: List[str],
        output_dir: str,
        target_format: str,
        quality: float,
        encoder_params: List[str],
        run_id: Optional[int],
        suffix: str = "",
    ):
        """Run image2 demuxer batch on uniform-size images via hardlink staging.

        Creates frame00001.<ext>, frame00002.<ext>, ... in a temp directory,
        runs ffmpeg once to produce out00001.<fmt>, out00002.<fmt>, ... alongside,
        then moves outputs to the target directory. Any missing outputs are
        returned as leftovers for multimap retry.

        Args:
            paths: List of input paths (all must be same resolution).
            output_dir: Directory where outputs are written.
            target_format: Output format.
            quality: Quality value.
            encoder_params: Encoder-specific parameters.
            run_id: Optional batch run ID.
            suffix: Optional filename suffix.

        Returns:
            Tuple of (success_count, failure_count, errors, telemetry, leftover_paths).
            Leftover paths are those whose outputs were not produced by ffmpeg.
        """
        ext = Path(paths[0]).suffix.lstrip(".").lower() or "png"
        count = len(paths)

        with staging_dir(prefix="ffbatch_img2_") as stage:
            try:
                rename_map = stage_inputs_for_image2(paths, stage, ext=ext)
            except Exception as e:
                log.warning("Image2 staging failed (%s); routing to multimap.", e)
                return 0, 0, [], {}, list(paths)

            args = build_image2_args(
                staging_dir_path=stage,
                input_ext=ext,
                output_ext=target_format,
                count=count,
                encoder_params=encoder_params,
            )
            proc = FFmpegProcess(
                self.ffmpeg_path,
                args,
                wall_timeout_s=ffmpeg_wall_timeout_for(target_format) * max(1, count // 2),
            )
            try:
                pid = proc.spawn()
            except FileNotFoundError as e:
                log.error("ffmpeg binary not found: %s", e)
                return 0, count, [{"path": p, "error": f"ffmpeg not found: {e}"} for p in paths], {}, []

            monitor = TelemetryMonitor(pid=pid, interval_ms=int(TELEMETRY_INTERVAL * 1000), run_id=run_id)
            monitor.start()
            try:
                result = proc.run()
            finally:
                telemetry = monitor.stop()

            success_count = 0
            failure_count = 0
            errors: List[Dict[str, Any]] = []
            leftovers: List[str] = []

            for idx, original_path in zip(range(1, count + 1), paths):
                produced = os.path.join(stage, f"out{idx:05d}.{target_format}")
                target = os.path.join(output_dir, f"{rename_map[idx]}{suffix}.{target_format}")
                if os.path.exists(produced) and os.path.getsize(produced) > 0:
                    try:
                        if os.path.exists(target):
                            os.remove(target)
                        os.replace(produced, target)
                        success_count += 1
                    except OSError as e:
                        log.warning("Failed to move %s -> %s: %s", produced, target, e)
                        leftovers.append(original_path)
                else:
                    leftovers.append(original_path)

            if not result.success and leftovers:
                log.info(
                    "Image2 batch failed (%s); routing %d files to multimap fallback.",
                    (result.error or "?").splitlines()[0] if result.error else "?",
                    len(leftovers),
                )

            return success_count, failure_count, errors, telemetry, leftovers

    def _run_multimap_path(
        self,
        paths: List[str],
        output_dir: str,
        target_format: str,
        quality: float,
        encoder_params: List[str],
        run_id: Optional[int],
        suffix: str = "",
    ):
        """Run multi-input/multi-output batch on mixed or smaller image groups.

        Chunks paths respecting max-files and max-cmdline-bytes limits, then runs
        one ffmpeg per chunk with -i <in0> -i <in1> ... -map 0:v [params] out0
        -map 1:v [params] out1 ... Any files missing from chunk output are
        retried via per-file convert() fallback.

        Args:
            paths: List of input paths.
            output_dir: Directory where outputs are written.
            target_format: Output format.
            quality: Quality value.
            encoder_params: Encoder-specific parameters.
            run_id: Optional batch run ID.
            suffix: Optional filename suffix.

        Returns:
            Tuple of (success_count, failure_count, errors, telemetry).
        """
        pairs = [
            (p, os.path.join(output_dir, f"{Path(p).stem}{suffix}.{target_format}"))
            for p in paths
        ]
        # Fixed: ffmpeg path + global flags
        fixed_overhead = len(self.ffmpeg_path) + 64
        # Per-pair: -i <in> + -map idx:v + encoder_params
        per_pair_overhead = 16 + sum(len(p) for p in encoder_params)

        chunks = pack_chunks(
            pairs,
            max_files=FFMPEG_BATCH_MAX_FILES,
            max_cmdline_bytes=FFMPEG_BATCH_MAX_CMDLINE_BYTES,
            fixed_overhead=fixed_overhead,
            per_pair_overhead=per_pair_overhead,
        )

        success_count = 0
        failure_count = 0
        errors: List[Dict[str, Any]] = []
        telemetry_samples: List[Dict[str, Any]] = []

        for chunk in chunks:
            args = build_multimap_args(chunk, encoder_params)
            proc = FFmpegProcess(
                self.ffmpeg_path,
                args,
                wall_timeout_s=ffmpeg_wall_timeout_for(target_format) * max(1, len(chunk) // 2),
            )
            try:
                pid = proc.spawn()
            except FileNotFoundError as e:
                errors.extend({"path": p_in, "error": f"ffmpeg not found: {e}"} for p_in, _ in chunk)
                failure_count += len(chunk)
                continue

            monitor = TelemetryMonitor(pid=pid, interval_ms=int(TELEMETRY_INTERVAL * 1000), run_id=run_id)
            monitor.start()
            try:
                result = proc.run()
            finally:
                telemetry_samples.append(monitor.stop())

            missing: List[str] = []
            for in_path, out_path in chunk:
                if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                    success_count += 1
                else:
                    missing.append(in_path)

            if missing:
                if not result.success:
                    log.warning("Multimap chunk failed (%s); falling back per-file for %d files.",
                                (result.error or "?").splitlines()[0] if result.error else "?",
                                len(missing))
                fb = self._fallback_per_file(missing, output_dir, target_format,
                                              [quality] * len(missing), run_id, suffix=suffix)
                success_count += fb["success_count"]
                failure_count += fb["failure_count"]
                errors.extend(fb["errors"])

        return success_count, failure_count, errors, aggregate_telemetry(telemetry_samples)

    def _fallback_per_file(
        self,
        paths: List[str],
        output_dir: str,
        target_format: str,
        qualities: List[float],
        run_id: Optional[int],
        suffix: str = "",
    ) -> Dict[str, Any]:
        """Invoke per-file convert() as final fallback for unsupported or failed batches.

        Args:
            paths: List of input paths.
            output_dir: Directory where outputs are written.
            target_format: Output format.
            qualities: Per-file quality values.
            run_id: Optional batch run ID.
            suffix: Optional filename suffix.

        Returns:
            Dict with 'success_count', 'failure_count', 'telemetry', and 'errors' keys.
        """
        success_count = 0
        failure_count = 0
        errors: List[Dict[str, Any]] = []
        telemetry_samples: List[Dict[str, Any]] = []

        for in_path, q in zip(paths, qualities):
            out_path = os.path.join(output_dir, f"{Path(in_path).stem}{suffix}.{target_format}")
            res = self.convert(in_path, out_path, target_format, q, run_id=run_id)
            if res.get("success"):
                success_count += 1
            else:
                failure_count += 1
                errors.append({"path": in_path, "error": res.get("error") or "Unknown error"})
            telemetry_samples.append(res.get("telemetry") or {})

        return {
            "success_count": success_count,
            "failure_count": failure_count,
            "telemetry": aggregate_telemetry(telemetry_samples),
            "errors": errors,
        }
