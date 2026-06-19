import pytest
import sqlite3
import json
import os
from app.core import config
from app.core.db.schema import init_db
from app.core.db.repositories.batch import BatchRepository

def test_calibration_data_persistence_and_export(tmp_path, monkeypatch):
    """
    Verify that calibration results are stored and can be exported to JSON.

    Calibration persistence is gated off by default (bead z0a); this test
    exercises the kept-but-inert feature with the flag explicitly enabled.
    """
    monkeypatch.setattr(config, "CALIBRATION_ENABLED", True)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    
    repo = BatchRepository()
    
    # 1. Create a run
    run_id = repo.create_run(conn, "src", "dst", "webp", "ffmpeg", "manual")
    
    # 2. Save calibration result
    input_path = "test.jpg"
    target_ssim = 0.98
    quality_found = 85.0
    iterations = 3
    data = [
        {"quality": 50, "ssim": 0.90},
        {"quality": 75, "ssim": 0.95},
        {"quality": 85, "ssim": 0.98}
    ]
    
    repo.save_calibration_result(
        conn, run_id, input_path, target_ssim, quality_found, iterations, data
    )
    
    # 3. Export to JSON
    export_path = tmp_path / "calibration.json"
    exported = repo.export_calibration_data(conn, run_id)
    with open(export_path, "w") as f:
        json.dump(exported, f)
    
    assert export_path.exists()
    
    # 4. Verify JSON content
    with open(export_path, "r") as f:
        exported = json.load(f)
        
    assert len(exported) == 1
    assert exported[0]["input_path"] == input_path
    assert exported[0]["quality_found"] == quality_found
    assert len(exported[0]["attempts"]) == 3
    assert exported[0]["attempts"][0]["quality"] == 50
