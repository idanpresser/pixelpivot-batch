# tests/db/test_schema_dialect.py
from app.core.db import connection as conn
from app.core.db.schema import init_db, EXPECTED_TABLES


def test_sqlite_schema_creates_all_tables(tmp_path, monkeypatch):
    monkeypatch.delenv("PIXELPIVOT_DB_URL", raising=False)
    monkeypatch.setenv("PIXELPIVOT_DB_PATH", str(tmp_path / "schema.db"))
    conn.reset_engine_cache()
    init_db()
    with conn.get_connection() as c:
        cur = c.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        names = {r["name"] for r in cur.fetchall()}
    assert EXPECTED_TABLES <= names


def test_pg_ddl_uses_serial_not_autoincrement():
    from app.core.db.schema import _ddl_for
    ddl = _ddl_for("postgresql")
    assert "AUTOINCREMENT" not in ddl.upper()
    assert "SERIAL" in ddl.upper() or "GENERATED" in ddl.upper()


def test_pg_schema_migration_path(monkeypatch):
    from unittest.mock import MagicMock
    from app.core.db import schema
    
    mock_engine = MagicMock()
    mock_engine.dialect.name = "postgresql"
    
    monkeypatch.setattr("app.core.db.connection.get_engine", lambda: mock_engine)
    
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.cursor.return_value = mock_cur
    
    def mock_fetchall():
        if mock_cur.execute.call_count == 0:
            return []
        call_args = mock_cur.execute.call_args_list[-1][0]
        params = call_args[1] if len(call_args) > 1 else ()
        table_name = params[0] if params else ""
        
        if table_name == "batch_runs":
            return [("source_dir",), ("target_dir",)]
        elif table_name == "batch_errors":
            return [("id",), ("batch_id",)]
        elif table_name == "batch_summary":
            return [("gpu_peak_pct",), ("vram_peak_mb",)]
        elif table_name == "batch_telemetry":
            return [("gpu_pct",), ("vram_mb",)]
        return []
        
    mock_cur.fetchall.side_effect = mock_fetchall
    
    schema._create_tables(mock_conn)
    
    executed_stmts = [call[0][0] for call in mock_cur.execute.call_args_list]
    
    # Check that migrations were run
    assert any("ALTER TABLE batch_runs ADD COLUMN heuristic_version TEXT" in s for s in executed_stmts)
    assert any("ALTER TABLE batch_runs ADD COLUMN priority INTEGER NOT NULL DEFAULT 0" in s for s in executed_stmts)
    assert any("ALTER TABLE batch_errors ADD COLUMN is_dlq BOOLEAN DEFAULT FALSE" in s for s in executed_stmts)
    assert any("ALTER TABLE batch_summary DROP COLUMN gpu_peak_pct" in s for s in executed_stmts)
    assert any("ALTER TABLE batch_summary DROP COLUMN vram_peak_mb" in s for s in executed_stmts)
    assert any("ALTER TABLE batch_telemetry DROP COLUMN gpu_pct" in s for s in executed_stmts)
    assert any("ALTER TABLE batch_telemetry DROP COLUMN vram_mb" in s for s in executed_stmts)

