# tests/db/test_compat_seam.py
from app.core.db import connection as conn
from app.core.db.schema import init_db


def _fresh(tmp_path, monkeypatch):
    monkeypatch.delenv("PIXELPIVOT_DB_URL", raising=False)
    monkeypatch.setenv("PIXELPIVOT_DB_PATH", str(tmp_path / "seam.db"))
    conn.reset_engine_cache()
    init_db()


def test_qmark_param_and_named_row_access(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    with conn.get_connection() as c:
        cur = c.cursor()
        cur.execute(
            "INSERT INTO batch_runs (source_dir, target_dir, target_format, tool, trigger_type, status) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("src", "dst", "avif", "ffmpeg", "manual", "running"),
        )
        rid = cur.lastrowid
    with conn.get_connection() as c:
        cur = c.cursor()
        cur.execute("SELECT * FROM batch_runs WHERE id = ?", (rid,))
        row = cur.fetchone()
        assert row["source_dir"] == "src"      # named access preserved
        assert row["status"] == "running"


def test_commit_and_rollback(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    try:
        with conn.get_connection() as c:
            c.cursor().execute(
                "INSERT INTO batch_runs (source_dir, target_dir, target_format, tool, trigger_type, status) "
                "VALUES (?,?,?,?,?,?)", ("a", "b", "webp", "vips", "manual", "running"))
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    with conn.get_connection() as c:
        cur = c.cursor()
        cur.execute("SELECT COUNT(*) AS n FROM batch_runs")
        assert cur.fetchone()["n"] == 0   # rolled back
