import os
import time
import asyncio
import pytest
from unittest.mock import MagicMock, patch
from app.batch_api.hot_folder import HotFolderManager, HotFolderHandler

@pytest.mark.asyncio
async def test_hot_folder_polling_fallback(tmp_path):
    """
    Verify that polling detects new files even if no events are fired.
    """
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    
    orchestrator = MagicMock()
    orchestrator.interpolator = MagicMock()
    orchestrator.interpolator.version = "1.0.0"
    orchestrator.execute_batch = MagicMock(side_effect=lambda run_id, req: asyncio.sleep(0))
    
    loop = asyncio.get_running_loop()
    
    with patch("app.batch_api.hot_folder.HOT_FOLDER_POLLING_INTERVAL_S", 0.1), \
         patch("app.batch_api.hot_folder.HOT_FOLDER_READINESS_CHECK_MS", 50), \
         patch("app.batch_api.hot_folder.HOT_FOLDER_READINESS_TIMEOUT_MS", 500):
        
        manager = HotFolderManager(orchestrator, loop)
        manager.start()
        
        watcher_id = manager.add_hot_folder({
            "source_dir": str(source_dir),
            "target_dir": str(tmp_path / "target"),
            "target_format": ["webp"],
            "tool": ["ffmpeg"]
        })
        
        handler = manager.watchers[watcher_id]["handler"]
        handler.debounce_seconds = 0.1
        
        file_path = source_dir / "test.jpg"
        file_path.write_text("dummy")
        
        with patch.object(handler.repo, "create_run", return_value=123) as mock_create, \
             patch("app.batch_api.hot_folder.get_connection"):
            
            # Wait for polling loop to detect and trigger
            for _ in range(30):
                if mock_create.call_count >= 1:
                    break
                await asyncio.sleep(0.1)
                
            assert mock_create.call_count == 1
            
        manager.stop()

@pytest.mark.asyncio
async def test_hot_folder_polling_no_double_trigger(tmp_path):
    """
    Verify that polling and events together don't trigger multiple batches.
    """
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    
    orchestrator = MagicMock()
    orchestrator.interpolator = MagicMock()
    orchestrator.interpolator.version = "1.0.0"
    orchestrator.execute_batch = MagicMock(side_effect=lambda run_id, req: asyncio.sleep(0))
    
    loop = asyncio.get_running_loop()
    
    with patch("app.batch_api.hot_folder.HOT_FOLDER_POLLING_INTERVAL_S", 0.1), \
         patch("app.batch_api.hot_folder.HOT_FOLDER_READINESS_CHECK_MS", 50), \
         patch("app.batch_api.hot_folder.HOT_FOLDER_READINESS_TIMEOUT_MS", 500):
        
        manager = HotFolderManager(orchestrator, loop)
        manager.start()
        
        watcher_id = manager.add_hot_folder({
            "source_dir": str(source_dir),
            "target_dir": str(tmp_path / "target"),
            "target_format": ["webp"],
            "tool": ["ffmpeg"]
        })
        
        handler = manager.watchers[watcher_id]["handler"]
        handler.debounce_seconds = 0.1
        
        file_path = source_dir / "test.jpg"
        file_path.write_text("dummy")
        
        with patch.object(handler.repo, "create_run", return_value=123) as mock_create, \
             patch("app.batch_api.hot_folder.get_connection"):
            
            # 1. Fire an event manually
            from watchdog.events import FileCreatedEvent
            handler.on_created(FileCreatedEvent(str(file_path)))
            
            # 2. Wait for polling loop to also see it (should have triggered at least one reset)
            await asyncio.sleep(0.2)
            
            # 3. Wait for the debounce + readiness to complete
            for _ in range(30):
                if mock_create.call_count >= 1:
                    break
                await asyncio.sleep(0.1)
                
            # Should only trigger once
            assert mock_create.call_count == 1
            
        manager.stop()
