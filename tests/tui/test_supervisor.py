# tests/tui/test_supervisor.py
import sys
import time
from app.tui.supervisor import ProcessSupervisor

def test_start_capture_stop_dummy_child():
    sup = ProcessSupervisor()
    # A child that prints one line then sleeps so we can observe capture + stop.
    cmd = [sys.executable, "-u", "-c",
           "import time; print('HELLO_CHILD', flush=True); time.sleep(30)"]
    sup.start("api", cmd)
    deadline = time.time() + 5
    while time.time() < deadline and not any("HELLO_CHILD" in l for l in sup.get_logs()):
        time.sleep(0.05)
    assert any("HELLO_CHILD" in l for l in sup.get_logs())
    assert sup.status()["api"] == "running"
    sup.stop("api")
    assert sup.status()["api"] == "stopped"

def test_restart_replaces_process():
    sup = ProcessSupervisor()
    cmd = [sys.executable, "-u", "-c", "import time; time.sleep(30)"]
    sup.start("api", cmd)
    first = sup._procs["api"].pid
    sup.restart("api", cmd)
    assert sup._procs["api"].pid != first
    sup.stop("api")
