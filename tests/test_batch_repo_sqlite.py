import sqlite3
import pytest
from app.core.db.repositories.batch import BatchRepository
from app.core.db.schema import init_db

def test_batch_repository_sqlite_full_cycle():
    """
    Verifies that BatchRepository works flawlessly with SQLite 
    for a full lifecycle (create, update, get, summary).
    """
    repo = BatchRepository()
    # Create an in-memory SQLite database
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    
    # Create the tables using global schema
    init_db(conn)
    
    # 1. Create Run
    run_id = repo.create_run(
        conn,
        source_dir="/in",
        target_dir="/out",
        target_format="avif",
        tool="ffmpeg",
        trigger_type="manual"
    )
    assert run_id is not None
    assert isinstance(run_id, int)
    
    # 2. Get Run
    run = repo.get_run(conn, run_id)
    assert run["id"] == run_id
    assert run["status"] == "running"
    assert run["tool"] == "ffmpeg"
    
    # 3. Update Status
    repo.update_status(conn, run_id, "completed", total_images=10)
    run = repo.get_run(conn, run_id)
    assert run["status"] == "completed"
    assert run["total_images"] == 10
    assert run["completed_at"] is not None
    
    # 4. Save Summary
    repo.save_summary(
        conn,
        batch_id=run_id,
        duration_ms=1500.5,
        cpu_avg_pct=15.0,
        cpu_peak_pct=45.0,
        ram_peak_mb=128.0,
        yield_mb_sec=10.5,
        savings_pct=25.0,
        success_count=10,
        failure_count=0
    )
    
    summary = repo.get_summary(conn, run_id)
    assert summary["batch_id"] == run_id
    assert summary["duration_ms"] == 1500.5
    assert summary["success_count"] == 10
    
    # 5. Get All Runs — columns are explicitly aliased so the GUI history
    # panel can index by run_id/duration_ms without colliding with r.id vs
    # s.batch_id.
    all_runs = repo.get_all_runs(conn)
    assert len(all_runs) >= 1
    assert all_runs[0]["run_id"] == run_id
    assert "duration_ms" in all_runs[0]
    assert all_runs[0]["duration_ms"] == 1500.5

    # 6. Save Errors
    repo.save_errors(conn, batch_id=run_id, errors=[
        {"path": "a.jpg", "error": "boom"},
        {"path": "b.jpg", "error": "boom"},
    ])
    cur = conn.cursor()
    cur.execute("SELECT input_path, error FROM batch_errors WHERE batch_id=?", (run_id,))
    rows = cur.fetchall()
    assert {(r["input_path"], r["error"]) for r in rows} == {("a.jpg", "boom"), ("b.jpg", "boom")}
