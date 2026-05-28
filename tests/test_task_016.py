"""Task 016 - fit a direct quality=f(MP) curve instead of 4-bucket means.

The pipeline used to collapse rich per-image data into 4 bucket means per
(category, format, tool), then linearly interpolate between fixed MP centers.
This task replaces that with a continuous log-linear fit
(q = a + b * log10(megapixels)) per (category, format, tool), evaluated directly
at the image's MP and clamped to the observed MP range and the encoder's valid
range. These tests pin the new schema, the curve accuracy at intermediate MPs
(where the old bucket interpolation was provably off), both clamps, and the
min-sample gate (folded in from task_017).
"""

import json
import math
import sqlite3

import pytest

from app.core.heuristic import generate_heuristic_table
from app.core.heuristic_interpolator import HeuristicInterpolator


# Known law the fixture follows so the least-squares fit is exactly recoverable.
def _law(mp: float) -> float:
    return 95.0 - 8.0 * math.log10(mp)


# (width, height) chosen so width*height/1e6 is an exact megapixel value.
_SAMPLE_DIMS = [
    (1000, 500),    # 0.5 MP
    (1000, 1000),   # 1.0 MP
    (2000, 1000),   # 2.0 MP
    (2000, 2000),   # 4.0 MP
    (4000, 2000),   # 8.0 MP
    (4000, 3000),   # 12.0 MP
]


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


def _seed_law(conn, category, fmt, tool, dims=_SAMPLE_DIMS, start_id=1):
    img_id = start_id
    for (w, h) in dims:
        mp = (w * h) / 1_000_000.0
        conn.execute(
            "INSERT INTO images VALUES (?, ?, ?, ?, NULL)", (img_id, category, w, h)
        )
        conn.execute(
            "INSERT INTO conversions VALUES (?, ?, ?, ?, 1)",
            (img_id, fmt, tool, _law(mp)),
        )
        img_id += 1
    return img_id


def test_config_table_version_bumped():
    from app.core.config import HEURISTIC_TABLE_VERSION

    # Schema change (buckets -> curves) must bump the major version.
    assert HEURISTIC_TABLE_VERSION == "2.0.0"


def test_quality_range_for_is_tool_format_native():
    from app.core.config import quality_range_for

    # ffmpeg avif is a libaom CRF (0..63); everything else is 0..100.
    assert quality_range_for("ffmpeg", "avif") == (0.0, 63.0)
    assert quality_range_for("magick", "webp") == (0.0, 100.0)
    assert quality_range_for("vips", "jxl") == (0.0, 100.0)


def test_generator_emits_curve_schema(tmp_path):
    conn = _conn()
    _seed_law(conn, "photo", "webp", "magick")
    conn.commit()

    table_path = tmp_path / "t.json"
    generate_heuristic_table(
        conn=conn, table_path=table_path, weights_path=tmp_path / "w.json"
    )

    data = json.loads(table_path.read_text())
    cell = data["photo"]["webp"]["magick"]
    for key in ("a", "b", "n", "mp_min", "mp_max"):
        assert key in cell
    assert cell["n"] == len(_SAMPLE_DIMS)
    assert cell["mp_min"] == pytest.approx(0.5)
    assert cell["mp_max"] == pytest.approx(12.0)


def test_curve_recovers_known_law_at_intermediate_mp(tmp_path):
    conn = _conn()
    _seed_law(conn, "photo", "webp", "magick")
    conn.commit()

    table_path = tmp_path / "t.json"
    generate_heuristic_table(
        conn=conn, table_path=table_path, weights_path=tmp_path / "w.json"
    )
    interp = HeuristicInterpolator(table_path)

    # 1.0 MP sample point, and 3.5 MP intermediate (between the old large bucket
    # samples) where 4-bucket interpolation was provably biased.
    q_1mp = interp.get_interpolated_quality("photo", "webp", "magick", 1000, 1000)
    q_3p5 = interp.get_interpolated_quality("photo", "webp", "magick", 2000, 1750)
    assert q_1mp == pytest.approx(_law(1.0), abs=0.1)
    assert q_3p5 == pytest.approx(_law(3.5), abs=0.1)


def test_result_clamped_to_observed_mp_range(tmp_path):
    conn = _conn()
    _seed_law(conn, "photo", "webp", "magick")
    conn.commit()

    table_path = tmp_path / "t.json"
    generate_heuristic_table(
        conn=conn, table_path=table_path, weights_path=tmp_path / "w.json"
    )
    interp = HeuristicInterpolator(table_path)

    # 50 MP is far above mp_max (12.0): evaluate at the boundary, not extrapolate.
    q_huge = interp.get_interpolated_quality("photo", "webp", "magick", 10000, 5000)
    assert q_huge == pytest.approx(_law(12.0), abs=0.1)
    assert 0.0 <= q_huge <= 100.0


def test_result_clamped_to_encoder_range(tmp_path):
    # Hand-built curve whose value (flat 100) exceeds the ffmpeg avif CRF ceiling.
    table_path = tmp_path / "t.json"
    table = {
        "version": "2.0.0",
        "c": {"avif": {"ffmpeg": {"a": 100.0, "b": 0.0, "n": 10, "mp_min": 0.1, "mp_max": 100.0}}},
    }
    table_path.write_text(json.dumps(table))
    interp = HeuristicInterpolator(table_path)

    q = interp.get_interpolated_quality("c", "avif", "ffmpeg", 1000, 1000)
    assert q == 63.0  # clamped to libaom CRF ceiling


def test_under_sampled_cell_falls_back(tmp_path):
    from app.core.config import HEURISTIC_MIN_SAMPLES, default_quality_for

    conn = _conn()
    # One fewer sample than the gate -> no curve fitted -> interpolator falls back.
    _seed_law(conn, "thin", "webp", "magick", dims=_SAMPLE_DIMS[: HEURISTIC_MIN_SAMPLES - 1])
    conn.commit()

    table_path = tmp_path / "t.json"
    generate_heuristic_table(
        conn=conn, table_path=table_path, weights_path=tmp_path / "w.json"
    )
    interp = HeuristicInterpolator(table_path)

    q = interp.get_interpolated_quality("thin", "webp", "magick", 1000, 1000)
    assert q == default_quality_for("magick", "webp")
