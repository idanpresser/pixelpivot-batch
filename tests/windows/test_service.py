"""Tests for Windows service child process management and orphan prevention."""
from __future__ import annotations

import sys
import subprocess
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="app.windows.service requires Windows")

from app.windows.service import PixelPivotService


def test_monitor_until_stop_sets_stop_event_on_child_death():
    with patch("win32serviceutil.ServiceFramework.__init__", lambda self, args: None), \
         patch("win32event.CreateEvent", return_value=999), \
         patch("win32event.SetEvent") as mock_set_event:
        
        svc = PixelPivotService([])
        svc._stop_event = 999
        
        # Setup mock processes
        mock_proc1 = MagicMock(spec=subprocess.Popen)
        mock_proc1.poll.return_value = None  # alive
        mock_proc1.pid = 101
        
        mock_proc2 = MagicMock(spec=subprocess.Popen)
        mock_proc2.poll.return_value = 1  # dead!
        mock_proc2.pid = 102
        mock_proc2.returncode = 1
        
        svc._procs = [mock_proc1, mock_proc2]
        
        with patch("win32event.WaitForSingleObject", return_value=258), \
             patch("servicemanager.LogErrorMsg") as mock_log_error:
            
            svc._monitor_until_stop()
            
            # Assertions
            mock_set_event.assert_called_with(999)
            mock_log_error.assert_called_once()


def test_svc_stop_only_sets_stop_event():
    with patch("win32serviceutil.ServiceFramework.__init__", lambda self, args: None), \
         patch("win32event.CreateEvent", return_value=999), \
         patch("win32event.SetEvent") as mock_set_event:
        
        svc = PixelPivotService([])
        svc._stop_event = 999
        svc.ReportServiceStatus = MagicMock()
        
        # Setup mock processes
        mock_proc1 = MagicMock(spec=subprocess.Popen)
        svc._procs = [mock_proc1]
        
        svc.SvcStop()
        
        # SvcStop should only set stop event and not terminate children or clear list
        mock_set_event.assert_called_with(999)
        assert len(svc._procs) == 1


def test_svc_do_run_lifecycle():
    with patch("win32serviceutil.ServiceFramework.__init__", lambda self, args: None), \
         patch("win32event.CreateEvent", return_value=999), \
         patch("servicemanager.LogMsg"):
        
        svc = PixelPivotService([])
        svc._start_children = MagicMock()
        svc._monitor_until_stop = MagicMock()
        svc._terminate_children = MagicMock()
        
        svc.SvcDoRun()
        
        svc._start_children.assert_called_once()
        svc._monitor_until_stop.assert_called_once()
        svc._terminate_children.assert_called_once()
