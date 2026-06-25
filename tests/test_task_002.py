import sqlite3
import threading
import time
import pytest
import os
from pathlib import Path

from app.batch_api.orchestrator import BatchOrchestrator
from app.batch_api.models import BatchRequest, Tool
from app.core.db.connection import get_connection

def test_sqlite_busy_retry_with_real_lock(tmp_path, monkeypatch):
    """
    Regression test for Task 002: Orchestrator retries save_summary on SQLITE_BUSY.
    """
    # Force clean thread-local connection to prevent leakage from prior tests
    import app.core.db.connection as connection
    if hasattr(connection, "_local"):
        connection._local.conn = None
        connection._local.depth = 0

    db_path = tmp_path / "test_busy.db"
    monkeypatch.setattr(connection, "SQLITE_DB_PATH", db_path)
    monkeypatch.setenv("PIXELPIVOT_DB_PATH", str(db_path))

    # get_connection() caches a thread-local connection; drop any left open by a
    # prior test on this worker thread so the whole test binds to db_path.
    if getattr(connection._local, "conn", None) is not None:
        try:
            connection._local.conn.close()
        except Exception:
            pass
    connection._local.conn = None
    connection._local.depth = 0

    # Initialize schema on an explicit connection bound to db_path. The no-arg
    # init_db() proved order-sensitive in the full suite (schema landed where a
    # raw reader couldn't see it -> "no such table"); passing the connection
    # makes the target deterministic. Checkpoint so the WAL is folded into the
    # main file before the orchestrator's raw connection reads it.
    from app.core.db.schema import init_db
    with connection.get_connection() as _c:
        print(f"DEBUG: _c database list at init_db: {[tuple(r) for r in _c.execute('PRAGMA database_list').fetchall()]}")
        init_db(_c)
        _c.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    # Create a batch request
    request = BatchRequest(
        source_dir=str(tmp_path),
        target_dir=str(tmp_path),
        category=["highRes"],
        tool=[Tool.magick],
        target_format=["webp"]
    )

    # Put a fake image so orchestrator has something to process
    (tmp_path / "test.jpg").write_bytes(b"fake image data")

    monkeypatch.setattr("app.core.utils.probe_image_dimensions", lambda path: (100, 100))

    orchestrator = BatchOrchestrator()
    # Mock converter to succeed instantly
    class DummyConverter:
        is_broken = False
        def get_name(self): return "magick"
        def convert_batch(self, *args, **kwargs):
            return {"success_count": 1, "failure_count": 0, "errors": [], "telemetry": {}}
    orchestrator.converters["magick"] = DummyConverter()
    
    # We must also mock config to speed up retries in tests, else it might take long.
    import app.core.config as config
    monkeypatch.setattr(config, "SQLITE_BUSY_ATTEMPTS", 8, raising=False)
    monkeypatch.setattr(config, "SQLITE_BUSY_BASE_DELAY_S", 0.1, raising=False)
    
    # Create a run record first
    with get_connection() as conn:
        # Note: the new matrix feature passes lists for format and tool, so we just use string representation 
        run_id = orchestrator.repo.create_run(conn, str(tmp_path), str(tmp_path), "['webp']", "['magick']", "api")

    def locker():
        try:
            # Hold an exclusive lock for 0.5s so the orchestrator's save_summary
            # hits SQLITE_BUSY and must exercise the busy-retry path. The lock
            # alone forces contention; we deliberately do NOT write status here
            # (a trailing UPDATE would race and clobber the orchestrator's final
            # 'completed', which is exactly what this test asserts survives).
            with sqlite3.connect(str(db_path), timeout=0.1) as conn:
                conn.execute("BEGIN EXCLUSIVE TRANSACTION")
                time.sleep(0.5)
                # Block exit commits and releases the lock.
        except Exception as e:
            print(f"Locker thread error: {e}")

    # Start the locking thread right before we execute batch
    t = threading.Thread(target=locker)
    t.start()
    
    # Give the thread a tiny head start to acquire the lock
    time.sleep(0.1)

    # Now execute_batch. It will try to save_summary and should encounter the lock.
    # The default connection timeout is 5s, but we'll drop it so the retry kicks in immediately.
    # Wait, get_connection() defaults to timeout=5.0!
    # If the default timeout is 5.0, it will wait 5 seconds before raising OperationalError.
    # The lock is held for only 0.5s, so it won't even raise!
    # We must patch get_connection to have a very short timeout so it raises OperationalError quickly.
    
    original_get_connection = get_connection
    def fast_fail_get_connection():
        # override timeout to 0.1s so it raises SQLITE_BUSY quickly
        conn = sqlite3.connect(str(db_path), timeout=0.1, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    monkeypatch.setattr("app.batch_api.orchestrator.get_connection", fast_fail_get_connection)
    
    # Debug update_status
    original_update_status = orchestrator.repo.update_status
    def mock_update_status(conn, r_id, status, *args, **kwargs):
        print(f"DEBUG: update_status called for r_id={r_id} with status={status}")
        try:
            res = original_update_status(conn, r_id, status, *args, **kwargs)
            print(f"DEBUG: update_status succeeded for r_id={r_id} status={status}")
            return res
        except Exception as e:
            print(f"DEBUG: update_status FAILED for r_id={r_id} status={status}: {e}")
            raise
    monkeypatch.setattr(orchestrator.repo, "update_status", mock_update_status)

    # Execute batch
    print("DEBUG: Starting execute_batch")
    try:
        orchestrator.execute_batch(run_id, request)
        print("DEBUG: execute_batch finished successfully")
    except Exception as e:
        print(f"DEBUG: execute_batch raised exception: {e}")
        raise

    t.join()

    # Verify that the run completed and summary is there
    with get_connection() as conn:
        row = conn.execute("SELECT status FROM batch_runs WHERE id=?", (run_id,)).fetchone()
        assert row is not None
        assert row[0] == "completed"
        
        sum_row = conn.execute("SELECT success_count FROM batch_summary WHERE batch_id=?", (run_id,)).fetchone()
        assert sum_row is not None
        assert sum_row[0] == 1
