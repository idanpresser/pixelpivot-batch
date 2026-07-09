"""Tests for DLQ matrix starvation issues."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="windows paths & shell assumed in parts")

from app.batch_api.models import BatchRequest
from app.batch_api.orchestrator import BatchOrchestrator
import app.core.db.connection as connection
from app.core.db.schema import init_db


def test_dlq_starvation_matrix(tmp_path, monkeypatch):
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    
    test_file = source_dir / "test.jpg"
    test_file.write_bytes(b"image data")
    
    db_path = tmp_path / "test_dlq.db"
    monkeypatch.setattr(connection, "SQLITE_DB_PATH", db_path)
    monkeypatch.setenv("PIXELPIVOT_DB_PATH", str(db_path))
    
    with connection.get_connection() as _c:
        init_db(_c)
        
    orchestrator = BatchOrchestrator()
    
    # Stub probing
    monkeypatch.setattr(orchestrator, "_probe_all_dimensions", lambda paths: {paths[0]: (100, 100)})
    monkeypatch.setattr(orchestrator, "_probe_quality", lambda *args, **kwargs: 80)
    
    # We define converter A (magick) which fails
    class ConverterA:
        is_broken = False
        def get_name(self): return "magick"
        def convert_batch(self, paths, target_dir, fmt, qualities, **kwargs):
            return {
                "success_count": 0,
                "failure_count": 1,
                "errors": [{"path": paths[0], "error": "magick failed", "dlq": True}],
                "telemetry": {},
                "bytes_written": 0
            }
            
    # We define converter B (ffmpeg) which succeeds
    class ConverterB:
        is_broken = False
        def get_name(self): return "ffmpeg"
        def convert_batch(self, paths, target_dir, fmt, qualities, **kwargs):
            out_file = Path(target_dir) / f"{Path(paths[0]).stem}_ffmpeg.webp"
            out_file.write_bytes(b"converted webp")
            return {
                "success_count": 1,
                "failure_count": 0,
                "errors": [],
                "telemetry": {},
                "bytes_written": 14
            }
            
    orchestrator.converters["magick"] = ConverterA()
    orchestrator.converters["ffmpeg"] = ConverterB()
    

    # Create run record
    with connection.get_connection() as conn:
        run_id = orchestrator.repo.create_run(
            conn,
            source_dir=str(source_dir),
            target_dir=str(target_dir),
            target_format="webp",
            tool="magick",
            trigger_type="test"
        )
    
    # We make a request with both magick and ffmpeg
    req = BatchRequest(
        source_dir=str(source_dir),
        target_dir=str(target_dir),
        category=["general"],
        tool=["magick", "ffmpeg"],
        target_format=["webp"]
    )
    
    # Run the batch synchronously
    orchestrator.execute_batch(run_id, req)
        
    # Check the results from the database
    with connection.get_connection() as conn:
        summary = orchestrator.repo.get_summary(conn, run_id)
        assert summary["success_count"] == 1
        assert summary["failure_count"] == 1
    
    # DLQ should NOT have the file
    dlq_dir = target_dir / "corrupt_or_failed"
    assert not (dlq_dir / "test.jpg").exists()
    
    # Original file should still be in source_dir
    assert test_file.exists()
    
    # Target should have the converted file
    assert (target_dir / "test_ffmpeg.webp").exists()
    
    # Get all errors from DB/orchestrator run summary or check db
    with connection.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT input_path, error, is_dlq FROM batch_errors WHERE batch_id = ?", (run_id,))
        rows = cursor.fetchall()
        assert len(rows) == 1
        # The path should still be the original source path and is_dlq should be False!
        assert rows[0][0] == str(test_file)
        assert rows[0][2] == 0  # SQLite boolean is 0
