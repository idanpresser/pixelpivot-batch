"""Task 011 - tool/format-aware quality fallback (CRF-safe defaults).

The heuristic value stored per (category, bucket, format, tool) is expressed in
each encoder's NATIVE scalar:

  - ffmpeg avif  -> libaom-av1 -crf (valid 0..63, lower = better)
  - magick/vips/sharp avif/webp -> -quality 0..100 (higher = better)
  - jxl (all tools) -> 0..100 quality; converters map to Butteraugli distance

A single tool-agnostic fallback (1.0 for jxl else 80.0) is therefore unsafe:
  - ffmpeg avif gets -crf 80 (outside libaom's 0..63 range -> worst quality)
  - jxl gets quality 1.0 -> distance 9.9 (near-worst), despite stored data = 90

These tests pin a tool/format-aware fallback sourced from config.py.
"""

from pathlib import Path

import pytest

from app.core.heuristic_interpolator import HeuristicInterpolator

CRF_MAX = 63  # libaom-av1 valid CRF ceiling


def _empty_interpolator(tmp_path: Path) -> HeuristicInterpolator:
    # A non-existent table forces every lookup down the fallback path.
    return HeuristicInterpolator(tmp_path / "no_such_table.json")


def test_default_quality_for_ffmpeg_avif_is_crf_valid():
    from app.core.config import default_quality_for

    q = default_quality_for("ffmpeg", "avif")
    assert 0 <= q <= CRF_MAX
    assert q != 80.0


def test_default_quality_for_quality_tool_avif_is_high():
    from app.core.config import default_quality_for

    # magick avif is 0..100 higher-is-better; it must NOT collapse to a CRF value.
    assert default_quality_for("magick", "avif") >= 70


def test_default_quality_for_jxl_matches_quality_scale():
    from app.core.config import default_quality_for

    # jxl scalar is 0..100; the converters map it to distance. A value of ~1.0
    # would mean distance ~9.9 (garbage). Must be a high quality on any tool.
    assert default_quality_for("ffmpeg", "jxl") >= 70
    assert default_quality_for("vips", "jxl") >= 70


def test_interpolator_empty_table_ffmpeg_avif_is_crf_valid(tmp_path):
    interp = _empty_interpolator(tmp_path)
    q = interp.get_interpolated_quality("general", "avif", "ffmpeg", 4000, 3000)
    assert 0 <= q <= CRF_MAX
    assert q != 80.0


def test_interpolator_empty_table_magick_avif_stays_high(tmp_path):
    interp = _empty_interpolator(tmp_path)
    q = interp.get_interpolated_quality("general", "avif", "magick", 4000, 3000)
    assert q >= 70


def test_interpolator_empty_table_jxl_is_quality_scale(tmp_path):
    interp = _empty_interpolator(tmp_path)
    q = interp.get_interpolated_quality("general", "jxl", "ffmpeg", 4000, 3000)
    assert q >= 70


def test_probe_quality_failure_is_tool_aware(monkeypatch, tmp_path):
    import app.core.utils as utils
    from app.batch_api.orchestrator import BatchOrchestrator

    def _boom(_path):
        raise RuntimeError("probe failed")

    monkeypatch.setattr(utils, "probe_image_dimensions", _boom)

    orch = BatchOrchestrator()
    q = orch._probe_quality(str(tmp_path / "missing.png"), "general", "ffmpeg", "avif")
    assert 0 <= q <= CRF_MAX
    assert q != 80.0
