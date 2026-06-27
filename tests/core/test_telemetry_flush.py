import pytest
import sqlite3
import time
from unittest.mock import MagicMock, patch
from app.core.telemetry import TelemetryMonitor
from app.core.db.schema import init_db
from app.core.db.repositories.batch import BatchRepository

def test_telemetry_real_flush_to_db():
    """
    Verify that TelemetryMonitor actually flushes samples to a real SQLite DB
    when run_id is provided.
    """
    # Create an in-memory DB
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    init_db(conn)
    
    # Disable foreign keys for simplicity in this specific test
    conn.execute("PRAGMA foreign_keys=OFF")
    
    # Mock get_connection to return our in-memory DB
    with patch("app.core.db.get_connection") as mock_get_conn:
        class MockCM:
            def __enter__(self): return conn
            def __exit__(self, *args): pass
            
        mock_get_conn.return_value = MockCM()
        
        # Start monitor with run_id
        monitor = TelemetryMonitor(run_id=1, interval_ms=10)
        monitor.start()
        
        # Wait for a few samples to be produced and flushed
        # Produced every 10ms. Flushed every 20 samples = 200ms.
        time.sleep(0.5)
        
        monitor.stop()
        
        # Check DB. Task 020 moved per-tick samples from the legacy
        # pipeline_telemetry table to batch_telemetry (keyed on
        # batch_runs.id) so the FK does not collide with the dead pipeline
        # path.
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM batch_telemetry WHERE run_id = 1")
        count = cur.fetchone()[0]

        assert count > 0


def test_telemetry_fast_conversion_optimisation(monkeypatch):
    """
    Verify that TelemetryMonitor skips the heavy _get_recursive_resources calls
    during the first 200ms of self.is_running to optimize CPU for fast-running
    conversions, but still takes a final sample on stop().
    """
    import time
    
    current_time = 1000.0
    def mock_monotonic():
        return current_time
    monkeypatch.setattr(time, "monotonic", mock_monotonic)
    
    monitor = TelemetryMonitor(run_id=1, interval_ms=10)
    
    with patch.object(monitor, "_get_recursive_resources", return_value=(10.0, 50.0)) as mock_get_res:

        monitor.start()
        
        current_time = 1000.05
        # Sleep real time to let the background thread thread loop
        time.sleep(0.08)
        
        # Even though real time has passed, mocked time says only 50ms has passed (< 200ms)
        assert mock_get_res.call_count == 0
        
        # Stop should trigger the final sample and make exactly 1 call
        stats = monitor.stop()
        assert mock_get_res.call_count == 1
        assert stats["cpu_peak"] == 10.0
        assert stats["ram_peak"] == 50.0
