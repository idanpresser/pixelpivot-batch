import pytest
import sqlite3
from unittest.mock import MagicMock, patch
from app.core.db.connection import get_connection

def test_sqlite_connection_reuse():
    """
    Verify that nested get_connection() calls reuse the same object.
    """
    with get_connection() as conn1:
        with get_connection() as conn2:
            assert conn1 is conn2

def test_transaction_atomicity_nested(monkeypatch):
    """
    Verify that nested rollback works correctly.
    """
    from app.core.db.schema import init_db
    # We use a real file-based DB for this test to avoid :memory: sharing issues
    import tempfile
    from pathlib import Path
    
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "test.db"
        conn = sqlite3.connect(str(db_path))
        init_db(conn)
        conn.close()
        
        # Patch PIXELPIVOT_DB_PATH to use our temp DB
        monkeypatch.setenv("PIXELPIVOT_DB_PATH", str(db_path))
        
        try:
            with get_connection() as conn_outer:
                conn_outer.execute("INSERT INTO batch_runs (source_dir, target_dir, target_format, tool, trigger_type, status) VALUES ('a','b','c','d','e','f')")
                
                with get_connection() as conn_inner:
                    conn_inner.execute("INSERT INTO batch_runs (source_dir, target_dir, target_format, tool, trigger_type, status) VALUES ('g','h','i','j','k','l')")
                    raise RuntimeError("Nested Boom")
        except RuntimeError:
            pass
        
        # Verify both were rolled back
        with get_connection() as conn_final:
            cur = conn_final.cursor()
            cur.execute("SELECT count(*) FROM batch_runs")
            assert cur.fetchone()[0] == 0


def test_transaction_atomicity_nested_caught(monkeypatch):
    """
    Verify that if a nested transaction fails but the exception is caught,
    the outer transaction's changes can still commit while the inner changes are rolled back.
    """
    from app.core.db.schema import init_db
    import tempfile
    from pathlib import Path
    
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "test.db"
        conn = sqlite3.connect(str(db_path))
        init_db(conn)
        conn.close()
        
        # Patch PIXELPIVOT_DB_PATH to use our temp DB
        monkeypatch.setenv("PIXELPIVOT_DB_PATH", str(db_path))
        
        with get_connection() as conn_outer:
            conn_outer.execute("INSERT INTO batch_runs (source_dir, target_dir, target_format, tool, trigger_type, status) VALUES ('outer','b','c','d','e','f')")
            
            try:
                with get_connection() as conn_inner:
                    conn_inner.execute("INSERT INTO batch_runs (source_dir, target_dir, target_format, tool, trigger_type, status) VALUES ('inner','h','i','j','k','l')")
                    raise RuntimeError("Nested Boom")
            except RuntimeError:
                pass
            
            # Outer transaction should continue and succeed
        
        # Verify only outer change committed, inner was rolled back
        with get_connection() as conn_final:
            cur = conn_final.cursor()
            cur.execute("SELECT source_dir FROM batch_runs")
            rows = cur.fetchall()
            assert len(rows) == 1
            assert rows[0][0] == "outer"

