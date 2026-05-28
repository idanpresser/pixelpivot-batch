"""
RED tests: BatchOrchestrator must support magick, ffmpeg, and vips as dispatch targets.
Currently fails because only 'magick' is registered in the converter factory.
"""
import pytest
from unittest.mock import MagicMock, patch, ANY

from app.batch_api.orchestrator import BatchOrchestrator
from app.batch_api.models import BatchRequest
from app.core.converters.ffmpeg_converter import FFmpegConverter
from app.core.converters.magick_converter import MagickConverter
from app.core.converters.vips_converter import VipsConverter


@pytest.fixture
def orchestrator():
    with patch("app.batch_api.orchestrator.HeuristicInterpolator"):
        with patch("app.batch_api.orchestrator.BatchRepository"):
            return BatchOrchestrator()


def test_orchestrator_registers_magick(orchestrator):
    assert "magick" in orchestrator.converters
    assert isinstance(orchestrator.converters["magick"], MagickConverter)


def test_orchestrator_registers_ffmpeg(orchestrator):
    assert "ffmpeg" in orchestrator.converters, (
        "BatchOrchestrator must register FFmpegConverter under 'ffmpeg'"
    )
    assert isinstance(orchestrator.converters["ffmpeg"], FFmpegConverter)


def test_orchestrator_registers_vips(orchestrator):
    assert "vips" in orchestrator.converters, (
        "BatchOrchestrator must register VipsConverter under 'vips'"
    )
    assert isinstance(orchestrator.converters["vips"], VipsConverter)


@pytest.mark.asyncio
async def test_execute_batch_dispatches_to_ffmpeg(tmp_path):
    """execute_batch routes to FFmpegConverter when tool=['ffmpeg']."""
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    (source_dir / "img.jpg").write_bytes(b"fake")

    with patch("app.batch_api.orchestrator.HeuristicInterpolator"):
        with patch("app.batch_api.orchestrator.BatchRepository"):
            orch = BatchOrchestrator()
            orch.interpolator.get_interpolated_quality.return_value = 30.0
            mock_converter = MagicMock()
            mock_converter.is_broken = False
            mock_converter.convert_batch.return_value = {
                "success_count": 1, "failure_count": 0, "errors": []
            }
            orch.converters["ffmpeg"] = mock_converter

            request = BatchRequest(
                source_dir=str(source_dir),
                target_dir=str(tmp_path / "out"),
                target_format=["avif"],
                tool=["ffmpeg"],
            )

            with patch("app.batch_api.orchestrator.get_connection") as mock_conn:
                mock_conn.return_value.__enter__.return_value = MagicMock()
                with patch("PIL.Image.open") as mock_open:
                    mock_open.return_value.__enter__.return_value.size = (800, 600)
                    orch.execute_batch(run_id=1, request=request)

            mock_converter.convert_batch.assert_called_once()


@pytest.mark.asyncio
async def test_execute_batch_dispatches_to_vips(tmp_path):
    """execute_batch routes to VipsConverter when tool=['vips']."""
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    (source_dir / "img.png").write_bytes(b"fake")

    with patch("app.batch_api.orchestrator.HeuristicInterpolator"):
        with patch("app.batch_api.orchestrator.BatchRepository"):
            orch = BatchOrchestrator()
            orch.interpolator.get_interpolated_quality.return_value = 75.0
            mock_converter = MagicMock()
            mock_converter.is_broken = False
            mock_converter.convert_batch.return_value = {
                "success_count": 1, "failure_count": 0, "errors": []
            }
            orch.converters["vips"] = mock_converter

            request = BatchRequest(
                source_dir=str(source_dir),
                target_dir=str(tmp_path / "out"),
                target_format=["avif"],
                tool=["vips"],
            )

            with patch("app.batch_api.orchestrator.get_connection") as mock_conn:
                mock_conn.return_value.__enter__.return_value = MagicMock()
                with patch("PIL.Image.open") as mock_open:
                    mock_open.return_value.__enter__.return_value.size = (400, 300)
                    orch.execute_batch(run_id=2, request=request)

            mock_converter.convert_batch.assert_called_once()


@pytest.mark.asyncio
async def test_execute_batch_raises_on_unknown_tool(tmp_path):
    """execute_batch must update status to 'failed' for an unregistered tool."""
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    (source_dir / "img.jpg").write_bytes(b"fake")

    with patch("app.batch_api.orchestrator.HeuristicInterpolator"):
        with patch("app.batch_api.orchestrator.BatchRepository"):
            orch = BatchOrchestrator()
            # Simulate an unregistered tool by removing it from the factory.
            orch.converters.pop("sharp", None)

            request = BatchRequest(
                source_dir=str(source_dir),
                target_dir=str(tmp_path / "out"),
                target_format=["avif"],
                tool=["sharp"],  # removed above
            )

            with patch("app.batch_api.orchestrator.get_connection") as mock_conn:
                mock_conn_ctx = MagicMock()
                mock_conn.return_value.__enter__.return_value = mock_conn_ctx
                with patch("PIL.Image.open") as mock_open:
                    mock_open.return_value.__enter__.return_value.size = (100, 100)
                    orch.execute_batch(run_id=3, request=request)

            orch.repo.update_status.assert_called_with(ANY, 3, "failed")
