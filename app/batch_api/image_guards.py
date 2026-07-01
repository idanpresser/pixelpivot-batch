"""Shared resource/image guardrails for batch and calibration runs.

Single source of truth for the preflight + image-partition checks the live
batch path performs, so the calibration runner enforces identical safety
limits without duplicating thresholds (which live in app.core.config).
"""

import shutil
from pathlib import Path
from typing import Dict, List, Tuple

import psutil

from ..core.config import (
    MASSIVE_IMAGE_THRESHOLD,
    MIN_AVAILABLE_RAM_BYTES,
    MIN_FREE_DISK_BYTES,
)
from ..core.logger import get_logger

log = get_logger(__name__)


def preflight_resources(target_dir: str) -> None:
    """Validate available RAM and free disk before a run. Raises ValueError."""
    vm = psutil.virtual_memory()
    if vm.available < MIN_AVAILABLE_RAM_BYTES:
        raise ValueError(
            f"Critically low memory: {vm.available / (1024 * 1024):.1f} MB available."
        )
    target_path = Path(target_dir)
    target_path.mkdir(parents=True, exist_ok=True)
    _, _, free = shutil.disk_usage(str(target_path))
    if free < MIN_FREE_DISK_BYTES:
        raise ValueError("Insufficient disk space on target directory.")


def check_free_disk(target_dir: str) -> None:
    """Mid-run disk check. Raises ValueError if free space is critically low."""
    _, _, free = shutil.disk_usage(target_dir)
    if free < MIN_FREE_DISK_BYTES:
        raise ValueError("Insufficient disk space on target directory mid-run.")


def partition_images(
    paths: List[str], dim_cache: Dict[str, Tuple[int, int]]
) -> Tuple[List[str], List[dict]]:
    """Split paths into (usable, rejected).

    Rejects unreadable images (dims (0,0)) and images whose pixel count exceeds
    MASSIVE_IMAGE_THRESHOLD. Mirrors the orchestrator's upfront filter.
    """
    usable: List[str] = []
    errors: List[dict] = []
    for p in paths:
        w, h = dim_cache.get(p, (0, 0))
        if w == 0 and h == 0:
            errors.append({"path": p, "error": f"Image {Path(p).name} unreadable or corrupt — skipped."})
        elif w * h > MASSIVE_IMAGE_THRESHOLD:
            errors.append({"path": p, "error": (
                f"Image {Path(p).name} exceeds MASSIVE_IMAGE_THRESHOLD "
                f"({w}x{h} = {w*h} px > {MASSIVE_IMAGE_THRESHOLD} px) — rejected."
            )})
        else:
            usable.append(p)
    return usable, errors


def disk_pct_over_threshold(target_dir: str, threshold_pct: float) -> bool:
    """True when the RESOLVED target_dir volume is at/above threshold_pct full.

    Probes shutil.disk_usage(os.path.abspath(target_dir)) so it reads the volume
    the outputs actually land on (a network mount / external drive / separate
    logical volume), never a static system root like C:/ or /.
    """
    try:
        import os
        usage = shutil.disk_usage(os.path.abspath(target_dir))
    except OSError:
        return False  # can't probe -> do not block pickup
    if usage.total <= 0:
        return False
    used_pct = 100.0 * usage.used / usage.total
    return used_pct >= threshold_pct

