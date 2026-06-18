import pytest
import os
import shutil
from pathlib import Path
from app.batch_api.orchestrator import BatchOrchestrator
from app.batch_api.models import BatchRequest
from unittest.mock import MagicMock, patch

@pytest.fixture
def real_images_dir():
    return Path("test_examples")

@pytest.fixture
def setup_dirs(tmp_path):
    source_dir = tmp_path / "source"
    target_dir = tmp_path / "target"
    source_dir.mkdir()
    target_dir.mkdir()
    return source_dir, target_dir

def has_tool(name):
    return shutil.which(name) is not None

@pytest.mark.asyncio
@pytest.mark.parametrize("tool,executable", [
    ("magick", "magick"),
    ("ffmpeg", "ffmpeg")
])
async def test_end_to_end_real_tools(tool, executable, real_images_dir, setup_dirs):
    if not has_tool(executable):
        pytest.skip(f"{executable} not found in path")
    
    source_dir, target_dir = setup_dirs
    
    # Pick up to 5 images from test_examples
    jpgs = list(real_images_dir.glob("*.jpg"))
    pngs = list(real_images_dir.glob("*.png"))
    
    import random
    selected_jpgs = random.sample(jpgs, min(len(jpgs), 5))
    selected_pngs = random.sample(pngs, min(len(pngs), 5))
    
    if not selected_jpgs and not selected_pngs:
        pytest.skip("No real image assets found in test_examples/")
        
    for p in selected_jpgs + selected_pngs:
        shutil.copy(p, source_dir / p.name)
    
    total_expected = len(selected_jpgs) + len(selected_pngs)
    
    request = BatchRequest(
        source_dir=str(source_dir),
        target_dir=str(target_dir),
        target_format=["webp"],
        tool=[tool],
        category=["general"]
    )
    
    orchestrator = BatchOrchestrator()
    
    # Mock DB calls to avoid needing a real SQLite file
    with patch("app.batch_api.orchestrator.get_connection") as mock_get_conn:
        mock_get_conn.return_value.__enter__.return_value = MagicMock()
        with patch.object(orchestrator.repo, "create_run", return_value=123):
            with patch.object(orchestrator.repo, "save_summary") as mock_save:
                with patch.object(orchestrator.repo, "update_status"):
                    orchestrator.execute_batch(run_id=123, request=request)
                    
                    assert mock_save.called
                    _, kwargs = mock_save.call_args
                    assert kwargs["success_count"] == total_expected
                    assert kwargs["failure_count"] == 0
                    
                    # Verify output files actually exist and are non-empty
                    for p in source_dir.iterdir():
                        out_file = target_dir / f"{p.stem}.webp"
                        assert out_file.exists()
                        assert out_file.stat().st_size > 0

@pytest.mark.asyncio
async def test_vips_real_assets(real_images_dir, setup_dirs):
    try:
        import pyvips
        # Test if libvips is actually loadable
        pyvips.Image.new_from_memory(b"", 1, 1, 1, 'black')
    except (ImportError, OSError, Exception):
        pytest.skip("pyvips or libvips not loadable")
        
    source_dir, target_dir = setup_dirs
    jpgs = list(real_images_dir.glob("*.jpg"))
    if not jpgs:
        pytest.skip("No JPEG assets found")
        
    shutil.copy(jpgs[0], source_dir / jpgs[0].name)
    
    request = BatchRequest(
        source_dir=str(source_dir),
        target_dir=str(target_dir),
        target_format=["avif"],
        tool=["vips"],
        category=["general"]
    )
    
    orchestrator = BatchOrchestrator()
    with patch("app.batch_api.orchestrator.get_connection") as mock_get_conn:
        mock_get_conn.return_value.__enter__.return_value = MagicMock()
        with patch.object(orchestrator.repo, "save_summary") as mock_save:
            orchestrator.execute_batch(run_id=456, request=request)
            assert mock_save.called
            assert mock_save.call_args[1]["success_count"] == 1
            
            out_file = target_dir / f"{jpgs[0].stem}.avif"
            assert out_file.exists()

@pytest.mark.asyncio
async def test_sharp_real_assets(real_images_dir, setup_dirs):
    if not has_tool("node"):
        pytest.skip("node not found")
        
    # Check if sharp is installed in node_modules
    if not os.path.exists("node_modules/sharp"):
        pytest.skip("sharp not found in node_modules")

    source_dir, target_dir = setup_dirs
    jpgs = list(real_images_dir.glob("*.jpg"))
    shutil.copy(jpgs[0], source_dir / jpgs[0].name)
    
    request = BatchRequest(
        source_dir=str(source_dir),
        target_dir=str(target_dir),
        target_format=["webp"],
        tool=["sharp"],
        category=["general"]
    )
    
    from app.core.converters.sharp_converter import SharpConverter
    orchestrator = BatchOrchestrator()
    # Replace default converters with one that has Sharp
    orchestrator.converters["sharp"] = SharpConverter()
    
    with patch("app.batch_api.orchestrator.get_connection") as mock_get_conn:
        mock_get_conn.return_value.__enter__.return_value = MagicMock()
        with patch.object(orchestrator.repo, "save_summary") as mock_save:
            try:
                orchestrator.execute_batch(run_id=789, request=request)
                assert mock_save.called
                assert mock_save.call_args[1]["success_count"] == 1
                
                out_file = target_dir / f"{jpgs[0].stem}.webp"
                assert out_file.exists()
            finally:
                if "sharp" in orchestrator.converters:
                    orchestrator.converters["sharp"]._stop_daemon()
