"""Task 008 - close the severed heuristic feedback loop.

The batch path historically wrote only batch_runs/batch_summary/batch_errors, never
the images/conversions tables the heuristic generators read from. So a table could
only ever be hand-seeded. These tests pin that a batch run persists per-conversion
analytics and that the generator can then build a table from batch-produced data.
"""

import json
import os
import sqlite3
from pathlib import Path

import pytest
from PIL import Image

from app.core.db.schema import init_db


def _make_image(path, size=(800, 600)):
    Image.new("RGB", size, "red").save(path)


def _init_mem_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn


def test_record_conversions_persists_image_and_conversion(tmp_path):
    from app.core.db.repositories.conversions import record_conversions, get_conversion_count

    img = tmp_path / "a.png"
    _make_image(img)
    conn = _init_mem_db()

    n = record_conversions(conn, [{
        "path": str(img), "category": "general", "format": "webp",
        "tool": "magick", "quality": 82.0, "success": True,
    }])
    conn.commit()

    assert n == 1
    assert get_conversion_count(conn) == 1
    row = conn.execute("SELECT width, height, category FROM images").fetchone()
    assert row["width"] == 800
    assert row["height"] == 600
    assert row["category"] == "general"


def test_generator_succeeds_over_batch_produced_db(tmp_path):
    from app.core.db.repositories.conversions import record_conversions
    from app.core.heuristic import generate_heuristic_table
    from app.core.config import HEURISTIC_MIN_SAMPLES

    # Distinct image paths so each yields its own conversion row (record_conversions
    # upserts the image by path+category): we need at least HEURISTIC_MIN_SAMPLES
    # samples for the cell to survive the min-sample gate (task_017).
    conn = _init_mem_db()
    records = []
    for i in range(HEURISTIC_MIN_SAMPLES):
        img = tmp_path / f"a{i}.png"
        _make_image(img)
        records.append({
            "path": str(img), "category": "general", "format": "webp",
            "tool": "magick", "quality": 82.0, "success": True,
        })
    record_conversions(conn, records)
    conn.commit()

    table_path = tmp_path / "t.json"
    generate_heuristic_table(conn=conn, table_path=table_path, weights_path=tmp_path / "w.json")

    from app.core.heuristic_interpolator import HeuristicInterpolator

    interp = HeuristicInterpolator(table_path)
    # All samples share one MP -> flat curve at 82.0; evaluating it returns 82.0.
    assert interp.get_interpolated_quality("general", "webp", "magick", 800, 600) == 82.0


def test_execute_batch_dispatches_analytics_records(tmp_path, monkeypatch):
    """execute_batch must hand per-conversion records to record_conversions for
    every converted image (the wiring). That record_conversions then persists
    rows + feeds the generator is covered by the two unit tests above."""
    from unittest.mock import MagicMock, patch

    import app.core.db.repositories.conversions as conv_mod
    from app.batch_api.orchestrator import BatchOrchestrator
    from app.batch_api.models import BatchRequest

    src = tmp_path / "src"
    src.mkdir()
    _make_image(src / "a.png")
    out = tmp_path / "out"

    monkeypatch.setattr("app.core.utils.probe_image_dimensions", lambda p: (800, 600))

    orch = BatchOrchestrator()
    orch.repo = MagicMock()

    class _Stub:
        is_broken = False

        def convert_batch(self, input_paths, output_dir, target_format, qualities,
                          run_id=None, suffix="", dimensions=None):
            return {"success_count": len(input_paths), "failure_count": 0, "errors": [], "telemetry": {}}

    orch.converters = {"magick": _Stub()}

    request = BatchRequest(
        source_dir=str(src), target_dir=str(out),
        target_format=["webp"], tool=["magick"], category=["general"],
    )

    captured = {}

    def _fake_record(conn, records):
        captured["records"] = records
        return len(records)

    monkeypatch.setattr(conv_mod, "record_conversions", _fake_record)
    with patch("app.batch_api.orchestrator.get_connection") as mock_conn:
        mock_conn.return_value.__enter__.return_value = MagicMock()
        orch.execute_batch(run_id=1, request=request)

    records = captured.get("records")
    assert records is not None and len(records) == 1
    rec = records[0]
    assert rec["category"] == "general"
    assert rec["format"] == "webp"
    assert rec["tool"] == "magick"
    assert rec["success"] is True
