# app/core/calibrator.py
"""Serial SSIM-targeted quality search.

Ports the main app's predictive secant + binary-fallback search, stripped of
GPU, MS-SSIM, stop-events and process pools. Pure: depends only on config and
similarity. Scores via an injectable score_fn (defaults to similarity.score_ssim)
so the loop is testable without native encoders.
"""

import os
from pathlib import Path

from .config import (
    TARGET_SSIM,
    MAX_CALIBRATION_ITERS,
    CALIBRATION_SSIM_TOLERANCE,
    quality_range_for,
    quality_direction_for,
)
from .logger import get_logger

log = get_logger(__name__)


def _next_quality_guess(initial_quality, history_q, history_ssim, target_ssim, low, high, use_float):
    """Predictive secant step from the last two points; bisection fallback.

    Returns the next quality to try, or None when the search is stuck.
    """
    if not history_q and initial_quality is not None:
        mid = float(initial_quality)
    elif len(history_q) >= 2:
        q1, q2 = history_q[-2], history_q[-1]
        s1, s2 = history_ssim[-2], history_ssim[-1]
        if abs(s2 - s1) < 1e-6:
            mid = (low + high) / 2.0
        else:
            mid = q2 + (target_ssim - s2) * (q2 - q1) / (s2 - s1)
    else:
        mid = (low + high) / 2.0

    mid = max(low, min(high, mid))
    mid_to_apply = round(mid, 2) if use_float else int(round(mid))

    if history_q and mid_to_apply == history_q[-1]:
        mid = (low + high) / 2.0
        mid_to_apply = round(mid, 2) if use_float else int(round(mid))
        if mid_to_apply == history_q[-1]:
            return None
    return mid_to_apply


def find_optimal_quality(
    converter,
    input_path,
    target_format,
    tool,
    output_dir,
    *,
    target_ssim=TARGET_SSIM,
    max_iters=MAX_CALIBRATION_ITERS,
    initial_quality=None,
    tolerance=CALIBRATION_SSIM_TOLERANCE,
    orig_rgb=None,
    score_fn=None,
):
    """Find the smallest-output quality whose SSIM meets target_ssim.

    Returns a dict {quality_found, ssim_achieved, iterations, history, best_path,
    output_size_bytes, duration_ms} on success, or {"error": ...} on failure.
    """
    if score_fn is None:
        from .similarity import score_ssim as score_fn

    qr = quality_range_for(tool, target_format)
    low, high = float(qr[0]), float(qr[1])
    ascending = quality_direction_for(tool, target_format) == "ascending"
    use_float = isinstance(qr[0], float) or isinstance(qr[1], float)

    history_q = []
    history_ssim = []
    best_quality = None
    best_ssim = -1.0
    best_path = None
    best_size = float("inf")
    best_duration = 0.0
    last_error = "no successful quality point"

    os.makedirs(output_dir, exist_ok=True)
    stem = Path(input_path).stem

    iterations = 0
    while iterations < max_iters:
        iterations += 1
        q = _next_quality_guess(
            initial_quality if iterations == 1 else None,
            history_q, history_ssim, target_ssim, low, high, use_float,
        )
        if q is None:
            break

        out_path = os.path.join(output_dir, f"{stem}_{tool}_{iterations}.{target_format}")
        result = converter.convert(input_path, out_path, target_format, q, is_intermediate=True)
        if isinstance(result, dict):
            from app.core.converters.base import ConvertResult
            result = ConvertResult(
                success=result.get("success", False),
                error=result.get("error"),
                duration_ms=result.get("duration_ms", 0.0),
                telemetry=result.get("telemetry", {}),
                parameters_used=result.get("parameters_used", {}),
                fatal_error=result.get("fatal_error", False),
                bytes_written=result.get("bytes_written", 0),
                total_overhead_ms=result.get("total_overhead_ms"),
            )

        if not result.success:
            last_error = result.error or "converter error"
            if result.fatal_error:
                log.error("Calibration aborted (fatal) for %s via %s", input_path, tool)
                break
            # Shrink the range away from the failing end and retry.
            if ascending:
                low = q + (0.1 if use_float else 1)
            else:
                high = q - (0.1 if use_float else 1)
            continue

        current_ssim = score_fn(input_path, out_path, orig_rgb=orig_rgb)
        if current_ssim == -1.0:
            last_error = "similarity scoring failed"
            if ascending:
                low = q + (0.1 if use_float else 1)
            else:
                high = q - (0.1 if use_float else 1)
            continue

        current_size = result.get("bytes_written") or os.path.getsize(out_path)
        history_q.append(q)
        history_ssim.append(current_ssim)

        # Best tracking: prefer the smallest output that meets target; if nothing
        # meets target yet, keep the highest-SSIM attempt as a best-effort.
        if current_ssim >= target_ssim:
            if current_size < best_size:
                best_quality, best_ssim, best_path, best_size, best_duration = (
                    q, current_ssim, out_path, current_size, result.get("duration_ms", 0.0)
                )
        elif best_size == float("inf") and current_ssim > best_ssim:
            best_quality, best_ssim, best_path, best_duration = (
                q, current_ssim, out_path, result.get("duration_ms", 0.0)
            )

        # Binary bounds (safety net around the secant step).
        if ascending:
            if current_ssim < target_ssim:
                low = q
            else:
                high = q
        else:
            if current_ssim < target_ssim:
                high = q
            else:
                low = q

        if abs(current_ssim - target_ssim) <= tolerance:
            break
        if len(history_ssim) >= 3:
            d1 = abs(history_ssim[-1] - history_ssim[-2])
            d2 = abs(history_ssim[-2] - history_ssim[-3])
            if d1 < 0.0001 and d2 < 0.0001:
                break

    if best_quality is None:
        return {"error": f"Calibration failed: {last_error}"}

    if best_size == float("inf"):
        try:
            best_size = os.path.getsize(best_path)
        except OSError:
            best_size = 0

    return {
        "quality_found": best_quality,
        "ssim_achieved": best_ssim,
        "iterations": iterations,
        "history": list(zip(history_q, history_ssim)),
        "best_path": best_path,
        "output_size_bytes": int(best_size),
        "duration_ms": best_duration,
    }
