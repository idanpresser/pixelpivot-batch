# tests/tui/test_telemetry_screen.py
from unittest.mock import MagicMock
from app.tui.state import UiState
from app.tui.screens.telemetry import create_screen


def test_telemetry_screen_renders_progress():
    state = UiState(active_run_id=42)
    state.progress_cache = {
        "cells_done": 2, "cells_total": 5, "current_cell": "magick/webp",
        "ok": 2, "fail": 0, "cpu_pct": 25.5, "ram_mb": 512.0
    }
    
    api = MagicMock()
    screen = create_screen(state, api=api, supervisor=None)
    text = screen.container.content.text()
    
    assert "Progress" in text
    assert "2/5" in text
    assert "magick/webp" in text
    assert "pause" in text


def test_telemetry_screen_fallback_to_status():
    state = UiState(active_run_id=42)
    state.progress_cache = {}
    
    api = MagicMock()
    api.get_progress.side_effect = RuntimeError("404 Not Found")
    api.get_status.return_value = {
        "status": "completed",
        "savings_pct": 15.5,
        "cells_total": 10,
        "result": "success"
    }
    
    screen = create_screen(state, api=api, supervisor=None)
    text = screen.container.content.text()
    
    assert "Run 42: completed" in text
    assert "Saved: 15.5%" in text
    assert "Total cells: 10" in text
    assert "Result: success" in text

def test_telemetry_screen_finalized_cached_status():
    state = UiState(active_run_id=42, run_finalized=True)
    state.final_status = {
        "status": "completed",
        "savings_pct": 20.0,
        "cells_total": 8,
        "result": "success"
    }
    
    api = MagicMock()
    screen = create_screen(state, api=api, supervisor=None)
    text = screen.container.content.text()
    
    # Assertions on rendered output
    assert "Run 42: completed" in text
    assert "Saved: 20.0%" in text
    assert "Total cells: 8" in text
    assert "Result: success" in text
    
    # Verify no API calls were made because it's cached
    api.get_progress.assert_not_called()
    api.get_status.assert_not_called()
