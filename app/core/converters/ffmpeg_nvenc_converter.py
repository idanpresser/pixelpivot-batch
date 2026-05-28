"""
FFmpegNvencConverter — AVIF encoding via FFmpeg's av1_nvenc encoder.

Requires:
  - NVIDIA GPU with Ada Lovelace architecture or newer (RTX 4000+, compute >= 8.9)
  - FFmpeg built with NVENC SDK (av1_nvenc encoder present)

This is a SEPARATE tool from FFmpegConverter (which uses libaom-av1). The two
produce incomparable benchmark results because they use different encoder
implementations with different quality/bitrate curves. Results are labelled
"ffmpeg_nvenc" to distinguish them in the database.

Quality parameter: CQ (Constant Quality), range 1–51, descending
(lower value = higher quality, matching libaom CRF semantics).
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Union

from ..config import TELEMETRY_INTERVAL, ffmpeg_wall_timeout_for
from ..ffmpeg import FFmpegProcess
from ..logger import get_logger
from ..telemetry import TelemetryMonitor
from .base import BaseConverter

log = get_logger(__name__)


class FFmpegNvencConverter(BaseConverter):
    """Convert still images via FFmpeg's NVIDIA hardware-accelerated encoders.

    Requires FFmpeg with NVENC SDK (av1_nvenc, hevc_nvenc). Quality is mapped
    from the 0-100 "higher is better" scale to NVENC's CQ (Constant Quality)
    range 0-51 via: CQ = round((100 - quality) / 2), clamped to [0, 51].
    This is a separate tool from FFmpegConverter (which uses libaom-av1),
    producing incomparable results; database records are labelled "ffmpeg_nvenc".
    """

    FORMAT_PARAMS = {
        "avif": lambda q: ["-c:v", "av1_nvenc", "-cq", str(round(max(0.0, min(51.0, (100.0 - q) / 2.0)))), "-preset", "p7", "-tune", "hq", "-rc", "vbr", "-b:v", "0", "-pix_fmt", "yuv420p"],
        "heic": lambda q: ["-c:v", "hevc_nvenc", "-cq", str(round(max(0.0, min(51.0, (100.0 - q) / 2.0)))), "-preset", "p7", "-tune", "hq", "-rc", "vbr", "-b:v", "0", "-pix_fmt", "yuv420p"],
    }

    def __init__(self, ffmpeg_path: str):
        """Initialize NVENC converter.

        Args:
            ffmpeg_path: Path to FFmpeg binary with NVENC support.
        """
        super().__init__()
        self.ffmpeg_path = ffmpeg_path

    def get_name(self) -> str:
        """Return the converter name."""
        return "ffmpeg_nvenc"

    def supported_formats(self) -> List[str]:
        """Return list of supported output formats."""
        return ["avif", "heic"]

    def convert(
        self,
        input_path: str,
        output_path: str,
        target_format: str,
        quality: Union[int, float],
        use_gpu: bool = True,
        is_intermediate: bool = False,
        run_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Convert a single image via NVIDIA hardware acceleration.

        Args:
            input_path: Path to input image.
            output_path: Path where output should be written.
            target_format: Output format ('avif' or 'heic').
            quality: Quality value 0-100 (higher is better); mapped to NVENC CQ 0-51.
            use_gpu: Always True (parameter for interface compatibility).
            is_intermediate: Unused (NVENC settings are fixed).
            run_id: Optional batch run ID for telemetry.

        Returns:
            Dict with conversion result including success status, duration, telemetry,
            and error details.
        """
        if self.is_broken:
            return {"success": False, "error": f"{self.get_name()} is broken"}

        param_builder = self.FORMAT_PARAMS.get(target_format)
        if not param_builder:
            return {"success": False, "error": f"Unsupported format: {target_format}"}

        params: List[str] = list(param_builder(quality))
        # Ensure we only encode ONE frame for still images
        if "-frames:v" not in params:
            params.extend(["-frames:v", "1"])

        global_opts = ["-y", "-hide_banner", "-nostats", "-progress", "pipe:1"]
        padding = ["-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2"]
        args = global_opts + ["-i", input_path] + padding + params + [output_path]

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

        if success:
            if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
                success = False
                error = f"ffmpeg_nvenc claimed success but output is missing or empty: {output_path}"

        if success:
            self._reset_failures()
        else:
            self._mark_failure()
            if result.fatal:
                self.is_broken = True
                log.error(
                    "[FATAL] ffmpeg_nvenc encountered an unrecoverable error: %s",
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
