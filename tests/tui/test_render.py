# tests/tui/test_render.py
from app.tui.render import progress_table, tools_table, history_table

def test_progress_table_contains_counts():
    out = progress_table({"cells_done": 1, "cells_total": 4, "current_cell": "general/magick/webp",
                           "ok": 5, "fail": 0, "cpu_pct": 42.0, "ram_mb": 2048.0})
    assert "1/4" in out
    assert "general/magick/webp" in out
    assert "42" in out

def test_tools_table_renders_rows():
    from app.core.toolcheck import ToolStatus
    out = tools_table([ToolStatus("magick", True, "7.1", "/x"), ToolStatus("sharp", False, None, "down")])
    assert "magick" in out and "sharp" in out

def test_history_table_handles_empty():
    assert isinstance(history_table([]), str)
