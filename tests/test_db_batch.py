import pytest
from unittest.mock import MagicMock, patch
import sqlite3
from app.core.db.repositories.batch import BatchRepository

@pytest.fixture
def mock_conn():
    return MagicMock()

@pytest.fixture
def repo():
    return BatchRepository()

def test_create_run(repo, mock_conn):
    mock_cursor = mock_conn.cursor.return_value
    mock_cursor.fetchone.return_value = {"id": 1}
    
    run_id = repo.create_run(
        mock_conn,
        source_dir="/src",
        target_dir="/dst",
        target_format="webp",
        tool="magick",
        trigger_type="manual"
    )
    
    assert run_id == 1
    mock_cursor.execute.assert_called_once()
    args, _ = mock_cursor.execute.call_args
    assert "INSERT INTO batch_runs" in args[0]
    assert "/src" in args[1]
    assert "manual" in args[1]
    assert "running" in args[1] # Default status

def test_update_status_completed(repo, mock_conn):
    mock_cursor = mock_conn.cursor.return_value
    
    repo.update_status(mock_conn, run_id=1, status="completed", total_images=100)
    
    mock_cursor.execute.assert_called_once()
    args, _ = mock_cursor.execute.call_args
    query = args[0]
    params = args[1]
    
    assert "UPDATE batch_runs" in query
    assert "completed_at" in query
    assert "total_images" in query
    assert "completed" in params
    assert 100 in params
    assert 1 in params

def test_get_run(repo, mock_conn):
    mock_cursor = mock_conn.cursor.return_value
    mock_cursor.fetchone.return_value = {"id": 1, "status": "running"}
    
    # We need to implement get_run in the repository
    run = repo.get_run(mock_conn, 1)
    
    assert run["id"] == 1
    mock_cursor.execute.assert_called_once_with("SELECT * FROM batch_runs WHERE id = ?", (1,))

def test_save_summary(repo, mock_conn):
    mock_cursor = mock_conn.cursor.return_value
    
    repo.save_summary(
        mock_conn,
        batch_id=1,
        duration_ms=1000.5,
        cpu_avg_pct=25.0,
        cpu_peak_pct=50.0,
        ram_peak_mb=128.0,
        yield_mb_sec=10.5,
        savings_pct=30.0,
        success_count=10,
        failure_count=0
    )
    
    mock_cursor.execute.assert_called_once()
    args, _ = mock_cursor.execute.call_args
    assert "INSERT INTO batch_summary" in args[0]
    assert 1 in args[1]
    assert 1000.5 in args[1]
    assert 10 in args[1]
