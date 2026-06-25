# app/core/similarity.py
"""In-process SSIM scoring for calibration.

Decodes images to RGB via pyvips (no temp files) and computes the reference
Wang et al. SSIM with OpenCV. Standard SSIM scale, so config.TARGET_SSIM stays
meaningful. No native binary, no subprocess.
"""

import cv2
import numpy as np
import pyvips

from . import config
from .logger import get_logger

log = get_logger(__name__)

# SSIM stabilisation constants for 8-bit data (L = 255).
_C1 = (0.01 * 255) ** 2
_C2 = (0.03 * 255) ** 2
_WIN = (11, 11)
_SIGMA = 1.5


def decode_rgb(path: str) -> np.ndarray:
    """Decode any supported image to a contiguous H*W*3 uint8 array via pyvips."""
    arr = pyvips.Image.new_from_file(path).numpy()
    if arr.ndim == 2:
        arr = np.stack([arr, arr, arr], axis=-1)
    return np.ascontiguousarray(arr[:, :, :3])


def compute_ssim(a: np.ndarray, b: np.ndarray) -> float:
    """Mean Wang SSIM over two H*W*3 uint8 arrays (11x11 Gaussian, sigma 1.5)."""
    a = a.astype(np.float32)
    b = b.astype(np.float32)
    mu1 = cv2.GaussianBlur(a, _WIN, _SIGMA)
    mu2 = cv2.GaussianBlur(b, _WIN, _SIGMA)
    mu1_sq = mu1 * mu1
    mu2_sq = mu2 * mu2
    mu1_mu2 = mu1 * mu2
    sigma1_sq = cv2.GaussianBlur(a * a, _WIN, _SIGMA) - mu1_sq
    sigma2_sq = cv2.GaussianBlur(b * b, _WIN, _SIGMA) - mu2_sq
    sigma12 = cv2.GaussianBlur(a * b, _WIN, _SIGMA) - mu1_mu2
    ssim_map = ((2 * mu1_mu2 + _C1) * (2 * sigma12 + _C2)) / (
        (mu1_sq + mu2_sq + _C1) * (sigma1_sq + sigma2_sq + _C2)
    )
    return float(ssim_map.mean())


def _maybe_downscale(original: np.ndarray, candidate: np.ndarray):
    """Downscale both arrays equally when over HUGE_IMAGE_THRESHOLD, to bound
    cv2 SSIM memory (float32 x3 buffers). Single source for SSIM memory safety.

    Reads config.HUGE_IMAGE_THRESHOLD at call time so the limit stays a single
    source of truth (and is overridable in tests).
    """
    h, w = original.shape[:2]
    if h * w <= config.HUGE_IMAGE_THRESHOLD:
        return original, candidate
    scale = (config.HUGE_IMAGE_THRESHOLD / float(h * w)) ** 0.5
    new_w = max(11, int(w * scale))
    new_h = max(11, int(h * scale))
    log.info("SSIM rescale %dx%d -> %dx%d (over HUGE threshold)", w, h, new_w, new_h)
    o = cv2.resize(original, (new_w, new_h), interpolation=cv2.INTER_AREA)
    c = cv2.resize(candidate, (new_w, new_h), interpolation=cv2.INTER_AREA)
    return o, c


def score_ssim(orig_path: str, conv_path: str, *, orig_rgb: np.ndarray = None) -> float:
    """Decode original (or reuse orig_rgb) and candidate, return SSIM.

    Returns -1.0 on any failure (decode error, dimension mismatch) so the
    calibration search treats that quality point as failed.
    """
    try:
        original = orig_rgb if orig_rgb is not None else decode_rgb(orig_path)
        candidate = decode_rgb(conv_path)
        if original.shape != candidate.shape:
            log.warning(
                "SSIM shape mismatch %s vs %s for %s", original.shape, candidate.shape, conv_path
            )
            return -1.0
        original, candidate = _maybe_downscale(original, candidate)
        return compute_ssim(original, candidate)
    except Exception as e:
        log.warning("SSIM scoring failed for %s: %s", conv_path, e)
        return -1.0
