import pytest
import os
from pathlib import Path
from unittest.mock import patch, MagicMock
from app.batch_api.orchestrator import BatchOrchestrator
from app.batch_api.models import BatchRequest, Tool
from app.core.db.connection import get_connection

def test_mkdir_error_not_swallowed(tmp_path, monkeypatch):
    """
    Task 006: mkdir OSError should propagate and fail the batch, not be swallowed.
    """
    db_path = tmp_path / "test.db"
    import app.core.db.connection as connection
    monkeypatch.setattr(connection, "SQLITE_DB_PATH", db_path)

    from app.core.db.schema import init_db
    init_db()

    request = BatchRequest(
        source_dir=str(tmp_path),
        target_dir=str(tmp_path / "invalid"),
        category=["highRes"],
        tool=[Tool.magick],
        target_format=["webp"]
    )
    (tmp_path / "test1.jpg").write_bytes(b"fake")

    orchestrator = BatchOrchestrator()
    
    with get_connection() as conn:
        run_id = orchestrator.repo.create_run(conn, str(tmp_path), str(tmp_path / "invalid"), "['webp']", "['magick']", "api")

    # Mock Path.mkdir to raise OSError ONLY for the target dir
    original_mkdir = Path.mkdir
    def selective_mkdir(self, *args, **kwargs):
        if "invalid" in str(self):
            raise OSError("Permission denied")
        return original_mkdir(self, *args, **kwargs)

    with patch("pathlib.Path.mkdir", autospec=True, side_effect=selective_mkdir):
        orchestrator.execute_batch(run_id, request)
        
        # Verify run was marked as failed
        with get_connection() as conn:
            row = conn.execute("SELECT status FROM batch_runs WHERE id=?", (run_id,)).fetchone()
            assert row is not None
            assert row[0] == "failed"

def test_mid_run_disk_check(tmp_path, monkeypatch):
    """
    Task 006: A mid-run low-disk should abort the remaining cells cleanly and save a partial summary.
    """
    db_path = tmp_path / "test.db"
    import app.core.db.connection as connection
    monkeypatch.setattr(connection, "SQLITE_DB_PATH", db_path)

    from app.core.db.schema import init_db
    init_db()

    # Create a 2-cell matrix (to trigger mid-run check)
    request = BatchRequest(
        source_dir=str(tmp_path),
        target_dir=str(tmp_path),
        category=["highRes"],
        tool=[Tool.magick, Tool.ffmpeg],
        target_format=["webp"]
    )
    (tmp_path / "test1.jpg").write_bytes(b"fake")

    orchestrator = BatchOrchestrator()
    class DummyConverter:
        is_broken = False
        def get_name(self): return "dummy"
        def convert_batch(self, *args, **kwargs):
            return {"success_count": 1, "failure_count": 0, "errors": [], "telemetry": {}}
            
    orchestrator.converters["magick"] = DummyConverter()
    orchestrator.converters["ffmpeg"] = DummyConverter()
    
    with get_connection() as conn:
        run_id = orchestrator.repo.create_run(conn, str(tmp_path), str(tmp_path), "['webp']", "['magick', 'ffmpeg']", "api")

    # Disk/RAM thresholds now live in the shared image_guards module (DRY); the
    # orchestrator delegates its preflight + mid-run disk check there.
    monkeypatch.setattr("app.batch_api.orchestrator.DISK_RECHECK_EVERY_CELLS", 1)
    monkeypatch.setattr("app.batch_api.image_guards.MIN_FREE_DISK_BYTES", 50*1024*1024)
    monkeypatch.setattr("app.batch_api.image_guards.MIN_AVAILABLE_RAM_BYTES", 50*1024*1024)

    call_count = 0
    def mock_disk_usage(*args):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return (100, 10, 100*1024*1024) # 100 MB free (preflight OK)
        else:
            return (100, 10, 10*1024*1024) # 10 MB free (mid-run FAIL)

    with patch("shutil.disk_usage", side_effect=mock_disk_usage), \
         patch("app.core.utils.probe_image_dimensions", return_value=(800, 600)):
        
        # It should process first cell and abort on the second
        orchestrator.execute_batch(run_id, request)
        
        with get_connection() as conn:
            # Succeeded but partially
            row = conn.execute("SELECT status FROM batch_runs WHERE id=?", (run_id,)).fetchone()
            assert row is not None
            assert row[0] == "completed" 
            
            sum_row = conn.execute("SELECT success_count FROM batch_summary WHERE batch_id=?", (run_id,)).fetchone()
            assert sum_row is not None
            assert sum_row[0] == 1 # Only 1 out of 2 conversions done
