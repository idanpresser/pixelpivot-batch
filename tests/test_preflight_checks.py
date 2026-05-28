import pytest
from unittest.mock import MagicMock, patch, ANY
from pathlib import Path
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
async def test_preflight_low_memory_aborts_batch(orchestrator, tmp_path):
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    (source_dir / "img1.jpg").write_text("dummy")
    
    target_dir = tmp_path / "dst"
    
    request = BatchRequest(
        source_dir=str(source_dir),
        target_dir=str(target_dir),
        target_format=["webp"],
        tool=["magick"]
    )
    
    # Mock psutil to simulate critically low memory (< 50MB)
    mock_vm = MagicMock()
    mock_vm.available = 10 * 1024 * 1024  # 10 MB available
    mock_vm.total = 16 * 1024 * 1024 * 1024
    
    with patch("psutil.virtual_memory", return_value=mock_vm):
        with patch("app.batch_api.orchestrator.get_connection") as mock_get_conn:
            mock_get_conn.return_value.__enter__.return_value = MagicMock()
            
            orchestrator.execute_batch(run_id=999, request=request)
            
            # The orchestrator should have aborted due to memory and NOT called the converter
            orchestrator.converters["magick"].convert_batch.assert_not_called()
            
            # The run status should be updated to "failed"
            orchestrator.repo.update_status.assert_called_with(
                ANY, 999, "failed"
            )

@pytest.mark.asyncio
async def test_preflight_low_disk_aborts_batch(orchestrator, tmp_path):
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    (source_dir / "img1.jpg").write_text("dummy")
    
    target_dir = tmp_path / "dst"
    
    request = BatchRequest(
        source_dir=str(source_dir),
        target_dir=str(target_dir),
        target_format=["webp"],
        tool=["magick"]
    )
    
    # Mock psutil to simulate plenty of memory
    mock_vm = MagicMock()
    mock_vm.available = 2 * 1024 * 1024 * 1024  # 2 GB available
    mock_vm.total = 16 * 1024 * 1024 * 1024
    
    # Mock shutil.disk_usage to simulate critically low disk space (< 50MB)
    low_disk_usage = (100 * 1024 * 1024, 90 * 1024 * 1024, 10 * 1024 * 1024) # 10 MB free
    
    with patch("psutil.virtual_memory", return_value=mock_vm):
        with patch("shutil.disk_usage", return_value=low_disk_usage):
            with patch("app.batch_api.orchestrator.get_connection") as mock_get_conn:
                mock_get_conn.return_value.__enter__.return_value = MagicMock()
                
                orchestrator.execute_batch(run_id=888, request=request)
                
                # The orchestrator should have aborted due to disk space and NOT called the converter
                orchestrator.converters["magick"].convert_batch.assert_not_called()
                
                # The run status should be updated to "failed"
                orchestrator.repo.update_status.assert_called_with(
                    ANY, 888, "failed"
                )
