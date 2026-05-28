"""Task 010 - emit a version key from the canonical heuristic generator.

HeuristicInterpolator reads a top-level `version` key and that value is stamped
onto every batch run. But the generator never wrote one, so regenerating the
table made `interpolator.version` fall back to "unknown" and the
batch_runs.heuristic_version column lost all meaning. These tests pin that a
freshly generated table round-trips a real version sourced from config.
"""

import json
import sqlite3
from pathlib import Path

import pytest

from app.core.heuristic_interpolator import HeuristicInterpolator


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
    conn.execute("INSERT INTO images VALUES (1, 'general', 400, 300, NULL)")
    conn.execute("INSERT INTO conversions VALUES (1, 'webp', 'magick', 82.0, 1)")
    conn.commit()
    return conn


def test_config_exposes_heuristic_table_version():
    from app.core.config import HEURISTIC_TABLE_VERSION

    assert isinstance(HEURISTIC_TABLE_VERSION, str)
    assert HEURISTIC_TABLE_VERSION


def test_generated_table_round_trips_version(tmp_path):
    from app.core.heuristic import generate_heuristic_table
    from app.core.config import HEURISTIC_TABLE_VERSION

    table_path = tmp_path / "t.json"
    generate_heuristic_table(
        conn=_fixture_conn(), table_path=table_path, weights_path=tmp_path / "w.json"
    )

    interp = HeuristicInterpolator(table_path)
    assert interp.version == HEURISTIC_TABLE_VERSION
    assert interp.version != "unknown"


def test_generated_table_version_can_be_overridden(tmp_path):
    from app.core.heuristic import generate_heuristic_table

    table_path = tmp_path / "t.json"
    generate_heuristic_table(
        conn=_fixture_conn(),
        table_path=table_path,
        weights_path=tmp_path / "w.json",
        version="9.9.9",
    )

    data = json.loads(table_path.read_text())
    assert data["version"] == "9.9.9"
