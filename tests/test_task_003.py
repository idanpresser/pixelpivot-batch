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
    
    from app.core.converters.magick_converter import MagickConverter
    from app.core.converters.ffmpeg_converter import FFmpegConverter
    
    orchestrator.converters["magick"] = MagickConverter(magick_path="magick")
    orchestrator.converters["ffmpeg"] = FFmpegConverter(ffmpeg_path="ffmpeg")
    
    with get_connection() as conn:
        run_id = orchestrator.repo.create_run(conn, str(tmp_path), str(tmp_path), "['webp']", "['magick', 'ffmpeg']", "api")

    # Patch probe_image_dimensions and subprocess.Popen
    with patch("app.core.utils.probe_image_dimensions", return_value=(800, 600)) as mock_probe, \
         patch("subprocess.Popen") as mock_popen:
        
        # Mock Popen to return success
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate.return_value = ("stdout", "stderr")
        mock_proc.__enter__.return_value = mock_proc
        mock_popen.return_value = mock_proc
        
        # We also need to mock os.path.exists and os.path.getsize for the output files
        # so the converters think they succeeded.
        with patch("os.path.exists", return_value=True), \
             patch("os.path.getsize", return_value=100), \
             patch("os.replace", return_value=None):
            
            orchestrator.execute_batch(run_id, request)
        
        # N=2 images. So probe_image_dimensions should be called exactly 2 times!
        # Even though we ran 2 categories * 2 tools * 1 format = 4 cells.
        # Magick and FFmpeg both do resolution bucketing/grouping which used to re-probe.
        assert mock_probe.call_count == 2, f"Expected 2 calls, got {mock_probe.call_count}"
