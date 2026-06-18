import pytest
from unittest.mock import patch, MagicMock
from app.batch_api.orchestrator import BatchOrchestrator
from app.batch_api.models import BatchRequest, Tool
from app.core.db.connection import get_connection

def test_probe_once_dimension_cache(tmp_path, monkeypatch):
    """
    Regression test for Task 003: probe_image_dimensions should be called
    exactly N times (once per input file) during a multi-cell matrix run.
    """
    db_path = tmp_path / "test.db"
    import app.core.db.connection as connection
    monkeypatch.setattr(connection, "SQLITE_DB_PATH", db_path)

    from app.core.db.schema import init_db
    init_db()

    # Matrix: 2 categories * 2 tools * 1 format = 4 cells
    request = BatchRequest(
        source_dir=str(tmp_path),
        target_dir=str(tmp_path),
        category=["highRes", "lowRes"],
        tool=[Tool.magick, Tool.ffmpeg],
        target_format=["webp"]
    )

    (tmp_path / "test1.jpg").write_bytes(b"fake")
    (tmp_path / "test2.png").write_bytes(b"fake")

    orchestrator = BatchOrchestrator()

    # Task 003 is an orchestrator-level guarantee: dimensions are probed once
    # (in _probe_all_dimensions) and the cache is reused for every matrix cell,
    # so no per-cell re-probe occurs. The converters are mocked at convert_batch
    # here on purpose — feeding an unconstrained MagicMock subprocess into the
    # real ffmpeg retry/fallback path grew a multi-GB MagicMock child tree
    # (~2.7 GB RSS, the suite's memory bomb). Converter-internal probing is
    # covered separately; this test only asserts the orchestrator probes once.
    def _fake_convert_batch(input_paths, *args, **kwargs):
        return {
            "success_count": len(input_paths), "failure_count": 0,
            "duration_ms": 1.0, "telemetry": {}, "errors": [],
        }

    for _name in ("magick", "ffmpeg"):
        conv = MagicMock()
        conv.is_broken = False
        conv.convert_batch.side_effect = _fake_convert_batch
        orchestrator.converters[_name] = conv

    with get_connection() as conn:
        run_id = orchestrator.repo.create_run(conn, str(tmp_path), str(tmp_path), "['webp']", "['magick', 'ffmpeg']", "api")

    with patch("app.core.utils.probe_image_dimensions", return_value=(800, 600)) as mock_probe:
        orchestrator.execute_batch(run_id, request)

    # N=2 images, so probe_image_dimensions must be called exactly 2 times even
    # though the matrix ran 2 categories * 2 tools * 1 format = 4 cells.
    assert mock_probe.call_count == 2, f"Expected 2 calls, got {mock_probe.call_count}"
