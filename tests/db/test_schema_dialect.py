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
