# tests/core/test_orchestrator.py
import os
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from app.batch_api.models import BatchRequest
from app.batch_api.orchestrator import BatchOrchestrator

def test_empty_scan_retry_succeeds(tmp_path, monkeypatch):
    """Test that empty scans are retried and succeed if files appear later."""
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    
    # Create a test file
    test_file = source_dir / "test.jpg"
    test_file.write_bytes(b"image data")
    
    # Initialize DB
    db_path = tmp_path / "test_retry.db"
    import app.core.db.connection as connection
    monkeypatch.setattr(connection, "SQLITE_DB_PATH", db_path)
    monkeypatch.setenv("PIXELPIVOT_DB_PATH", str(db_path))
    
    from app.core.db.schema import init_db
    with connection.get_connection() as _c:
        init_db(_c)
        
    orchestrator = BatchOrchestrator()
    
    # Stub probing so it doesn't call external binaries
    monkeypatch.setattr(orchestrator, "_probe_all_dimensions", lambda paths: {paths[0]: (100, 100)})
    monkeypatch.setattr(orchestrator, "_probe_quality", lambda *args, **kwargs: 80)
    
    # Mock converter
    class DummyConverter:
        is_broken = False
        def get_name(self): return "magick"
        def convert_batch(self, *args, **kwargs):
            out_file = target_dir / "test_magick.webp"
            out_file.write_bytes(b"dummy webp")
            return {"success_count": 1, "failure_count": 0, "errors": [], "telemetry": {}, "bytes_written": 10}
            
    orchestrator.converters["magick"] = DummyConverter()
    
    # Count how many times iterdir is called
    call_count = 0
    original_iterdir = Path.iterdir
    
    def mock_iterdir(self):
        nonlocal call_count
        if self == source_dir:
            call_count += 1
            if call_count == 1:
                return iter([]) # return empty on first scan
        return original_iterdir(self)
        
    monkeypatch.setattr(Path, "iterdir", mock_iterdir)
    
    # We want sleep to be short so the test runs fast
    monkeypatch.setattr(time, "sleep", lambda x: None)
    
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
        
    request = BatchRequest(
        source_dir=str(source_dir),
        target_dir=str(target_dir),
        target_format=["webp"],
        tool=["magick"],
        category=["general"]
    )
    
    orchestrator.execute_batch(run_id, request)
    
    # Verify the scan was retried (call_count should be 2)
    assert call_count == 2
    
    # Assert final status in DB is "completed"
    with connection.get_connection() as conn:
        run = orchestrator.repo.get_run(conn, run_id)
        assert run["status"] == "completed"
        assert run["total_images"] == 1


def test_empty_scan_fails_loud(tmp_path, monkeypatch):
    """Test that empty scans fail loud with a descriptive error when remaining empty."""
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    
    # Initialize DB
    db_path = tmp_path / "test_empty.db"
    import app.core.db.connection as connection
    monkeypatch.setattr(connection, "SQLITE_DB_PATH", db_path)
    monkeypatch.setenv("PIXELPIVOT_DB_PATH", str(db_path))
    
    from app.core.db.schema import init_db
    with connection.get_connection() as _c:
        init_db(_c)
        
    orchestrator = BatchOrchestrator()
    
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
        
    request = BatchRequest(
        source_dir=str(source_dir),
        target_dir=str(target_dir),
        target_format=["webp"],
        tool=["magick"],
        category=["general"]
    )
    
    # Make sleep short
    monkeypatch.setattr(time, "sleep", lambda x: None)
    
    orchestrator.execute_batch(run_id, request)
    
    # Assert final status in DB is "failed"
    with connection.get_connection() as conn:
        run = orchestrator.repo.get_run(conn, run_id)
        assert run["status"] == "failed"
        
        errors = orchestrator.repo.get_errors(conn, run_id)
        assert len(errors) == 1
        assert "No images found in" in errors[0]["error"]
        assert "after 3 scan attempts" in errors[0]["error"]
        assert errors[0]["input_path"] is None


def test_no_pre_loop_stat_storm_and_savings_math(tmp_path, monkeypatch):
    """Test that pre-loop stat calls do not occur and leftover files are excluded from savings."""
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    
    # Create two input files
    img1 = source_dir / "image1.jpg"
    img1.write_bytes(b"1234567890") # 10 bytes
    img2 = source_dir / "image2.jpg"
    img2.write_bytes(b"123456789012345") # 15 bytes
    
    # Write pre-existing leftover output file for image2
    leftover = target_dir / "image2_magick.webp"
    leftover.write_bytes(b"pre-existing leftover webp file content") # 39 bytes
    
    # Set leftover file's mtime to the past (e.g. 100 seconds ago)
    past_time = time.time() - 100
    os.utime(leftover, (past_time, past_time))
    
    # Initialize DB
    db_path = tmp_path / "test_stat.db"
    import app.core.db.connection as connection
    monkeypatch.setattr(connection, "SQLITE_DB_PATH", db_path)
    monkeypatch.setenv("PIXELPIVOT_DB_PATH", str(db_path))
    
    from app.core.db.schema import init_db
    with connection.get_connection() as _c:
        init_db(_c)
        
    orchestrator = BatchOrchestrator()
    
    # Mock probing
    monkeypatch.setattr(orchestrator, "_probe_all_dimensions", lambda paths: {p: (100, 100) for p in paths})
    monkeypatch.setattr(orchestrator, "_probe_quality", lambda *args, **kwargs: 80)
    
    # Instrument stat calls and converter invocation order
    converter_called = False
    stat_calls_before_converter = []
    
    original_stat = Path.stat
    def mock_stat(self):
        # Track stat calls on the target files
        if "target" in str(self) and self.suffix == ".webp":
            if not converter_called:
                stat_calls_before_converter.append(str(self))
        return original_stat(self)
        
    monkeypatch.setattr(Path, "stat", mock_stat)
    
    # Mock converter
    class MockConverter:
        is_broken = False
        def get_name(self): return "magick"
        def convert_batch(self, input_paths, target_dir, fmt, qualities, *args, **kwargs):
            nonlocal converter_called
            converter_called = True
            
            # Convert only image1 by creating its output file
            out1 = Path(target_dir) / "image1_magick.webp"
            out1.write_bytes(b"new output webp file content") # 28 bytes
            
            return {"success_count": 1, "failure_count": 0, "errors": [], "telemetry": {}, "bytes_written": 28}
            
    orchestrator.converters["magick"] = MockConverter()
    
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
        
    request = BatchRequest(
        source_dir=str(source_dir),
        target_dir=str(target_dir),
        target_format=["webp"],
        tool=["magick"],
        category=["general"]
    )
    
    orchestrator.execute_batch(run_id, request)
    
    # Verify no stat calls occurred on the output files before converter ran
    assert len(stat_calls_before_converter) == 0
    
    # Verify the savings percentage math
    # input_bytes = 10 (image1) + 15 (image2) = 25 bytes
    # output_bytes should only include image1_magick.webp (28 bytes) and exclude leftover (39 bytes)
    # output_bytes = 28
    # savings_pct = (1.0 - 28 / 25) * 100.0 = -12.0%
    with connection.get_connection() as conn:
        summary = orchestrator.repo.get_summary(conn, run_id)
        assert summary is not None
        assert summary["success_count"] == 1
        assert summary["failure_count"] == 0
        assert round(summary["savings_pct"], 1) == -12.0


def test_execute_batch_srp_decomposed_units(tmp_path, monkeypatch):
    """Test the decomposed helper methods under execute_batch (SRP validation)."""
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    target_dir = tmp_path / "target"
    target_dir.mkdir()

    img1 = source_dir / "image1.jpg"
    img1.write_bytes(b"1234567890")

    db_path = tmp_path / "test_srp.db"
    import app.core.db.connection as connection
    monkeypatch.setattr(connection, "SQLITE_DB_PATH", db_path)
    monkeypatch.setenv("PIXELPIVOT_DB_PATH", str(db_path))

    from app.core.db.schema import init_db
    with connection.get_connection() as _c:
        init_db(_c)

    orchestrator = BatchOrchestrator()
    # Stub probing so it doesn't call external binaries or crash on dummy files
    monkeypatch.setattr(orchestrator, "_probe_all_dimensions", lambda paths: {paths[0]: (100, 100)})

    request = BatchRequest(
        source_dir=str(source_dir),
        target_dir=str(target_dir),
        target_format=["webp"],
        tool=["magick"],
        category=["general"]
    )

    # 1. Test _scan_and_preflight
    paths, sizes = orchestrator._scan_and_preflight(request)
    assert len(paths) == 1
    assert str(img1) in paths
    assert sizes[str(img1)] == 10

    # 2. Test _reset_converters
    for c in orchestrator.converters.values():
        c.is_broken = True
    orchestrator._reset_converters(run_id=42)
    from app.core.converters.base import BaseConverter
    for c in orchestrator.converters.values():
        if isinstance(c, BaseConverter):
            assert c.is_broken is False

    # 3. Test _prepare_image_plan
    from app.batch_api.orchestrator import MatrixCell
    plan = [MatrixCell(category="general", tool="magick", target_format="webp")]
    active, dim_cache, fail_count, rejects = orchestrator._prepare_image_plan(paths, plan, str(target_dir))
    assert len(active) == 1
    assert fail_count == 0
    assert len(rejects) == 0
