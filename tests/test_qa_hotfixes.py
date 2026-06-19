import pytest
import time
import os
import threading
from unittest.mock import MagicMock, patch
from pathlib import Path

from app.batch_api.hot_folder import HotFolderHandler, HotFolderManager
from app.core.converters.sharp_converter import SharpConverter

def test_loop_prevention(tmp_path):
    mock_orchestrator = MagicMock()
    mock_loop = MagicMock()
    manager = HotFolderManager(mock_orchestrator, mock_loop)
    
    same_dir = str(tmp_path / "same")
    os.makedirs(same_dir, exist_ok=True)
    
    config = {
        "source_dir": same_dir,
        "target_dir": same_dir,
        "target_format": ["webp"],
        "tool": ["magick"]
    }

    with pytest.raises(ValueError, match="cannot be the same"):
        manager.add_hot_folder(config)

def test_timer_cancellation_on_remove(tmp_path):
    mock_orchestrator = MagicMock()
    mock_loop = MagicMock()
    manager = HotFolderManager(mock_orchestrator, mock_loop)
    
    src_dir = str(tmp_path / "src")
    dst_dir = str(tmp_path / "dst")
    os.makedirs(src_dir, exist_ok=True)
    os.makedirs(dst_dir, exist_ok=True)
    
    config = {
        "source_dir": src_dir,
        "target_dir": dst_dir,
        "target_format": ["webp"],
        "tool": ["magick"]
    }
    
    watcher_id = manager.add_hot_folder(config)
    entry = manager.watchers[watcher_id]
    handler = entry["handler"]
    
    # Trigger a timer schedule
    event = MagicMock()
    event.is_directory = False
    event.src_path = os.path.join(src_dir, "new.jpg")
    handler.on_created(event)
    
    assert handler.timer is not None
    assert handler.timer.is_alive()
    
    # Remove watcher
    success = manager.remove_hot_folder(watcher_id)
    assert success is True
    assert handler.timer is None

def test_sharp_converter_thread_local_isolation():
    converter = SharpConverter(port=12345)
    
    # We mock socket.create_connection to return a unique Mock object
    socket_counter = 0
    created_sockets = []
    
    def mock_create_connection(address, timeout=None):
        nonlocal socket_counter
        socket_counter += 1
        mock_sock = MagicMock()
        mock_sock.id = socket_counter
        created_sockets.append(mock_sock)
        return mock_sock
        
    with patch("socket.create_connection", side_effect=mock_create_connection):
        # We will retrieve connection in Thread A and Thread B concurrently
        thread_sockets = {}
        
        def run_thread(name):
            sock1 = converter._get_connection()
            sock2 = converter._get_connection()
            # Verify that consecutive calls within the same thread return the SAME socket
            assert sock1 is sock2
            thread_sockets[name] = sock1
            
        t1 = threading.Thread(target=run_thread, args=("A",))
        t2 = threading.Thread(target=run_thread, args=("B",))
        
        t1.start()
        t2.start()
        
        t1.join()
        t2.join()
        
        # Verify both threads got a socket, and they are completely DIFFERENT socket objects!
        assert thread_sockets["A"] is not None
        assert thread_sockets["B"] is not None
        assert thread_sockets["A"] is not thread_sockets["B"]
        assert thread_sockets["A"].id != thread_sockets["B"].id
