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

def test_transaction_atomicity_nested():
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
        
        # Patch SQLITE_DB_PATH to use our temp DB
        with patch("app.core.db.connection.SQLITE_DB_PATH", db_path):
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
