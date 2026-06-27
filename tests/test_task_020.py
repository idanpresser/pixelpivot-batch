"""Task 020 - per-tick telemetry must land for batch_runs, not collide with
the legacy pipeline_runs FK.

Today `pipeline_telemetry.run_id REFERENCES pipeline_runs(id)`, but the
TelemetryMonitor is given a batch_runs.id, so every insert violates the FK
and the whole sample batch is dropped. Fix: route per-tick samples to a new
`batch_telemetry` table keyed on batch_runs(id).
"""
from __future__ import annotations
import logging
import os
import sqlite3
import tempfile
from pathlib import Path

import pytest

_TMP_DIR = tempfile.mkdtemp(prefix="pp_t020_")
_DB_PATH = Path(_TMP_DIR) / "audit.db"

@pytest.fixture(autouse=True)
def setup_env(monkeypatch):
    monkeypatch.setenv("PIXELPIVOT_DB_PATH", str(_DB_PATH))


@pytest.fixture
def fresh_db() -> sqlite3.Connection:
    """Bootstrap a fresh DB via init_db() and yield a connection to it."""
    # Re-import on each fixture so a stale module-level path can't leak.
    from app.core.db.schema import init_db
    from app.core.db.connection import get_connection

    # Wipe any prior file so PRAGMA integrity_check has a clean slate.
    if _DB_PATH.exists():
        _DB_PATH.unlink()
    init_db()
    with get_connection() as conn:
        yield conn


def _create_batch_run(conn: sqlite3.Connection) -> int:
    """Insert a minimal batch_runs row and return its id."""
    from app.core.db.repositories.batch import BatchRepository
    repo = BatchRepository()
    return repo.create_run(
        conn,
        source_dir="/tmp/src",
        target_dir="/tmp/tgt",
        target_format="webp",
        tool="magick",
        trigger_type="manual",
        heuristic_version="2.0.0",
    )


def test_per_tick_sample_lands_for_batch_run(fresh_db: sqlite3.Connection) -> None:
    """A sample tagged with a batch_runs.id must be persisted, not dropped."""
    from app.core.db.repositories.telemetry import insert_telemetry_batch
    batch_id = _create_batch_run(fresh_db)
    # (run_id, timestamp, cpu_pct, ram_mb) — GPU columns dropped (CPU-only target).
    samples = [
        (batch_id, "2026-05-28 03:48:08", 1.0, 2.0),
        (batch_id, "2026-05-28 03:48:09", 5.0, 6.0),
    ]
    insert_telemetry_batch(fresh_db, samples, auto_commit=True)
    # The fixed code writes to a batch-keyed table -- assert rows landed.
    cur = fresh_db.cursor()
    try:
        cur.execute(
            "SELECT COUNT(*) FROM batch_telemetry WHERE run_id = ?",
            (batch_id,),
        )
        count = cur.fetchone()[0]
    finally:
        cur.close()
    assert count == 2, f"expected 2 rows in batch_telemetry; got {count}"


class _CaptureHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def test_no_foreign_key_constraint_warning_on_batch_insert(fresh_db: sqlite3.Connection) -> None:
    """No 'FOREIGN KEY constraint failed' warning may be emitted for a sample
    whose run_id is a valid batch_runs.id."""
    from app.core.db.repositories.telemetry import insert_telemetry_batch
    logger = logging.getLogger("app.core.db.repositories.telemetry")
    cap = _CaptureHandler()
    logger.addHandler(cap)
    try:
        batch_id = _create_batch_run(fresh_db)
        insert_telemetry_batch(
            fresh_db,
            [(batch_id, "2026-05-28 03:48:08", 1.0, 2.0)],
            auto_commit=True,
        )
    finally:
        logger.removeHandler(cap)
    fk_msgs = [
        r for r in cap.records
        if "FOREIGN KEY" in r.getMessage() or "foreign key" in r.getMessage().lower()
    ]
    assert not fk_msgs, (
        f"unexpected FK warnings during batch telemetry insert: "
        f"{[r.getMessage() for r in fk_msgs]}"
    )


def test_orphan_run_id_does_not_crash(fresh_db: sqlite3.Connection) -> None:
    """A sample with a run_id absent from batch_runs must not raise to the
    caller (best-effort contract preserved)."""
    from app.core.db.repositories.telemetry import insert_telemetry_batch
    # Bogus run_id; either dropped silently (if FK kept) or accepted (if FK
    # dropped). Either is fine; what is forbidden is a crash propagating to
    # the caller.
    insert_telemetry_batch(
        fresh_db,
        [(99999, "2026-05-28 03:48:08", 1.0, 2.0)],
        auto_commit=True,
    )
