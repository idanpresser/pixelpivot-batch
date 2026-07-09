"""Tests for UAC elevation helper on Windows."""
from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="elevation requires Windows")

from app.windows import elevation


def test_elevate_success():
    mock_handle = MagicMock()
    mock_handle.Close = MagicMock()
    
    with patch("os.path.exists", return_value=True), \
         patch("win32com.shell.shell.ShellExecuteEx", return_value={"hProcess": mock_handle}) as mock_execute, \
         patch("win32event.WaitForSingleObject", return_value=0) as mock_wait, \
         patch("win32process.GetExitCodeProcess", return_value=0) as mock_exit_code:
        
        # Should not raise any error
        elevation.elevate("C:/dummy.exe", "arg1", "arg2")
        
        mock_execute.assert_called_once()
        mock_wait.assert_called_once_with(mock_handle, 30000)
        mock_exit_code.assert_called_once_with(mock_handle)
        mock_handle.Close.assert_called_once()


def test_elevate_failure_exit_code():
    mock_handle = MagicMock()
    mock_handle.Close = MagicMock()
    
    with patch("os.path.exists", return_value=True), \
         patch("win32com.shell.shell.ShellExecuteEx", return_value={"hProcess": mock_handle}), \
         patch("win32event.WaitForSingleObject", return_value=0), \
         patch("win32process.GetExitCodeProcess", return_value=42):
        
        with pytest.raises(RuntimeError) as exc_info:
            elevation.elevate("C:/dummy.exe", "arg1")
        
        assert "exit code 42" in str(exc_info.value)
        mock_handle.Close.assert_called_once()


def test_elevate_timeout():
    mock_handle = MagicMock()
    mock_handle.Close = MagicMock()
    
    # 258 is WAIT_TIMEOUT
    with patch("os.path.exists", return_value=True), \
         patch("win32com.shell.shell.ShellExecuteEx", return_value={"hProcess": mock_handle}), \
         patch("win32event.WaitForSingleObject", return_value=258):
        
        with pytest.raises(RuntimeError) as exc_info:
            elevation.elevate("C:/dummy.exe", "arg1")
        
        assert "timed out" in str(exc_info.value)
        mock_handle.Close.assert_called_once()
