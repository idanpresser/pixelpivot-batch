
import os
import sys
import time
import json
import sqlite3
import queue
import subprocess
from pathlib import Path
import pytest
from unittest.mock import patch, MagicMock

from app.core.paths import DATABASE_URL, PROJ_ROOT
from app.core.db.connection import get_connection
from app.core.converters.ffmpeg_converter import FFmpegConverter
from app.core.converters.magick_converter import MagickConverter
from app.core.converters.vips_converter import VipsConverter
from app.core.converters.sharp_converter import SharpConverter
from app.core.telemetry import TelemetryMonitor
from app.core.ffmpeg.process import FFmpegProcess
from app.core.utils import probe_image_dimensions, ensure_vips_dlls

# --- Phase 1: Environment & DLL Integrity ---

def test_sqlite_path_resolution():
    """Verify DATABASE_URL is absolute and points to the data directory."""
    assert DATABASE_URL.startswith("sqlite:///")
    db_path = DATABASE_URL.replace("sqlite:///", "")
    assert Path(db_path).is_absolute()
    assert "data" in db_path

def test_vips_dll_logic_windows():
    """Test the logic of ensure_vips_dlls without actually loading DLLs."""
    if sys.platform != "win32":
        pytest.skip("Windows only test")
    
    mock_proj_root = Path("C:/mock_proj")
    mock_vips_bin = mock_proj_root / "bin" / "vips" / "bin"
    
    with patch("app.core.paths.PROJ_ROOT", mock_proj_root), \
         patch("pathlib.Path.exists", return_value=True), \
         patch("pathlib.Path.glob", side_effect=lambda p: [mock_proj_root / "bin" / "vips"] if "vips" in p else []), \
         patch("os.add_dll_directory") as mock_add_dll, \
         patch.dict(os.environ, {"PATH": "C:/some/other/path"}):
        
        ensure_vips_dlls()
        assert str(mock_vips_bin.absolute()) in os.environ["PATH"]
        mock_add_dll.assert_called_once()

# --- Phase 2: Process Lifecycles & Windows Subprocesses ---

def test_ffmpeg_convert_batch_signature():
    """Verify that FFmpegConverter.convert_batch doesn't crash due to signature mismatch."""
    conv = FFmpegConverter(ffmpeg_path="ffmpeg")
    with patch("app.core.converters.ffmpeg_converter.group_by_dimensions") as mock_group:
        mock_group.return_value = { (100, 100): ["test.png"] }
        with patch.object(conv, "_run_multimap_path", return_value=(1, 0, [], {})):
            with patch("app.core.converters.ffmpeg_converter.encoder_params_for", return_value=["-some-param"]):
                res = conv.convert_batch(
                    input_paths=["test.png"],
                    output_dir="out",
                    target_format="webp",
                    qualities=[80.0]
                )
                args, kwargs = mock_group.call_args
                assert "dimensions" not in kwargs

def test_subprocess_creation_flags():
    """Verify that all relevant subprocess calls use CREATE_NO_WINDOW on Windows."""
    if sys.platform != "win32":
        pytest.skip("Windows only test")
        
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout='{"streams":[{"width":100,"height":100}]}')
        probe_image_dimensions("dummy.png")
        _, kwargs = mock_run.call_args
        assert kwargs.get("creationflags") == subprocess.CREATE_NO_WINDOW

# --- Phase 3: Telemetry & Memory Safety ---

def test_telemetry_queue_bounds():
    """Verify that TelemetryMonitor queue is bounded."""
    monitor = TelemetryMonitor(run_id=123)
    assert monitor._queue.maxsize == 2000

def test_ffmpeg_process_samples_limit():
    """Verify that FFmpegProcess samples list is bounded."""
    proc = FFmpegProcess("ffmpeg", ["-i", "in", "out"], wall_timeout_s=10)
    assert proc._samples.maxlen == 1000

# --- Phase 4: Database WAL Mode & Socket Hardening ---

def test_sqlite_connection_pragmas():
    """Verify that SQLite connections have the correct pragmas."""
    with get_connection() as conn:
        res = conn.execute("PRAGMA busy_timeout").fetchone()
        assert res[0] == 5000
        res = conn.execute("PRAGMA synchronous").fetchone()
        assert res[0] == 1

def test_sharp_daemon_jxl_precision():
    """Verify that SharpConverter preserves float quality for JXL."""
    conv = SharpConverter()
    with patch.object(conv, "_ensure_daemon_running"), \
         patch.object(conv, "_get_connection") as mock_conn:
        mock_sock = MagicMock()
        mock_conn.return_value = mock_sock
        mock_sock.recv.return_value = b'{"success": true}\n'
        conv.convert("in.png", "out.jxl", "jxl", 0.5)
        sent_data = mock_sock.sendall.call_args[0][0].decode('utf-8')
        request = json.loads(sent_data.strip())
        assert isinstance(request["quality"], float)
        assert request["quality"] == 0.5

def test_vips_run_id_telemetry():
    """Verify VipsConverter correctly passes run_id for telemetry."""
    conv = VipsConverter()
    with patch.object(conv, "_run_library") as mock_run:
        conv.convert("in.png", "out.webp", "webp", 80, run_id=123)
        args, kwargs = mock_run.call_args
        assert kwargs.get("run_id") == 123
