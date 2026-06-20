import json
import sqlite3
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
from app.core.heuristic_interpolator import HeuristicInterpolator
from app.core.db.schema import init_db
from app.core.db.repositories.batch import BatchRepository

@pytest.fixture
def mock_table(tmp_path):
    table_path = tmp_path / "heuristic_table.json"
    data = {
        "version": "1.2.3",
        "general": {
            "small": {"webp": {"ffmpeg": 70}},
            "large": {"webp": {"ffmpeg": 90}}
        }
    }
    table_path.write_text(json.dumps(data))
    return table_path

def test_heuristic_table_version_recorded(mock_table, tmp_path):
    """
    Verify that create_run stores the current version of the heuristic table.
    """
    # Create an in-memory DB and init it
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    
    repo = BatchRepository()
    interpolator = HeuristicInterpolator(mock_table)
    
    run_id = repo.create_run(
        conn,
        source_dir="src",
        target_dir="dst",
        target_format="webp",
        tool="ffmpeg",
        trigger_type="manual",
        heuristic_version=interpolator.version
    )
    
    cur = conn.cursor()
    cur.execute("SELECT heuristic_version FROM batch_runs WHERE id = ?", (run_id,))
    version = cur.fetchone()[0]
    
    assert version == "1.2.3"

def test_heuristic_table_rollback(tmp_path):
    """
    Verify that overriding the version works and uses old values.
    """
    v1_path = tmp_path / "v1.json"
    v1_data = {
        "version": "1.0.0",
        "general": {"webp": {"ffmpeg": {"a": 50.0, "b": 0.0, "n": 10, "mp_min": 0.001, "mp_max": 50.0}}},
    }
    v1_path.write_text(json.dumps(v1_data))

    # A pinned older table still loads its own version and evaluates its curve.
    interpolator_v1 = HeuristicInterpolator(v1_path)
    assert interpolator_v1.get_interpolated_quality("general", "webp", "ffmpeg", 100, 100) == 50
    assert interpolator_v1.version == "1.0.0"

def test_batch_run_migration():
    """
    Verify that init_db correctly adds the missing column to an existing database.
    """
    conn = sqlite3.connect(":memory:")
    cur = conn.cursor()
    # Create OLD schema (no heuristic_version)
    cur.execute("""
    CREATE TABLE batch_runs (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        source_dir      TEXT    NOT NULL,
        target_dir      TEXT    NOT NULL,
        target_format   TEXT    NOT NULL,
        tool            TEXT    NOT NULL,
        trigger_type    TEXT    NOT NULL,
        status          TEXT    NOT NULL,
        total_images    INTEGER DEFAULT 0,
        created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        completed_at    TIMESTAMP
    )
    """)
    conn.commit()
    
    # Run init_db (migration)
    init_db(conn)
    
    # Check if column exists
    cur.execute("PRAGMA table_info('batch_runs')")
    columns = [row[1] for row in cur.fetchall()]
    assert "heuristic_version" in columns
