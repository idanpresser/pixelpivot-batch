# app/core/converters/chunk_sizing.py
"""Pure resource-aware chunk sizing (e5.2).

Deterministic model: a decoded image costs ~4 bytes/pixel (raw RGBA), so an
in-flight chunk of N images at M megapixels needs ~= 4 * M * 1e6 * N bytes. Given
a RAM budget, the max chunk size is budget / (4 * M * 1e6), clamped to [1, ceiling].
"""
from __future__ import annotations

import math

_BYTES_PER_MP = 4 * 1_000_000  # 4 bytes/pixel * 1e6 pixels per megapixel


def dynamic_max_files(megapixels: float, ram_budget_bytes: float, ceiling: int) -> int:
    """Return the RAM-bounded max files per chunk, clamped to [1, ceiling]."""
    if megapixels <= 0:
        return ceiling
    per_image = _BYTES_PER_MP * megapixels
    fit = int(math.floor(ram_budget_bytes / per_image))
    return max(1, min(ceiling, fit))
