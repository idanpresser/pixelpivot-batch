import sqlite3
import pytest
from unittest.mock import MagicMock, patch
from app.core.db.schema import init_db

def test_integrity_check_pass():
    conn = MagicMock(spec=sqlite3.Connection)
    cur = conn.cursor.return_value
    # PRAGMA integrity_check -> ("ok",)
    cur.fetchone.return_value = ("ok",)
    
    init_db(conn)
    
    assert conn.cursor.called

def test_integrity_check_fail_raises_runtime_error():
    conn = MagicMock(spec=sqlite3.Connection)
    cur = conn.cursor.return_value
    # PRAGMA integrity_check -> ("Main table corrupt",)
    cur.fetchone.return_value = ("Main table corrupt",)
    
    with pytest.raises(RuntimeError) as exc:
        init_db(conn)
    
    assert "Database integrity check failed" in str(exc.value)

def test_wal_mode_enforced():
    conn = MagicMock(spec=sqlite3.Connection)
    cur = conn.cursor.return_value
    cur.fetchone.return_value = ("ok",)
    
    init_db(conn)
    
    # Check if WAL pragma was called
    wal_called = False
    for call in cur.execute.call_args_list:
        if "journal_mode=WAL" in call[0][0]:
            wal_called = True
            break
    assert wal_called
