"""Constraints — hardware encoder resolution limits and calibration safety thresholds."""

# Hardware Encoder Resolution Limits
# Most consumer NVIDIA GPUs (Ampere/Ada Lovelace) have an 8192px limit for AV1/HEVC.
ENCODER_LIMITS = {
    "ffmpeg_nvenc": {
        "avif": {"max_width": 8192, "max_height": 8192},
        "hevc": {"max_width": 8192, "max_height": 8192},
        "h264": {"max_width": 4096, "max_height": 4096},
    },
    # Software encoders usually don't have hard limits but become extremely slow/unstable
    "ffmpeg": {
        "avif": {"max_width": 16384, "max_height": 16384},
        "jxl": {"max_width": 16384, "max_height": 16384},
        "webp": {"max_width": 16383, "max_height": 16383},  # WebP hard limit is 16383
    },
    "sharp": {
        "webp": {"max_width": 16383, "max_height": 16383},
    }
}

from .config import (
    MASSIVE_IMAGE_THRESHOLD,
    HUGE_IMAGE_THRESHOLD,
    VRAM_SAFE_THRESHOLD,
    MAX_CALIBRATION_ITERS as MAX_CALIBRATION_ITERATIONS
)

# Calibration Safety
CALIBRATION_TIMEOUT_SECONDS = 60.0


def is_resolution_supported(tool: str, fmt: str, width: int, height: int) -> tuple[bool, str]:
    """Check if a resolution is supported by the tool+format combination.

    Args:
        tool: Encoder tool (e.g. "ffmpeg", "ffmpeg_nvenc", "sharp").
        fmt: Target format (e.g. "webp", "avif", "jxl").
        width: Image width in pixels.
        height: Image height in pixels.

    Returns:
        (is_supported, error_message) tuple. is_supported is True if the resolution
        is valid; error_message explains why if False.
    """
    # 🚨 Prevent Native Segfaults on Degenerate Dimensions BEFORE checking limits
    if fmt in ("jxl", "avif") and (width < 16 or height < 16):
        return False, f"Degenerate dimensions ({width}x{height}) trigger native crashes in {fmt} encoders."

    if tool not in ENCODER_LIMITS:
        return True, ""

    limits = ENCODER_LIMITS[tool].get(fmt)
    if not limits:
        return True, ""

    max_w = limits.get("max_width")
    max_h = limits.get("max_height")

    if max_w and width > max_w:
        return False, f"Width {width} exceeds {tool} limit of {max_w}"
    if max_h and height > max_h:
        return False, f"Height {height} exceeds {tool} limit of {max_h}"

    return True, ""
