# tests/db/test_priority_column.py
from app.core.db.connection import get_connection
from app.core.db.schema import init_db


def test_batch_runs_has_priority_column():
    init_db()
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT priority FROM batch_runs LIMIT 0")
        cols = [d[0] for d in cur.description]
    assert "priority" in cols
