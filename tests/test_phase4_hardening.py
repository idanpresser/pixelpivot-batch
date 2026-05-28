
import sqlite3
import pytest
from unittest.mock import patch, MagicMock
from app.core.db.connection import get_connection

def test_sqlite_pragmas():
    """Verify that SQLite connections have the correct pragmas."""
    with get_connection() as conn:
        # Check busy_timeout
        res = conn.execute("PRAGMA busy_timeout").fetchone()
        assert res[0] == 5000
        
        # Check synchronous
        res = conn.execute("PRAGMA synchronous").fetchone()
        # NORMAL is 1
        assert res[0] == 1

def test_vips_converter_run_id_passing():
    """Verify that VipsConverter correctly passes run_id to _run_library."""
    from app.core.converters.vips_converter import VipsConverter
    conv = VipsConverter()
    
    with patch.object(conv, "_run_library") as mock_run:
        conv.convert("in.png", "out.webp", "webp", 80, run_id=123)
        args, kwargs = mock_run.call_args
        assert kwargs.get("run_id") == 123

def test_sharp_daemon_quality_preservation():
    """Verify that SharpConverter preserves float quality for JXL."""
    from app.core.converters.sharp_converter import SharpConverter
    conv = SharpConverter()
    
    # Mock socket and other things to avoid actual execution
    with patch.object(conv, "_ensure_daemon_running"), \
         patch.object(conv, "_get_connection") as mock_conn:
        
        mock_sock = MagicMock()
        mock_conn.return_value = mock_sock
        mock_sock.recv.return_value = b'{"success": true}\n'
        
        conv.convert("in.png", "out.jxl", "jxl", 0.5)
        
        # Check what was sent to socket
        sent_data = mock_sock.sendall.call_args[0][0].decode('utf-8')
        import json
        request = json.loads(sent_data.strip())
        assert request["quality"] == 0.5
        assert isinstance(request["quality"], float)

def test_sharp_daemon_int_quality_for_others():
    """Verify that SharpConverter casts quality to int for non-JXL formats."""
    from app.core.converters.sharp_converter import SharpConverter
    conv = SharpConverter()
    
    with patch.object(conv, "_ensure_daemon_running"), \
         patch.object(conv, "_get_connection") as mock_conn:
        
        mock_sock = MagicMock()
        mock_conn.return_value = mock_sock
        mock_sock.recv.return_value = b'{"success": true}\n'
        
        conv.convert("in.png", "out.webp", "webp", 80.5)
        
        # Check what was sent to socket
        sent_data = mock_sock.sendall.call_args[0][0].decode('utf-8')
        import json
        request = json.loads(sent_data.strip())
        assert request["quality"] == 80
        assert isinstance(request["quality"], int)
