import sqlite3
import pytest
import os
import tempfile
from app.core.db.schema import init_db

def test_init_db_sqlite_memory():
    """Verifies that the schema can be initialized in-memory."""
    conn = sqlite3.connect(":memory:")
    init_db(conn)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='batch_runs'")
    assert cursor.fetchone() is not None

def test_init_db_sqlite_file():
    """Verifies that the schema can be initialized in a file with WAL mode."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = sqlite3.connect(path)
    try:
        init_db(conn)
        cursor = conn.cursor()
        cursor.execute("PRAGMA journal_mode")
        mode = cursor.fetchone()[0]
        assert mode == "wal"
    finally:
        conn.close()
        # WAL leaves -wal and -shm sidecars next to the main file
        for suffix in ("", "-wal", "-shm"):
            target = path + suffix
            if os.path.exists(target):
                try:
                    os.remove(target)
                except OSError:
                    pass
