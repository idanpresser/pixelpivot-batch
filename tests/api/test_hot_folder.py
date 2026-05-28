import pytest
import time
import os
from unittest.mock import MagicMock, patch
from app.batch_api.hot_folder import HotFolderHandler

def test_hot_folder_debouncer(tmp_path):
    mock_orchestrator = MagicMock()
    mock_loop = MagicMock()
    # Configuration for the hot folder
    config = {
        "source_dir": str(tmp_path / "hot"),
        "target_dir": str(tmp_path / "out"),
        "target_format": "webp",
        "tool": "magick"
    }
    
    os.makedirs(config["source_dir"], exist_ok=True)
    
    with patch("app.batch_api.hot_folder.get_connection") as mock_get_conn:
        mock_get_conn.return_value.__enter__.return_value = MagicMock()
        
        with patch("app.batch_api.hot_folder.BatchRepository") as mock_repo_class:
            mock_repo = mock_repo_class.return_value
            mock_repo.create_run.return_value = 123
            
            with patch("app.batch_api.hot_folder.asyncio.run_coroutine_threadsafe") as mock_run_coro:
                # We'll use a short debounce for testing
                handler = HotFolderHandler(mock_orchestrator, mock_loop, config, debounce_seconds=0.5)
                
                # Simulate file creation events
                event = MagicMock()
                event.is_directory = False
                event.src_path = os.path.join(config["source_dir"], "test1.jpg")
                
                handler.on_created(event)
                
                # Coroutine should NOT be dispatched yet
                mock_run_coro.assert_not_called()
                
                # Wait less than debounce
                time.sleep(0.2)
                
                # Add another file
                event2 = MagicMock()
                event2.is_directory = False
                event2.src_path = os.path.join(config["source_dir"], "test2.jpg")
                handler.on_created(event2)
                
                # Wait for debounce to expire
                time.sleep(0.7) # Slightly more than 0.5
                
                # Now it should have been dispatched ONCE for both files
                mock_run_coro.assert_called_once()
                args, _ = mock_run_coro.call_args
                
                # Close the coroutine to avoid RuntimeWarning
                args[0].close()
                
                # First arg is the coroutine, second is the loop
                assert args[1] == mock_loop
