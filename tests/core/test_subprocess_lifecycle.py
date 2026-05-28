import os
import sys
import time
import subprocess
import pytest
import psutil
from unittest.mock import patch
from pathlib import Path
from app.core.ffmpeg.process import FFmpegProcess
from app.core.converters.base import BaseConverter

# A dummy converter to test _run_subprocess
class DummyConverter(BaseConverter):
    def get_name(self) -> str: return "dummy"
    def supported_formats(self) -> list[str]: return ["jpg"]
    def convert(self, *args, **kwargs): pass

@pytest.fixture
def slow_child_script(tmp_path):
    """Creates a script that spawns a child and waits."""
    child_script = tmp_path / "child.py"
    child_script.write_text("import time; time.sleep(10)")
    
    parent_script = tmp_path / "parent.py"
    parent_script.write_text(f"""
import subprocess
import sys
import time
# Spawn a child that will persist
subprocess.Popen([sys.executable, "{child_script.as_posix()}"])
time.sleep(10)
""")
    return parent_script

def test_orphan_cleanup_ffmpeg_process(tmp_path, slow_child_script):
    """
    Test that FFmpegProcess kills children of the process it spawns.
    """
    # We use python as a fake ffmpeg
    proc = FFmpegProcess(
        ffmpeg_path=sys.executable,
        args=[str(slow_child_script)],
        wall_timeout_s=1.0
    )
    
    pid = proc.spawn()
    parent = psutil.Process(pid)
    
    # Wait for parent to spawn child
    time.sleep(0.5)
    children = parent.children(recursive=True)
    assert len(children) >= 1
    child_pid = children[0].pid
    
    # Cancel and let it supervise
    proc.cancel("test")
    # run() will block until killed
    proc.run()
    
    # Verify both are dead
    assert not psutil.pid_exists(pid)
    assert not psutil.pid_exists(child_pid)

def test_orphan_cleanup_base_converter(tmp_path, slow_child_script):
    """
    Test that BaseConverter._run_subprocess kills children on timeout.
    """
    conv = DummyConverter()
    
    # We'll need to mock FFMPEG_TIMEOUT to be small
    with patch("app.core.converters.base.FFMPEG_TIMEOUT", 0.5):
        # We need to capture the child PID somehow. 
        # Since _run_subprocess is internal, we'll patch Popen to track it.
        
        real_popen = subprocess.Popen
        child_pids = []
        
        def mock_popen(*args, **kwargs):
            p = real_popen(*args, **kwargs)
            # In a real scenario, we don't know the children yet.
            # But for the test, we'll wait and find them.
            time.sleep(0.2)
            for c in psutil.Process(p.pid).children(recursive=True):
                child_pids.append(c.pid)
            return p
            
        with patch("subprocess.Popen", side_effect=mock_popen):
            # This should timeout
            res = conv._run_subprocess(
                cmd=[sys.executable, str(slow_child_script), str(tmp_path / "out.jpg")],
                tool_name="dummy",
                params=[],
                quality=75
            )
            
            assert not res["success"]
            # Check if "timed out" is in the error string
            err_msg = str(res.get("error", "")).lower()
            assert "timed out" in err_msg
            
            # Verify orphans are killed
            for pid in child_pids:
                assert not psutil.pid_exists(pid)

@pytest.mark.parametrize("escalation_s", [(0.1, 0.1)])
def test_graceful_termination_escalation(tmp_path, escalation_s):
    """
    Test that if a process ignores SIGTERM, it is eventually SIGKILLed.
    """
    # Script that ignores SIGTERM (KeyboardInterrupt in python)
    ignore_script = tmp_path / "ignore_term.py"
    ignore_script.write_text("""
import time
import signal
def handler(signum, frame):
    print("Ignoring signal")
signal.signal(signal.SIGTERM, handler)
while True:
    time.sleep(0.1)
""")
    
    with patch("app.core.ffmpeg.process.FFMPEG_CANCEL_ESCALATION_S", escalation_s):
        proc = FFmpegProcess(
            ffmpeg_path=sys.executable,
            args=[str(ignore_script)],
            wall_timeout_s=10.0 # Don't timeout via wall clock
        )
        
        pid = proc.spawn()
        proc.cancel("test")
        
        start = time.monotonic()
        proc.run()
        duration = time.monotonic() - start
        
        # Should have taken at least 0.2s (graceful + terminate wait)
        assert duration >= 0.2
        assert not psutil.pid_exists(pid)
