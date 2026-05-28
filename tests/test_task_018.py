"""Task 018 - remove the unused time-of-day buckets.

`heuristic.py` computes a whole time-of-day analysis on every regeneration
(get_time_bucket, time_group_data, flat_lookup_by_time) and writes
"time_buckets" / "lookup_by_time" into the weights file, but nothing reads it:
HeuristicInterpolator never opens the weights file at all. These tests pin that
the dead code is gone and that removing it does not perturb the heuristic TABLE
cells the engine actually consumes.
"""

import json
import sqlite3

import app.core.heuristic as heuristic_mod
from app.core.heuristic import generate_heuristic_table


def _fixture_conn():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE images (id INTEGER PRIMARY KEY, category TEXT, "
        "width INTEGER, height INTEGER, arrival_time TEXT)"
    )
    conn.execute(
        "CREATE TABLE conversions (image_id INTEGER, format TEXT, tool TEXT, "
        "quality REAL, success BOOLEAN)"
    )
    # 0.12 MP -> small. Five samples (>= the min-sample gate) with median 85.0.
    # arrival_time deliberately spans daytime and nighttime hours: this used to
    # crash a latent bug in the (now-removed) time-of-day accumulation.
    qualities = [80.0, 82.0, 85.0, 88.0, 90.0]  # median 85.0
    times = [
        "2026-05-27 12:00:00",
        "2026-05-27 02:00:00",
        "2026-05-27 15:00:00",
        "2026-05-27 23:30:00",
        "2026-05-27 08:00:00",
    ]
    for img_id, (qv, ts) in enumerate(zip(qualities, times), start=1):
        conn.execute(
            "INSERT INTO images VALUES (?, 'general', 400, 300, ?)", (img_id, ts)
        )
        conn.execute(
            "INSERT INTO conversions VALUES (?, 'webp', 'magick', ?, 1)", (img_id, qv)
        )
    conn.commit()
    return conn


def test_weights_file_has_no_time_of_day_keys(tmp_path):
    weights_path = tmp_path / "w.json"
    generate_heuristic_table(
        conn=_fixture_conn(),
        table_path=tmp_path / "t.json",
        weights_path=weights_path,
    )

    payload = json.loads(weights_path.read_text())
    assert "lookup_by_time" not in payload
    assert "time_buckets" not in payload


def test_get_time_bucket_is_removed():
    assert not hasattr(heuristic_mod, "get_time_bucket")


def test_table_cells_unchanged_by_removal(tmp_path):
    table_path = tmp_path / "t.json"
    generate_heuristic_table(
        conn=_fixture_conn(),
        table_path=table_path,
        weights_path=tmp_path / "w.json",
    )

    from app.core.heuristic_interpolator import HeuristicInterpolator

    # All samples share one MP -> flat curve at their mean (85.0); removing the
    # time-of-day code must not change the quality the engine consumes.
    interp = HeuristicInterpolator(table_path)
    assert interp.get_interpolated_quality("general", "webp", "magick", 400, 300) == 85.0
