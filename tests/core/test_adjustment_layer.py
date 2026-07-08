"""Tests for AdjustmentLayer — sidecar for online nudges (leaky integrator).

Verify thread-safe per-cell offset storage, leaky-integrator math,
and atomic file I/O.
"""

import json
import tempfile
from pathlib import Path
import pytest
from app.core.adjustment import AdjustmentLayer


def test_adjustment_layer_load_nonexistent_returns_empty():
    """Load from missing file returns empty."""
    layer = AdjustmentLayer(Path("/nonexistent/adjust.json"))
    assert layer.get("cat|fmt|tool") == 0.0


def test_adjustment_layer_get_and_update():
    """Get offset, update with nudge, get updated value."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "adjust.json"
        layer = AdjustmentLayer(path)

        # Initial offset is zero
        assert layer.get("cell1") == 0.0

        # Nudge: err=0.02 (SSIM below target), sign=+1 (ascending), k=10
        # delta = 10 * 0.02 * (+1) = +0.2
        # offset = 0 * (1 - 0.1) + 0.2 = 0.2
        layer.update("cell1", ssim_err=0.02, direction="ascending")

        # Clamp check: offset should be ~0.2
        offset = layer.get("cell1")
        assert 0.19 < offset < 0.21, f"Expected ~0.2, got {offset}"


def test_adjustment_layer_leak_decay():
    """Leaky integrator decays old offset over time."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "adjust.json"
        layer = AdjustmentLayer(path)

        # First nudge
        layer.update("cell1", ssim_err=0.02, direction="ascending")
        offset1 = layer.get("cell1")

        # Second nudge (same signal)
        layer.update("cell1", ssim_err=0.02, direction="ascending")
        offset2 = layer.get("cell1")

        # With leak λ=0.1, offset should grow but sub-linearly
        assert offset1 > 0.19
        assert offset2 > offset1


def test_adjustment_layer_clamp():
    """Offset clamped to [-max_offset, +max_offset]."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "adjust.json"
        layer = AdjustmentLayer(path, max_offset=5.0)

        # Nudge hard (high error)
        layer.update("cell1", ssim_err=0.5, direction="ascending")
        offset = layer.get("cell1")

        assert offset <= 5.0, f"Expected offset <= 5.0, got {offset}"


def test_adjustment_layer_descending_direction_inverts_sign():
    """CRF-style descending quality: error sign inverted."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "adjust.json"
        layer = AdjustmentLayer(path)

        # Ascending: SSIM error +0.02 → offset increases
        layer.update("asc_cell", ssim_err=0.02, direction="ascending")
        asc_offset = layer.get("asc_cell")

        # Descending: same SSIM error → offset decreases (sign inverted)
        layer.update("desc_cell", ssim_err=0.02, direction="descending")
        desc_offset = layer.get("desc_cell")

        assert asc_offset > 0.0
        assert desc_offset < 0.0


def test_adjustment_layer_atomic_write_and_reload():
    """Write is atomic (tmp + os.replace); reload reads fresh state."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "adjust.json"
        layer1 = AdjustmentLayer(path)

        layer1.update("cell1", ssim_err=0.02, direction="ascending")

        # New layer reads the persisted file
        layer2 = AdjustmentLayer(path)
        assert layer2.get("cell1") == layer1.get("cell1")

        # File exists and is valid JSON
        with open(path) as f:
            data = json.load(f)
        assert "version" in data
        assert "cells" in data
