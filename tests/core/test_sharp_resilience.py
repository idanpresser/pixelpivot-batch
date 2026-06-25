import socket
import json
import pytest
from unittest.mock import MagicMock, patch
from app.core.converters.sharp_converter import SharpConverter

@pytest.fixture
def mock_daemon():
    """Mocks the Sharp daemon subprocess and network."""
    with patch("subprocess.Popen") as mock_popen, \
         patch("socket.create_connection") as mock_connect, \
         patch("app.core.converters.sharp_converter.SharpConverter._test_daemon_ready", return_value=True), \
         patch("app.core.converters.sharp_converter.SharpConverter._is_port_open") as mock_is_port, \
         patch("app.core.converters.sharp_converter.SharpConverter._ensure_daemon_running"):
        
        mock_proc = mock_popen.return_value
        mock_proc.poll.return_value = None
        mock_proc.pid = 9999
        mock_is_port.return_value = True
        
        yield {
            "popen": mock_popen,
            "connect": mock_connect,
            "proc": mock_proc,
            "is_port": mock_is_port
        }

def test_sharp_daemon_restarts_on_connection_reset(mock_daemon):
    """
    Verify that if the socket resets, the daemon is stopped and restarted.
    """
    conv = SharpConverter()
    
    # First connection fails with Reset, second succeeds
    mock_sock1 = MagicMock()
    mock_sock1.sendall.side_effect = ConnectionResetError("Reset")
    
    mock_sock2 = MagicMock()
    mock_sock2.recv.return_value = json.dumps({"success": True, "duration_ms": 100}).encode("utf-8") + b"\n"
    
    mock_daemon["connect"].side_effect = [mock_sock1, mock_sock2]
    
    with patch("app.core.converters.sharp_converter.SharpConverter._stop_daemon") as mock_stop:
        res = conv.convert("in.jpg", "out.webp", "webp", 80)
        
        assert res["success"]
        assert mock_stop.call_count >= 1

def test_sharp_daemon_max_retries_and_circuit_break(mock_daemon):
    """
    Verify that after enough failed attempts, it gives up.
    """
    conv = SharpConverter()
    conv.failure_threshold = 1 # Force break on first convert() failure
    
    # All connections fail
    mock_daemon["connect"].side_effect = ConnectionRefusedError("Refused")
    
    with patch("app.core.converters.sharp_converter.SharpConverter._stop_daemon"):
        res = conv.convert("in.jpg", "out.webp", "webp", 80)
        
        assert not res["success"]
        assert "3 attempts" in res["error"]
        assert conv.is_broken

@pytest.mark.parametrize("error_type", [socket.timeout, ConnectionError, OSError])
def test_sharp_socket_resilience_types(mock_daemon, error_type):
    """
    Verify it handles various socket errors similarly.
    """
    conv = SharpConverter()
    
    mock_sock = MagicMock()
    mock_sock.sendall.side_effect = error_type("Error")
    mock_daemon["connect"].return_value = mock_sock
    
    with patch("app.core.converters.sharp_converter.SharpConverter._stop_daemon") as mock_stop:
        res = conv.convert("in.jpg", "out.webp", "webp", 80)
        
        assert not res["success"]
        # With max_retries = 3, it should try 3 times and stop 2 times
        assert mock_stop.call_count == 2

def test_sharp_batch_error_path_correlation(mock_daemon):
    """
    Verify that if a batch of files is processed and one of them fails,
    the error in the result is correctly correlated to the failing input path.
    """
    conv = SharpConverter()
    
    mock_sock = MagicMock()
    # Mock the daemon response for two files: first succeeds, second fails.
    resp1 = json.dumps({"success": True, "duration_ms": 50, "inputPath": "good.jpg"}).encode("utf-8") + b"\n"
    resp2 = json.dumps({"success": False, "error": "Sharp processing error", "inputPath": "bad.jpg"}).encode("utf-8") + b"\n"
    
    mock_sock.recv.return_value = resp1 + resp2
    mock_daemon["connect"].return_value = mock_sock
    
    res = conv.convert_batch(
        input_paths=["good.jpg", "bad.jpg"],
        output_dir="dummy_out",
        target_format="webp",
        qualities=[80, 80]
    )
    
    assert res["success_count"] == 1
    assert res["failure_count"] == 1
    assert len(res["errors"]) == 1
    assert res["errors"][0]["path"] == "bad.jpg"
    assert "Sharp processing error" in res["errors"][0]["error"]

def test_sharp_batch_error_path_correlation_fallback(mock_daemon):
    """
    Verify that if the daemon doesn't echo the inputPath, the converter
    still maps the errors correctly based on the send order.
    """
    conv = SharpConverter()
    
    mock_sock = MagicMock()
    # Mock responses with NO inputPath echoed.
    resp1 = json.dumps({"success": True, "duration_ms": 50}).encode("utf-8") + b"\n"
    resp2 = json.dumps({"success": False, "error": "Order fallback error"}).encode("utf-8") + b"\n"
    
    mock_sock.recv.return_value = resp1 + resp2
    mock_daemon["connect"].return_value = mock_sock
    
    res = conv.convert_batch(
        input_paths=["first_good.jpg", "second_bad.jpg"],
        output_dir="dummy_out",
        target_format="webp",
        qualities=[80, 80]
    )
    
    assert res["success_count"] == 1
    assert res["failure_count"] == 1
    assert len(res["errors"]) == 1
    assert res["errors"][0]["path"] == "second_bad.jpg"
    assert "Order fallback error" in res["errors"][0]["error"]

