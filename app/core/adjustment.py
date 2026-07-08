"""Adjustment Layer — sidecar for online heuristic nudges (leaky integrator).

Stores per-cell offset corrections in a JSON sidecar file, independent of the
canonical heuristic_table.json. Offset applied on top of the fitted curve:

    q_adjusted = native_clamp((a + b*log10(MP)) + offset[cell])

Implements leaky-integrator nudge: offset += k*err*sign - lambda*offset.
Thread-safe via module-level lock and atomic file I/O (tmp + os.replace).
"""

import json
import os
import threading
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

from .logger import get_logger
from .config import NUDGE_GAIN_K, NUDGE_LEAK_LAMBDA, NUDGE_MAX_OFFSET

log = get_logger(__name__)

_ADJUSTMENT_LOCK = threading.RLock()


class AdjustmentLayer:
    """Manages per-cell offset corrections via leaky-integrator nudge."""

    def __init__(self, path: Path, max_offset: float = NUDGE_MAX_OFFSET):
        """Initialize adjustment layer, loading sidecar if it exists.

        Args:
            path: Path to heuristic_adjust.json.
            max_offset: Max absolute offset (clamping).
        """
        self.path = Path(path)
        self.max_offset = max_offset
        self.cells = {}
        self._load()

    def _load(self):
        """Load sidecar from disk if it exists."""
        if not self.path.exists():
            log.debug(f"Adjustment file {self.path} not found; starting empty.")
            self.cells = {}
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.cells = data.get("cells", {})
            log.debug(f"Loaded adjustment layer from {self.path}: {len(self.cells)} cells.")
        except Exception as e:
            log.warning(f"Failed to load adjustment file {self.path}: {e}. Starting empty.")
            self.cells = {}

    def _save(self):
        """Atomically write sidecar to disk (tmp + os.replace)."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_suffix(".json.tmp")
        try:
            payload = {"version": "1.0.0", "cells": self.cells}
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            os.replace(tmp_path, self.path)
            log.debug(f"Adjustment layer persisted to {self.path}.")
        except Exception as e:
            log.error(f"Failed to save adjustment file {self.path}: {e}")
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)

    def get(self, cell: str) -> float:
        """Get current offset for a cell (thread-safe).

        Args:
            cell: Cell key (e.g. "highRes|webp|magick").

        Returns:
            Current offset in native quality units, or 0.0 if not set.
        """
        with _ADJUSTMENT_LOCK:
            cell_data = self.cells.get(cell, {})
            return float(cell_data.get("offset", 0.0))

    def update(
        self,
        cell: str,
        ssim_err: float,
        direction: str,
        gain_k: float = NUDGE_GAIN_K,
        leak_lambda: float = NUDGE_LEAK_LAMBDA,
    ):
        """Update offset via leaky-integrator nudge (thread-safe).

        offset_new = (offset_old * (1 - lambda)) + (gain_k * err * sign)

        Clamped to [-max_offset, +max_offset].

        Args:
            cell: Cell key (e.g. "highRes|webp|magick").
            ssim_err: SSIM error (target - measured); >0 means measured below target.
            direction: "ascending" or "descending". Inverts sign for CRF-style (lower=better).
            gain_k: Nudge gain (quality points per SSIM unit).
            leak_lambda: Leak rate (0.1 = 10% forget per update).
        """
        with _ADJUSTMENT_LOCK:
            old_offset = float(self.cells.get(cell, {}).get("offset", 0.0))

            # Compute sign based on encoder direction
            sign = 1.0 if direction == "ascending" else -1.0

            # Nudge: err > 0 => measured below target => increase quality (positive offset for ascending)
            delta = gain_k * ssim_err * sign
            new_offset = old_offset * (1.0 - leak_lambda) + delta

            # Clamp to [-max_offset, +max_offset]
            new_offset = max(-self.max_offset, min(self.max_offset, new_offset))

            # Store (rounded for cleaner JSON)
            self.cells[cell] = {
                "offset": round(new_offset, 4),
                "samples": self.cells.get(cell, {}).get("samples", 0) + 1,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }

            log.debug(
                f"Nudged {cell}: err={ssim_err:.4f}, sign={sign:+.1f}, "
                f"delta={delta:.4f}, offset {old_offset:.4f} -> {new_offset:.4f}"
            )

            # Persist immediately
            self._save()

    def reset(self):
        """Clear all offsets (e.g., after full recalibration)."""
        with _ADJUSTMENT_LOCK:
            self.cells = {}
            self._save()
            log.info("Adjustment layer reset to empty.")

    def reset_cell(self, cell: str):
        """Clear offset for a single cell."""
        with _ADJUSTMENT_LOCK:
            self.cells.pop(cell, None)
            self._save()
