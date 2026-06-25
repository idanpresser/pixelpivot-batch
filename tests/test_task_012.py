"""Task 012 - savings_pct accounting on partial runs; suffix single-source.

Defects in BatchOrchestrator.execute_batch's summary math:
  1. input_bytes was multiplied by len(plan) (the FULL matrix), counting cells
     that were skipped (unsupported tool / broken converter / aborted run).
  2. output bytes were counted by a blind rescan of predicted names, so a
     same-named file from a PRIOR run was attributed to this run.
  3. the suffix was recovered by a string round-trip through output_name.

These tests pin a suffix_for() single source of truth and savings math scoped
to cells that actually executed and outputs actually produced this run.
"""

import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.batch_api.models import BatchRequest
from app.batch_api.orchestrator import BatchOrchestrator, MatrixCell, output_name


# --------------------------------------------------------------------------
# suffix_for() - single source of truth for the per-cell output suffix
# --------------------------------------------------------------------------

def test_suffix_for_single_category():
    from app.batch_api.orchestrator import suffix_for

    cell = MatrixCell(category="general", tool="magick", target_format="webp")
    assert suffix_for(cell, multi_category=False) == "_magick"


def test_suffix_for_multi_category():
    from app.batch_api.orchestrator import suffix_for

    cell = MatrixCell(category="web", tool="ffmpeg", target_format="avif")
    assert suffix_for(cell, multi_category=True) == "_web_ffmpeg"


def test_output_name_is_built_from_suffix_for():
    from app.batch_api.orchestrator import suffix_for

    cell = MatrixCell(category="web", tool="ffmpeg", target_format="avif")
    for multi in (False, True):
        suffix = suffix_for(cell, multi_category=multi)
        assert output_name("photo", cell, multi_category=multi) == f"photo{suffix}.avif"


# --------------------------------------------------------------------------
# savings accounting
# --------------------------------------------------------------------------

class _StubConverter:
    """Converter stub. Writes a fixed-size output per input when write_bytes is
    set; otherwise produces nothing (simulating a fully-failed cell)."""

    def __init__(self, write_bytes=None):
        self.is_broken = False
        self._write_bytes = write_bytes

    def convert_batch(self, input_paths, output_dir, target_format, qualities,
                      run_id=None, suffix="", dimensions=None):
        produced = 0
        if self._write_bytes is not None:
            os.makedirs(output_dir, exist_ok=True)
            for p in input_paths:
                out = Path(output_dir) / f"{Path(p).stem}{suffix}.{target_format}"
                out.write_bytes(b"x" * self._write_bytes)
                produced += 1
        return {
            "success_count": produced,
            "failure_count": len(input_paths) - produced,
            "duration_ms": 1.0,
            "telemetry": {},
            "errors": [] if produced else [{"path": p, "error": "stub fail"} for p in input_paths],
            "bytes_written": produced * self._write_bytes if self._write_bytes is not None else 0,
        }


def _make_orch():
    with patch("app.batch_api.orchestrator.HeuristicInterpolator"):
        with patch("app.batch_api.orchestrator.BatchRepository"):
            return BatchOrchestrator()


def _run_and_capture_summary(orch, request, run_id=1):
    with patch("app.batch_api.orchestrator.get_connection") as mock_conn:
        mock_conn.return_value.__enter__.return_value = MagicMock()
        orch.execute_batch(run_id=run_id, request=request)
    saved = {}
    for call in orch.repo.save_summary.call_args_list:
        saved.update(call.kwargs)
    return saved


def test_savings_counts_only_executed_cells(tmp_path, monkeypatch):
    monkeypatch.setattr("app.core.utils.probe_image_dimensions", lambda p: (400, 300))
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.jpg").write_bytes(b"x" * 1000)  # 1000-byte input
    out = tmp_path / "out"

    orch = _make_orch()
    # Only magick is registered; the "sharp" cell is therefore skipped.
    orch.converters = {"magick": _StubConverter(write_bytes=500)}

    request = BatchRequest(
        source_dir=str(src),
        target_dir=str(out),
        target_format=["webp"],
        tool=["magick", "sharp"],
        category=["general"],
    )
    saved = _run_and_capture_summary(orch, request)

    # 1 executed cell: input=1000, output=500 -> 50%.
    # Buggy denominator (len(plan)=2 -> input=2000) would give 75%.
    assert saved["savings_pct"] == pytest.approx(50.0, abs=0.5)


def test_stale_prior_output_not_attributed_to_this_run(tmp_path, monkeypatch):
    monkeypatch.setattr("app.core.utils.probe_image_dimensions", lambda p: (400, 300))
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.jpg").write_bytes(b"x" * 1000)
    out = tmp_path / "out"
    out.mkdir()

    # A leftover output from a previous run, matching the predicted name.
    stale = out / "a_magick.webp"
    stale.write_bytes(b"x" * 9999)
    old = time.time() - 10000
    os.utime(stale, (old, old))

    orch = _make_orch()
    orch.converters = {"magick": _StubConverter(write_bytes=None)}  # produces nothing

    request = BatchRequest(
        source_dir=str(src),
        target_dir=str(out),
        target_format=["webp"],
        tool=["magick"],
        category=["general"],
    )
    saved = _run_and_capture_summary(orch, request)

    # Nothing produced this run -> output_bytes 0 -> savings 100%.
    # Blind rescan would count the stale 9999 and yield a large negative savings.
    assert saved["savings_pct"] == pytest.approx(100.0, abs=0.5)
