"""Invariants for the batch orchestrator's must-succeed / best-effort split.

The user classified ``duration_ms`` (per-batch summary) as the primary
telemetry signal — it must persist on every terminal run. Per-file failure
rows are best-effort: a failure when persisting them must not roll back the
summary write. These tests pin both invariants.
"""

from unittest.mock import MagicMock, patch

import pytest

from app.batch_api.models import BatchRequest
from app.batch_api.orchestrator import BatchOrchestrator
from app.core.db.connection import get_connection
from app.core.db.schema import init_db


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    """Point the SQLite layer at a tmp_path DB for the duration of the test.

    ``connection.py`` captures ``SQLITE_DB_PATH`` at import time, so we patch
    the captured binding in that module rather than the source in ``paths``.
    The DB is initialized fresh — no pollution from prior runs.
    """
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("app.core.db.connection.SQLITE_DB_PATH", db_path)
    with get_connection() as conn:
        init_db(conn)
    return db_path


def _make_source_files(tmp_path, names):
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    for n in names:
        (source_dir / n).write_text("dummy")
    return source_dir


@pytest.mark.asyncio
async def test_summary_persists_when_all_files_fail(tmp_path, isolated_db):
    """duration_ms must persist even when every file in the batch fails."""
    orch = BatchOrchestrator()
    mock_converter = MagicMock()
    mock_converter.is_broken = False
    mock_converter.convert_batch.return_value = {
        "success_count": 0,
        "failure_count": 3,
        "duration_ms": 250.0,
        "telemetry": {"cpu_avg": 10.0},
        "errors": [
            {"path": "file1.png", "error": "boom1"},
            {"path": "file2.png", "error": "boom2"},
            {"path": "file3.png", "error": "boom3"},
        ],
    }
    orch.converters["magick"] = mock_converter

    source_dir = _make_source_files(tmp_path, ["file1.png", "file2.png", "file3.png"])
    request = BatchRequest(
        source_dir=str(source_dir),
        target_dir=str(tmp_path / "target"),
        target_format=["webp"],
        tool=["magick"],
    )

    with get_connection() as conn:
        run_id = orch.repo.create_run(
            conn, request.source_dir, request.target_dir,
            ",".join(request.target_format), ",".join([t.value for t in request.tool]), "manual",
        )

    orch.execute_batch(run_id, request)

    with get_connection() as conn:
        summary = orch.repo.get_summary(conn, run_id)
        assert summary is not None
        assert summary["failure_count"] == 3
        assert summary["duration_ms"] > 0

        cur = conn.cursor()
        cur.execute("SELECT count(*) as cnt FROM batch_errors WHERE batch_id=?", (run_id,))
        assert cur.fetchone()["cnt"] == 3


@pytest.mark.asyncio
async def test_summary_survives_when_save_errors_raises(tmp_path, isolated_db):
    """If save_errors raises, save_summary must remain committed.

    This pins the must-succeed / best-effort contract: the summary's
    duration_ms — the primary telemetry signal — cannot be lost just because
    persistence of per-file failure rows hit a snag.
    """
    orch = BatchOrchestrator()
    mock_converter = MagicMock()
    mock_converter.is_broken = False
    mock_converter.convert_batch.return_value = {
        "success_count": 1,
        "failure_count": 2,
        "duration_ms": 500.0,
        "telemetry": {"cpu_avg": 5.0},
        "errors": [
            {"path": "a.png", "error": "boom_a"},
            {"path": "b.png", "error": "boom_b"},
        ],
    }
    orch.converters["magick"] = mock_converter

    source_dir = _make_source_files(tmp_path, ["a.png", "b.png", "c.png"])
    request = BatchRequest(
        source_dir=str(source_dir),
        target_dir=str(tmp_path / "target"),
        target_format=["webp"],
        tool=["magick"],
    )

    with get_connection() as conn:
        run_id = orch.repo.create_run(
            conn, request.source_dir, request.target_dir,
            ",".join(request.target_format), ",".join([t.value for t in request.tool]), "manual",
        )

    # Force save_errors to raise. The orchestrator's best-effort wrapper must
    # log a warning and continue; the prior must-succeed save_summary +
    # update_status writes (in a separate transaction) must remain.
    with patch.object(
        orch.repo, "save_errors", side_effect=RuntimeError("simulated DB failure")
    ):
        orch.execute_batch(run_id, request)

    with get_connection() as conn:
        summary = orch.repo.get_summary(conn, run_id)
        assert summary is not None, "save_summary was rolled back by save_errors failure"
        assert summary["duration_ms"] > 0
        assert summary["failure_count"] == 2

        run = orch.repo.get_run(conn, run_id)
        assert run["status"] == "completed", (
            "update_status was rolled back by save_errors failure"
        )

        cur = conn.cursor()
        cur.execute("SELECT count(*) as cnt FROM batch_errors WHERE batch_id=?", (run_id,))
        assert cur.fetchone()["cnt"] == 0, "best-effort write should have dropped"


@pytest.mark.asyncio
async def test_probe_quality_falls_back_safely_for_jxl(tmp_path):
    """JXL is a 0-100 quality (converters map it to a Butteraugli distance), so
    the fallback must be a high quality sourced from config - not the legacy 1.0,
    which would map to distance 9.9 (near-worst). See task_011."""
    from app.core.config import default_quality_for

    orch = BatchOrchestrator()

    with patch("PIL.Image.open", side_effect=Exception("Corrupt")):
        q = orch._probe_quality("dummy.png", "general", "magick", "jxl")
        assert q == default_quality_for("magick", "jxl")
        assert q >= 70
