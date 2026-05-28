import pytest
import os
from unittest.mock import MagicMock, patch
from app.core.converters.ffmpeg_nvenc_converter import FFmpegNvencConverter
from app.core.telemetry import TelemetryMonitor

def test_nvenc_args_generation():
    """
    Verify that FFmpegNvencConverter produces correct CLI args for AV1.
    """
    conv = FFmpegNvencConverter(ffmpeg_path="ffmpeg")
    
    with patch("app.core.converters.ffmpeg_nvenc_converter.FFmpegProcess") as mock_proc_cls:
        mock_proc = mock_proc_cls.return_value
        mock_proc.spawn.return_value = 1234
        # return success
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.progress_samples = []
        mock_proc.run.return_value = mock_result
        
        with patch("os.path.exists", return_value=True), \
             patch("os.path.getsize", return_value=1000):
            conv.convert("in.jpg", "out.avif", "avif", 30)
        
        args = mock_proc_cls.call_args[0][1] # second arg is args list
        
        assert "av1_nvenc" in args
        assert "-cq" in args
        # For quality=30, CQ = (100-30)/2 = 35
        assert args[args.index("-cq")+1] == "35"

def test_gpu_telemetry_capture_mocked():
    """
    Mock pynvml and verify TelemetryMonitor includes GPU usage.
    """
    with patch("app.core.telemetry.HAS_GPU", True), \
         patch("app.core.telemetry.nvmlDeviceGetHandleByIndex") as mock_handle, \
         patch("app.core.telemetry.nvmlDeviceGetUtilizationRates") as mock_util, \
         patch("app.core.telemetry.nvmlDeviceGetMemoryInfo") as mock_mem:
        
        mock_util.return_value.gpu = 45.0
        mock_mem.return_value.used = 1024 * 1024 * 500 # 500 MB
        
        monitor = TelemetryMonitor(pid=os.getpid(), interval_ms=10)
        monitor.start()
        import time
        time.sleep(0.05)
        summary = monitor.stop()
        
        assert summary["gpu_peak"] == 45.0
        assert summary["vram_peak_mb"] == 500.0

def test_nvenc_fatal_error_circuit_break():
    """
    Verify that "no capable devices" trips the circuit breaker.
    """
    conv = FFmpegNvencConverter(ffmpeg_path="ffmpeg")
    
    # Mock FFmpegProcess to return a fatal error
    mock_result = MagicMock()
    mock_result.success = False
    mock_result.fatal = True
    mock_result.error = "No capable devices found"
    mock_result.duration_ms = 100
    mock_result.progress_samples = []
    
    with patch("app.core.converters.ffmpeg_nvenc_converter.FFmpegProcess") as mock_proc_cls:
        mock_proc = mock_proc_cls.return_value
        mock_proc.spawn.return_value = 1234
        mock_proc.run.return_value = mock_result
        
        res = conv.convert("in.jpg", "out.avif", "avif", 30)
        
        assert not res["success"]
        assert conv.is_broken
        assert res["fatal_error"]
