"""Task 017 - min-sample gate + median aggregation for table cells.

Even with ~1000 samples per category, the table fragments data across
buckets x formats x tools, so corner cells often hold only a handful of samples.
The generator emitted a plain MEAN regardless, with no minimum-sample gate and no
outlier resistance. These tests pin that under-sampled cells are dropped (so the
interpolator falls back to a tool/format-native default) and that surviving cells
aggregate with the MEDIAN, not the mean.
"""

import json
import sqlite3

from app.core.config import HEURISTIC_MIN_SAMPLES, default_quality_for
from app.core.heuristic import generate_heuristic_table
from app.core.heuristic_interpolator import HeuristicInterpolator


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE images (id INTEGER PRIMARY KEY, category TEXT, "
        "width INTEGER, height INTEGER, arrival_time TEXT)"
    )
    conn.execute(
        "CREATE TABLE conversions (image_id INTEGER, format TEXT, tool TEXT, "
        "quality REAL, success BOOLEAN)"
    )
    return conn


def _add(conn, img_id, category, w, h, fmt, tool, quality):
    conn.execute(
        "INSERT INTO images VALUES (?, ?, ?, ?, NULL)", (img_id, category, w, h)
    )
    conn.execute(
        "INSERT INTO conversions VALUES (?, ?, ?, ?, 1)", (img_id, fmt, tool, quality)
    )


def test_config_exposes_min_samples():
    assert isinstance(HEURISTIC_MIN_SAMPLES, int)
    # >= 3 so the [low] + [high]*(n-1) median fixture below is unambiguous.
    assert HEURISTIC_MIN_SAMPLES >= 3


def test_under_sampled_cell_is_gated_to_fallback(tmp_path):
    conn = _conn()
    # 'thin' category: a single (small, webp, magick) cell with one fewer sample
    # than the gate -> must be dropped, leaving the combo empty so the
    # interpolator falls back instead of trusting a noisy handful.
    img = 1
    for _ in range(HEURISTIC_MIN_SAMPLES - 1):
        _add(conn, img, "thin", 400, 300, "webp", "magick", 50.0)
        img += 1
    conn.commit()

    table_path = tmp_path / "t.json"
    generate_heuristic_table(
        conn=conn, table_path=table_path, weights_path=tmp_path / "w.json"
    )

    interp = HeuristicInterpolator(table_path)
    q = interp.get_interpolated_quality("thin", "webp", "magick", 400, 300)
    assert q == default_quality_for("magick", "webp")
    assert q != 50.0


# NOTE: the median-aggregation half of task_017 was superseded by task_016, which
# fits a curve over raw per-image points (no per-cell mean/median to choose). The
# min-sample gate above is the durable part and is enforced by both tasks.
