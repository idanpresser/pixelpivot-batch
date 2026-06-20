import pytest
import os
import time
from unittest.mock import MagicMock, patch
from app.core.converters.vips_converter import VipsConverter
from app.core.telemetry import TelemetryMonitor

@pytest.mark.skipif(os.name != 'nt', reason="Vips tests often need specific setup on Linux")
def test_vips_telemetry_capture(tmp_path):
    """
    Verify that VipsConverter captures telemetry for in-process calls.
    """
    # We need a real image or a very good mock of pyvips
    conv = VipsConverter()
    
    # Mock pyvips (loaded lazily via get_pyvips()) to simulate memory usage.
    with patch("app.core.converters.vips_converter.get_pyvips") as mock_get_vips:
        mock_vips = mock_get_vips.return_value
        mock_img = mock_vips.Image.new_from_file.return_value
        
        # We need to mock psutil to return some memory usage
        mock_mem = MagicMock()
        mock_mem.rss = 100 * 1024 * 1024 # 100 MB
        
        with patch("psutil.Process") as mock_proc_cls:
            mock_proc = mock_proc_cls.return_value
            mock_proc.memory_info.return_value = mock_mem
            mock_proc.cpu_percent.return_value = 10.0
            mock_proc.children.return_value = []
            
            res = conv.convert("in.jpg", "out.webp", "webp", 80)
            
            assert res["success"]
            tele = res["telemetry"]
            assert tele["ram_peak"] > 0
            # Since we mocked 100MB, it should be around 100
            assert 90 <= tele["ram_peak"] <= 110
def test_telemetry_monitor_current_process_defaults():
    """
    Verify TelemetryMonitor defaults to current PID and captures it.
    """
    monitor = TelemetryMonitor(interval_ms=10)
    assert monitor.target_pid is None # It defaults to os.getpid() lazily in _sample or stays None

    monitor.start()
    time.sleep(0.05)
    summary = monitor.stop()

    assert summary["ram_peak"] > 0

