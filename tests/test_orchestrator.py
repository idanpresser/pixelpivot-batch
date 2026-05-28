import pytest
import os
from pathlib import Path
from unittest.mock import MagicMock, patch, ANY
from app.batch_api.orchestrator import BatchOrchestrator
from app.batch_api.models import BatchRequest

@pytest.fixture
def orchestrator():
    with patch("app.batch_api.orchestrator.HeuristicInterpolator"):
        with patch("app.batch_api.orchestrator.MagickConverter"):
            with patch("app.batch_api.orchestrator.BatchRepository") as mock_repo_class:
                orch = BatchOrchestrator()
                orch.repo = mock_repo_class.return_value
                return orch

@pytest.mark.asyncio
async def test_execute_batch_success(orchestrator, tmp_path):
    # Setup source dir with some dummy images
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    (source_dir / "img1.jpg").write_text("dummy")
    (source_dir / "img2.png").write_text("dummy")
    
    target_dir = tmp_path / "dst"
    
    request = BatchRequest(
        source_dir=str(source_dir),
        target_dir=str(target_dir),
        target_format=["webp"],
        tool=["magick"]
    )
    
    # Mock dependencies
    orchestrator.interpolator.get_interpolated_quality.return_value = 85.0
    orchestrator.converters["magick"].is_broken = False
    orchestrator.converters["magick"].convert_batch.return_value = {
        "success_count": 2,
        "failure_count": 0,
        "errors": []
    }
    
    # Mock DB connection and repo
    with patch("app.batch_api.orchestrator.get_connection") as mock_get_conn:
        mock_get_conn.return_value.__enter__.return_value = MagicMock()
        
        # Mock PIL Image to avoid reading real files
        with patch("PIL.Image.open") as mock_image_open:
            mock_img = mock_image_open.return_value.__enter__.return_value
            mock_img.size = (1000, 1000)
            
            orchestrator.execute_batch(run_id=123, request=request)
            
            # Verify orchestrator steps
            assert orchestrator.interpolator.get_interpolated_quality.call_count == 2
            orchestrator.converters["magick"].convert_batch.assert_called_once()
            
            # Since I patched repo in __init__, I should check orchestrator.repo
            orchestrator.repo.save_summary.assert_called_once()
            orchestrator.repo.update_status.assert_called_with(
                ANY, 123, "completed", total_images=2
            )
