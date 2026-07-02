# tests/db/test_engine_factory.py
import os
from app.core.db import connection as conn


def test_engine_defaults_to_sqlite(tmp_path, monkeypatch):
    monkeypatch.delenv("PIXELPIVOT_DB_URL", raising=False)
    monkeypatch.setenv("PIXELPIVOT_DB_PATH", str(tmp_path / "t.db"))
    conn.reset_engine_cache()
    eng = conn.get_engine()
    assert eng.dialect.name == "sqlite"


def test_engine_url_override_selects_dialect(monkeypatch):
    try:
        import psycopg
    except ImportError:
        import pytest
        pytest.skip("psycopg (PostgreSQL driver) not installed")
        
    monkeypatch.setenv("PIXELPIVOT_DB_URL", "postgresql+psycopg://u:p@localhost/x")
    conn.reset_engine_cache()
    eng = conn.get_engine()
    assert eng.dialect.name == "postgresql"
    conn.reset_engine_cache()


def test_sqlite_connection_has_wal(tmp_path, monkeypatch):
    monkeypatch.delenv("PIXELPIVOT_DB_URL", raising=False)
    monkeypatch.setenv("PIXELPIVOT_DB_PATH", str(tmp_path / "wal.db"))
    conn.reset_engine_cache()
    raw = conn.get_engine().raw_connection()
    try:
        cur = raw.cursor()
        cur.execute("PRAGMA journal_mode")
        assert cur.fetchone()[0].lower() == "wal"
    finally:
        raw.close()
