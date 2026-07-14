import pytest
import time
import os
import asyncio
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

@pytest.mark.asyncio
async def test_hot_folder_trigger_failure_marks_run_failed(tmp_path):
    """qk1.6: when dispatch (execute_batch) raises, the run must not be left in
    'running'. The handler must transition it to 'failed' so it is not orphaned
    until the next restart reap."""
    mock_orchestrator = MagicMock()
    mock_orchestrator.execute_batch.side_effect = RuntimeError("dispatch boom")
    mock_loop = asyncio.get_running_loop()

    source_dir = tmp_path / "source"
    target_dir = tmp_path / "target"
    source_dir.mkdir()
    target_dir.mkdir()
    (source_dir / "file1.png").write_text("dummy")

    config = {
        "source_dir": str(source_dir),
        "target_dir": str(target_dir),
        "target_format": ["webp"],
        "tool": ["magick"],
        "category": ["general"],
    }

    handler = HotFolderHandler(mock_orchestrator, mock_loop, config, debounce_seconds=0.1)
    handler.repo.create_run = MagicMock(return_value=456)
    handler.repo.update_status = MagicMock()

    with patch("app.batch_api.hot_folder.get_connection") as mock_get_conn:
        mock_get_conn.return_value.__enter__.return_value = MagicMock()
        await handler._async_trigger_batch()

    assert handler.repo.update_status.called, "update_status never called; run left 'running'"
    call_args = handler.repo.update_status.call_args[0]
    assert 456 in call_args, f"run_id 456 not in update_status args: {call_args}"
    assert "failed" in call_args, f"'failed' status not written: {call_args}"


@pytest.mark.asyncio
async def test_hot_folder_two_waves(tmp_path):
    """
    Verify that hot folder only triggers conversion for new/changed files
    by dropping files in two waves.
    """
    mock_orchestrator = MagicMock()
    mock_loop = asyncio.get_running_loop()
    
    source_dir = tmp_path / "source"
    target_dir = tmp_path / "target"
    source_dir.mkdir()
    target_dir.mkdir()
    
    config = {
        "source_dir": str(source_dir),
        "target_dir": str(target_dir),
        "target_format": ["webp"],
        "tool": ["magick"],
        "category": ["general"]
    }
    
    handler = HotFolderHandler(mock_orchestrator, mock_loop, config, debounce_seconds=0.1)
    
    # Wave 1: Write file1.png and file2.png
    f1 = source_dir / "file1.png"
    f2 = source_dir / "file2.png"
    f1.write_text("dummy")
    f2.write_text("dummy")
    
    handler.repo.create_run = MagicMock(side_effect=[123, 124])
    
    with patch("app.batch_api.hot_folder.get_connection") as mock_get_conn:
        mock_get_conn.return_value.__enter__.return_value = MagicMock()
        
        # Trigger Wave 1
        await handler._async_trigger_batch()
        
        # Assert execute_batch was called with both files
        mock_orchestrator.execute_batch.assert_called_once()
        run_id, request = mock_orchestrator.execute_batch.call_args[0]
        assert run_id == 123
        assert set(request.input_files) == {str(f1), str(f2)}
        
        # Reset mock
        mock_orchestrator.reset_mock()
        
        # Simulate that Wave 1 finished and created outputs (touch output files)
        (target_dir / "file1.webp").write_text("output")
        (target_dir / "file2.webp").write_text("output")
        
        # Wave 2: Write file3.png
        f3 = source_dir / "file3.png"
        f3.write_text("dummy")
        
        # Trigger Wave 2
        await handler._async_trigger_batch()
        
        # Assert execute_batch was called with ONLY file3.png
        mock_orchestrator.execute_batch.assert_called_once()
        run_id2, request2 = mock_orchestrator.execute_batch.call_args[0]
        assert run_id2 == 124
        assert request2.input_files == [str(f3)]
