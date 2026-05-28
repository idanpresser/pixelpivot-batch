
import time
import queue
import pytest
from unittest.mock import patch, MagicMock
from app.core.telemetry import TelemetryMonitor
from app.core.ffmpeg.process import FFmpegProcess

def test_telemetry_queue_overflow():
    """Verify that TelemetryMonitor queue is bounded and handles overflow."""
    # We want to test that it's maxsize=2000
    monitor = TelemetryMonitor(run_id=123)
    assert monitor._queue.maxsize == 2000
    
    # Fill the queue
    for i in range(2000):
        monitor._queue.put(i)
        
    assert monitor._queue.full()
    
    # Try to add one more - it should log a warning and not raise exception
    with patch("app.core.telemetry.log.warning") as mock_log:
        # We need to mock _get_recursive_resources to avoid actual psutil calls
        with patch.object(monitor, "_get_recursive_resources", return_value=(0.0, 0.0)):
            monitor._sample()
            mock_log.assert_called_with("Telemetry queue is full! Dropping oldest sample to prevent memory growth.")

def test_ffmpeg_process_samples_limit():
    """Verify that FFmpegProcess samples list is bounded."""
    proc = FFmpegProcess("ffmpeg", ["-i", "test.png", "out.webp"], wall_timeout_s=10)
    assert proc._samples.maxlen == 1000
    
    # Mock progress sample
    sample = MagicMock()
    for i in range(1100):
        proc._samples.append(sample)
        
    assert len(proc._samples) == 1000
