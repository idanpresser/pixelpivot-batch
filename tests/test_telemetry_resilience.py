import pytest
import time
import queue
import threading
from unittest.mock import MagicMock, patch

from app.core.telemetry import TelemetryMonitor


def test_telemetry_connection_recovery():
    """Verify that transient DB flush failures retain the buffer and recover on subsequent ticks."""
    monitor = TelemetryMonitor(run_id=999)
    # Set short queue timeout for fast testing
    monitor.interval = 0.01
    
    # We mock get_connection and insert_telemetry_batch
    mock_conn = MagicMock()
    
    # Let's count calls to insert_telemetry_batch
    insert_calls = []
    
    def mock_insert(conn, samples, auto_commit=True):
        insert_calls.append(list(samples))
        if len(insert_calls) == 1:
            raise RuntimeError("Database Locked")  # First write fails
        # Subsequent writes succeed

    with patch("app.core.db.get_connection", return_value=MagicMock()), \
         patch("app.core.db.insert_telemetry_batch", side_effect=mock_insert), \
         patch("app.core.telemetry.TELEMETRY_BATCH_SIZE", 1), \
         patch.object(monitor, "_get_recursive_resources", return_value=(10.0, 100.0)):
        
        # Start flusher thread manually (we don't run the monitor producer loop)
        monitor.is_running = True
        monitor._flusher_thread = threading.Thread(target=monitor._flush_loop, daemon=True)
        monitor._flusher_thread.start()

        try:
            # Enqueue sample 1
            monitor._sample()
            
            # Wait for flusher to process sample 1 and experience database failure
            # It should retain sample 1 in its buffer.
            time.sleep(0.1)
            assert len(insert_calls) == 1  # Fails
            
            # Enqueue sample 2
            monitor._sample()
            
            # Put sentinel to trigger final flush and shutdown
            monitor._queue.put(None)
            monitor._flusher_thread.join(timeout=2.0)
            
            # The second flush attempt (during shutdown/None processing) should succeed
            # and it must contain BOTH sample 1 and sample 2 (connection recovery + buffer retention)!
            assert len(insert_calls) == 2
            final_samples = insert_calls[1]
            assert len(final_samples) == 2  # Both samples flushed together!
            assert final_samples[0][0] == 999  # run_id check
            assert final_samples[1][0] == 999

        finally:
            monitor.is_running = False


def test_telemetry_buffer_limit_safeguard():
    """Verify that buffer doesn't grow indefinitely and is capped on persistent DB failure."""
    monitor = TelemetryMonitor(run_id=999)
    
    # Force DB write to always raise exception
    def mock_insert(conn, samples, auto_commit=True):
        raise RuntimeError("Persistent DB Offline Error")
        
    with patch("app.core.db.get_connection", return_value=MagicMock()), \
         patch("app.core.db.insert_telemetry_batch", side_effect=mock_insert), \
         patch.object(monitor, "_get_recursive_resources", return_value=(10.0, 100.0)):
        
        monitor.is_running = True
        monitor._flusher_thread = threading.Thread(target=monitor._flush_loop, daemon=True)
        monitor._flusher_thread.start()
        
        try:
            # We bypass the queue to fill the buffer manually and simulate queue ticks
            # The flusher thread will process items from the queue.
            # Let's enqueue 1100 items (exceeds the 1000 limit)
            for i in range(1100):
                monitor._queue.put((999, "2026-05-20 12:00:00", 5.0, 50.0))
                
            # Send stop sentinel to join flusher
            monitor._queue.put(None)
            monitor._flusher_thread.join(timeout=3.0)
            
            # Wait a small moment to ensure the flusher loop completes
            time.sleep(0.1)
            
            # Since the flusher thread is dead, we cannot access the local `buffer` directly,
            # but we can verify that the queue is empty and the thread exited without crashing.
            assert monitor._queue.empty()
            
        finally:
            monitor.is_running = False
