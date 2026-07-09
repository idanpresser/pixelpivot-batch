"""Tests for LogWindow functionality in tray UI."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="tray is win32-only")

pytest.importorskip("pytestqt")

from app.windows.tray import LogWindow


def test_log_window_refresh_appends_and_handles_truncation(qtbot, tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    
    log_file = log_dir / "service.log"
    log_file.write_text("line 1\nline 2\n", encoding="utf-8")
    
    dlg = LogWindow(log_dir)
    qtbot.addWidget(dlg)
    
    # Trigger initial refresh
    dlg._refresh()
    
    # Text box should show the content
    text = dlg._text.toPlainText()
    assert "line 1" in text
    assert "line 2" in text
    assert dlg._pos.get("service.log", 0) > 0
    
    # Append more text
    with open(log_file, "a", encoding="utf-8") as fh:
        fh.write("line 3\n")
        
    dlg._refresh()
    assert "line 3" in dlg._text.toPlainText()
    
    # Now truncate/rotate the log file (size < pos)
    log_file.write_text("new start\n", encoding="utf-8")
    
    dlg._refresh()
    # The text box should be cleared and show only "new start"
    text = dlg._text.toPlainText()
    assert "line 1" not in text
    assert "new start" in text
    
    dlg.close()


def test_log_window_populates_new_logs(qtbot, tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    
    log_file1 = log_dir / "service1.log"
    log_file1.write_text("log 1", encoding="utf-8")
    
    dlg = LogWindow(log_dir)
    qtbot.addWidget(dlg)
    
    # Initially combo box should contain service1.log
    items = [dlg._combo.itemText(i) for i in range(dlg._combo.count())]
    assert "service1.log" in items
    
    # Create service2.log
    log_file2 = log_dir / "service2.log"
    log_file2.write_text("log 2", encoding="utf-8")
    
    # Trigger refresh, which should populate combo box
    dlg._refresh()
    
    items = [dlg._combo.itemText(i) for i in range(dlg._combo.count())]
    assert "service2.log" in items
    
    dlg.close()


def test_log_window_no_spurious_newlines(qtbot, tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    
    log_file = log_dir / "service.log"
    log_file.write_text("line1", encoding="utf-8")
    
    dlg = LogWindow(log_dir)
    qtbot.addWidget(dlg)
    
    dlg._refresh()
    assert dlg._text.toPlainText() == "line1"
    
    # Append the rest of the line
    with open(log_file, "a", encoding="utf-8") as fh:
        fh.write("line2")
        
    dlg._refresh()
    # There should be NO newline between line1 and line2
    assert dlg._text.toPlainText() == "line1line2"
    
    dlg.close()
