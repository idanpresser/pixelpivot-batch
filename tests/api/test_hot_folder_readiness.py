import os
import time
import asyncio
import pytest
from unittest.mock import MagicMock, patch
from watchdog.events import FileCreatedEvent
from app.batch_api.hot_folder import HotFolderHandler

@pytest.mark.asyncio
async def test_hot_folder_readiness_delay(tmp_path):
    source_dir = tmp_path / "source"
    target_dir = tmp_path / "target"
    source_dir.mkdir()
    target_dir.mkdir()
    
    file_path = source_dir / "test.jpg"
    file_path.write_text("initial")
    
    orchestrator = MagicMock()
    async def mock_execute(run_id, req):
        pass
    orchestrator.execute_batch = mock_execute
    
    loop = asyncio.get_running_loop()
    config = {
        "source_dir": str(source_dir),
        "target_dir": str(target_dir),
        "target_format": "webp",
        "tool": "ffmpeg"
    }
    
    with patch("app.batch_api.hot_folder.BatchRepository") as mock_repo_cls:
        mock_repo = mock_repo_cls.return_value
        mock_repo.create_run.return_value = 123
        
        handler = HotFolderHandler(orchestrator, loop, config, debounce_seconds=0.1)
        
        with patch("app.batch_api.hot_folder.get_connection"), \
             patch("app.batch_api.hot_folder.HOT_FOLDER_READINESS_CHECK_MS", 100), \
             patch("app.batch_api.hot_folder.HOT_FOLDER_READINESS_TIMEOUT_MS", 1000):
            
            # Trigger the event
            handler._trigger_batch()
            
            # Simulate file growth in the background
            async def grow_file():
                await asyncio.sleep(0.2)
                file_path.write_text("growing...")
                await asyncio.sleep(0.2)
                file_path.write_text("done stable now")
                
            asyncio.create_task(grow_file())
            
            # Wait for the async task to complete
            start = time.time()
            success = False
            while (time.time() - start) < 3.0:
                if mock_repo.create_run.call_count >= 1:
                    success = True
                    break
                await asyncio.sleep(0.1)
            
            assert success
            assert mock_repo.create_run.call_count == 1

@pytest.mark.asyncio
async def test_hot_folder_double_trigger_prevention(tmp_path):
    source_dir = tmp_path / "source"
    target_dir = tmp_path / "target"
    source_dir.mkdir()
    
    orchestrator = MagicMock()
    async def mock_execute(run_id, req):
        pass
    orchestrator.execute_batch = mock_execute
    
    loop = asyncio.get_running_loop()
    config = {
        "source_dir": str(source_dir),
        "target_dir": str(target_dir),
        "target_format": "webp",
        "tool": "ffmpeg"
    }
    
    with patch("app.batch_api.hot_folder.BatchRepository") as mock_repo_cls:
        mock_repo = mock_repo_cls.return_value
        mock_repo.create_run.return_value = 123
        
        handler = HotFolderHandler(orchestrator, loop, config, debounce_seconds=0.1)
        
        with patch("app.batch_api.hot_folder.get_connection"), \
             patch("app.batch_api.hot_folder.HOT_FOLDER_READINESS_CHECK_MS", 50), \
             patch("app.batch_api.hot_folder.HOT_FOLDER_READINESS_TIMEOUT_MS", 500):
            
            # Fire 10 events rapidly
            for i in range(10):
                p = source_dir / f"test_{i}.jpg"
                p.write_text("dummy")
                handler.on_created(FileCreatedEvent(str(p)))
                await asyncio.sleep(0.01)
                
            # Wait for the async task to complete
            start = time.time()
            success = False
            while (time.time() - start) < 3.0:
                if mock_repo.create_run.call_count >= 1:
                    success = True
                    break
                await asyncio.sleep(0.1)
                
            assert success
            assert mock_repo.create_run.call_count == 1

@pytest.mark.asyncio
async def test_hot_folder_readiness_timeout(tmp_path):
    source_dir = tmp_path / "source"
    target_dir = tmp_path / "target"
    source_dir.mkdir()
    
    file_path = source_dir / "never_ready.jpg"
    file_path.write_text("dummy")
    
    orchestrator = MagicMock()
    loop = asyncio.get_running_loop()
    config = {
        "source_dir": str(source_dir),
        "target_dir": str(target_dir),
        "target_format": "webp",
        "tool": "ffmpeg"
    }
    
    with patch("app.batch_api.hot_folder.BatchRepository") as mock_repo_cls:
        mock_repo = mock_repo_cls.return_value
        
        handler = HotFolderHandler(orchestrator, loop, config, debounce_seconds=0.1)
        
        with patch("app.batch_api.hot_folder.get_connection"), \
             patch("app.batch_api.hot_folder.HOT_FOLDER_READINESS_TIMEOUT_MS", 300), \
             patch("app.batch_api.hot_folder.HOT_FOLDER_READINESS_CHECK_MS", 100), \
             patch("os.path.getsize") as mock_getsize:
            
            mock_getsize.side_effect = lambda p: int(time.time() * 1000)
            
            handler._trigger_batch()
            await asyncio.sleep(0.8)
            assert mock_repo.create_run.call_count == 0
