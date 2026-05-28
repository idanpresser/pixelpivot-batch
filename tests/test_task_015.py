"""Task 015 - reap orphaned 'running' batches on startup.

Batches run as fire-and-forget background work and only transition to
completed/failed from within their own process. A crash mid-batch leaves a
batch_runs row stuck at status='running' forever (a permanent ghost in
/batch/status and history). A startup reaper transitions those to a terminal
'interrupted' state.
"""

import sqlite3

from app.core.db.schema import init_db
from app.core.db.repositories.batch import BatchRepository


def _conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    init_db(c)
    return c


def _new_run(repo, conn):
    return repo.create_run(
        conn, source_dir="s", target_dir="d", target_format="webp",
        tool="magick", trigger_type="manual",
    )


def test_reap_marks_running_as_interrupted_terminal():
    conn = _conn()
    repo = BatchRepository()

    running_id = _new_run(repo, conn)   # create_run inserts status='running'
    done_id = _new_run(repo, conn)
    repo.update_status(conn, done_id, "completed")
    conn.commit()

    reaped = repo.reap_stale_running(conn)
    conn.commit()

    assert reaped == 1

    running_row = conn.execute(
        "SELECT status, completed_at FROM batch_runs WHERE id = ?", (running_id,)
    ).fetchone()
    assert running_row["status"] == "interrupted"
    assert running_row["completed_at"] is not None

    # A terminal run must be left untouched.
    done_row = conn.execute(
        "SELECT status FROM batch_runs WHERE id = ?", (done_id,)
    ).fetchone()
    assert done_row["status"] == "completed"


def test_reap_returns_zero_when_nothing_running():
    conn = _conn()
    repo = BatchRepository()
    done_id = _new_run(repo, conn)
    repo.update_status(conn, done_id, "completed")
    conn.commit()

    assert repo.reap_stale_running(conn) == 0
