"""Calibration is gated off; quality is heuristic-only (bead z0a).

The calibration machinery (calibration_results table, repo methods, SSIM
constants) is kept intact but inert: ``config.CALIBRATION_ENABLED`` defaults
to False, and the persistence path is a no-op while disabled. The runtime
quality path resolves every image via ``HeuristicInterpolator`` with a
``default_quality_for`` fallback — it must never consult calibration.
"""

import sqlite3

import pytest

from app.core import config
from app.core.db.schema import init_db
from app.core.db.repositories.batch import BatchRepository


def test_calibration_disabled_by_default():
    assert config.CALIBRATION_ENABLED is False


def _calibration_row_count(conn) -> int:
    cur = conn.cursor()
    try:
        cur.execute("SELECT COUNT(*) FROM calibration_results")
        return cur.fetchone()[0]
    finally:
        cur.close()


def test_save_calibration_result_is_noop_when_disabled(monkeypatch):
    """Disabled: method exists and the table exists, but nothing is written."""
    monkeypatch.setattr(config, "CALIBRATION_ENABLED", False)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    repo = BatchRepository()
    run_id = repo.create_run(conn, "src", "dst", "webp", "ffmpeg", "manual")

    repo.save_calibration_result(
        conn, run_id, "test.jpg", 0.98, 85.0, 3, [{"quality": 85, "ssim": 0.98}]
    )

    assert _calibration_row_count(conn) == 0


def test_save_calibration_result_persists_when_enabled(monkeypatch):
    """Enabled: the table + method remain fully functional."""
    monkeypatch.setattr(config, "CALIBRATION_ENABLED", True)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    repo = BatchRepository()
    run_id = repo.create_run(conn, "src", "dst", "webp", "ffmpeg", "manual")

    repo.save_calibration_result(
        conn, run_id, "test.jpg", 0.98, 85.0, 3, [{"quality": 85, "ssim": 0.98}]
    )

    assert _calibration_row_count(conn) == 1
