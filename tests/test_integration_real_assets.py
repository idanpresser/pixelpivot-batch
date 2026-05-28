import pytest
import os
import shutil
import json
from pathlib import Path
from unittest.mock import MagicMock, patch, ANY
from app.batch_api.orchestrator import BatchOrchestrator
from app.batch_api.models import BatchRequest

@pytest.fixture
def real_images_dir():
    return Path("test_examples")

@pytest.mark.asyncio
async def test_full_pipeline_with_real_assets(real_images_dir, tmp_path):
    # Setup source and target dirs
    source_dir = tmp_path / "source"
    target_dir = tmp_path / "target"
    source_dir.mkdir()
    target_dir.mkdir()
    
    # Copy a few real images
    images_to_test = [
        "highRes_0055_4B91E53B4F08F2912ED97EF166C3EFE3C.jpg",
        "lowContrst_0510_23030E51A4400884518F3B044251A27C2.png"
    ]
    
    for img_name in images_to_test:
        shutil.copy(real_images_dir / img_name, source_dir / img_name)
    
    # Add a "fake" corrupt file
    corrupt_file = source_dir / "corrupt_0000_FAKE.jpg"
    corrupt_file.write_text("not an image")
    
    request = BatchRequest(
        source_dir=str(source_dir),
        target_dir=str(target_dir),
        target_format=["webp"],
        tool=["magick"],
        category=["general"]
    )
    
    heuristic_table = Path("app/heuristic_table.json")
    heuristic_table.parent.mkdir(parents=True, exist_ok=True)
    with open(heuristic_table, "w") as f:
        json.dump({"general": {"small": {"webp": {"magick": 80}}, "xlarge": {"webp": {"magick": 60}}}}, f)

    with patch("app.batch_api.orchestrator.BatchRepository") as mock_repo_class:
        mock_repo = mock_repo_class.return_value
        orchestrator = BatchOrchestrator()
        orchestrator.repo = mock_repo
        
        with patch("app.batch_api.orchestrator.get_connection") as mock_get_conn:
            mock_get_conn.return_value.__enter__.return_value = MagicMock()
            
            # SMARTER MOCK: Success for real files, Failure for 'corrupt'
            def side_effect(cmd, *args, **kwargs):
                mock_proc = MagicMock()
                mock_proc.__enter__.return_value = mock_proc
                mock_proc.pid = 1234
                
                # If it's a 'mogrify' command, we'll make it fail to test the fallback!
                if "mogrify" in cmd:
                    mock_proc.returncode = 1
                    mock_proc.communicate.return_value = ("", "mogrify batch simulation failure")
                else:
                    # Individual 'magick' call
                    # Check if 'corrupt' is in the command arguments
                    if any("corrupt" in str(arg) for arg in cmd):
                        mock_proc.returncode = 1
                        mock_proc.communicate.return_value = ("", "magick individual simulation failure")
                    else:
                        mock_proc.returncode = 0
                        mock_proc.communicate.return_value = ("", "")
                        # Simulate output file creation
                        out_path = cmd[-1]
                        with open(out_path, "w") as f: f.write("fake output")
                return mock_proc

            with patch("app.core.converters.base.subprocess.Popen", side_effect=side_effect):
                with patch("app.core.converters.base.TelemetryMonitor"):
                    orchestrator.execute_batch(run_id=999, request=request)
                    
                    assert mock_repo.save_summary.called
                    _, kwargs = mock_repo.save_summary.call_args
                    
                    print(f"DEBUG Summary: {kwargs}")
                    
                    # 2 real files should succeed, 1 fake should fail
                    assert kwargs["success_count"] == 2
                    assert kwargs["failure_count"] == 1
                    assert kwargs["success_count"] + kwargs["failure_count"] == 3

    if heuristic_table.exists():
        heuristic_table.unlink()
