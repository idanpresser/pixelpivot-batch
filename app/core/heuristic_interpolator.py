"""Heuristic Interpolator — evaluates log-linear quality curves per (category, format, tool).

Loads heuristic_table.json and evaluates quality = a + b*log10(megapixels) at an
image's exact pixel count. Clamps the result to the curve's observed megapixel range
(no extrapolation) and to the encoder's native quality range.
"""

import json
import math
from pathlib import Path
from typing import Dict, Any, Optional

from .logger import get_logger

log = get_logger(__name__)


class HeuristicInterpolator:
    """
    Evaluates a fitted quality=f(MP) curve (quality = a + b * log10(megapixels))
    per (category, format, tool) at an image's exact pixel count. The result is
    clamped to the curve's observed MP range (no wild extrapolation) and to the
    encoder's valid native quality range.
    """
    def __init__(self, heuristic_table_path: Path):
        """Initialize the interpolator with a heuristic table.

        Args:
            heuristic_table_path: Path to heuristic_table.json.
        """
        self.table = self._load_table(heuristic_table_path)
        self.version = self.table.get("version", "unknown")

    def _load_table(self, path: Path) -> Dict[str, Any]:
        """Load the heuristic table from a JSON file.

        Args:
            path: Path to the heuristic_table.json file.

        Returns:
            Loaded table dict, or empty dict if file doesn't exist or parse fails.
        """
        if not path.exists():
            log.warning(f"Heuristic table not found at {path}. Using empty table.")
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            log.error(f"Failed to load heuristic table: {e}")
            return {}

    def get_interpolated_quality(
        self, category: str, format: str, tool: str, width: int, height: int
    ) -> float:
        """Evaluate the fitted log-linear curve at an image's megapixel count.

        Clamps the result to the curve's observed megapixel range and to the
        encoder's native valid quality range. Falls back to tool/format-native
        default if no curve exists for the (category, format, tool) combo.

        Args:
            category: Image category (e.g. "highRes", "edgeCase").
            format: Target format (e.g. "webp", "avif", "jxl").
            tool: Encoder tool (e.g. "ffmpeg", "imagemagick", "sharp").
            width: Image width in pixels.
            height: Image height in pixels.

        Returns:
            Interpolated or fallback quality in the encoder's native scalar range.
        """
        from .otel import span
        with span("quality_curve"):
            from .config import default_quality_for, quality_range_for

            megapixels = (width * height) / 1_000_000.0

            cell = self.table.get(category, {}).get(format, {}).get(tool)
            if not cell or "a" not in cell or "b" not in cell:
                # No fitted curve for this combo: fall back to a tool/format-native default.
                fallback = default_quality_for(tool, format)
                log.debug(f"No heuristic curve for {category}|{format}|{tool}. Falling back to default: {fallback}")
                return fallback

            # Clamp MP to the observed range so we evaluate the curve, never extrapolate.
            mp = max(cell.get("mp_min", megapixels), min(megapixels, cell.get("mp_max", megapixels)))
            if mp <= 0:
                return default_quality_for(tool, format)

            q = cell["a"] + cell["b"] * math.log10(mp)

            # Clamp the result to the encoder's valid native quality range.
            lo, hi = quality_range_for(tool, format)
            q = max(lo, min(q, hi))

            return round(float(q), 2)
