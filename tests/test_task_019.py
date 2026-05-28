"""Task 019 - CLI generator emits a real version (converge on one generator).

task_010 made the canonical generator stamp a `version` into the table, but the
standalone CLI generator did not, so a table produced via the CLI read back as
interpolator.version == "unknown", silently losing run provenance. These tests
pin that the CLI entrypoint delegates to the canonical generator, so the emitted
table round-trips a real version and the cells match the single source of truth.
"""

import json
import sqlite3

from app.core.config import HEURISTIC_TABLE_VERSION
from app.core.heuristic_interpolator import HeuristicInterpolator


def _build_db(path):
    # HEURISTIC_MIN_SAMPLES identical samples so the cell survives the min-sample
    # gate (task_017); median of identical values is that value (82.0).
    from app.core.config import HEURISTIC_MIN_SAMPLES

    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE images (id INTEGER PRIMARY KEY, category TEXT, "
        "width INTEGER, height INTEGER, arrival_time TEXT)"
    )
    conn.execute(
        "CREATE TABLE conversions (image_id INTEGER, format TEXT, tool TEXT, "
        "quality REAL, success BOOLEAN)"
    )
    for img_id in range(1, HEURISTIC_MIN_SAMPLES + 1):
        conn.execute(
            "INSERT INTO images VALUES (?, 'general', 400, 300, NULL)", (img_id,)
        )
        conn.execute(
            "INSERT INTO conversions VALUES (?, 'webp', 'magick', 82.0, 1)", (img_id,)
        )
    conn.commit()
    conn.close()


def test_cli_table_round_trips_real_version(tmp_path):
    from tools.generate_heuristic_data import generate_cli

    db_file = tmp_path / "src.db"
    out_json = tmp_path / "table.json"
    _build_db(db_file)

    generate_cli(str(db_file), str(out_json), weights_path=str(tmp_path / "w.json"))

    interp = HeuristicInterpolator(out_json)
    assert interp.version == HEURISTIC_TABLE_VERSION
    assert interp.version != "unknown"


def test_cli_cells_match_canonical_generator(tmp_path):
    from tools.generate_heuristic_data import generate_cli

    db_file = tmp_path / "src.db"
    out_json = tmp_path / "table.json"
    _build_db(db_file)

    generate_cli(str(db_file), str(out_json), weights_path=str(tmp_path / "w.json"))

    # Single source of truth: the canonical curve flows through the CLI. All
    # samples share one MP -> flat curve at 82.0.
    interp = HeuristicInterpolator(out_json)
    assert interp.get_interpolated_quality("general", "webp", "magick", 400, 300) == 82.0
