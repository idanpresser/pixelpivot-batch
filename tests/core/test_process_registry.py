# tests/core/test_process_registry.py
import subprocess
import sys

from app.core import process_registry as reg


def _spawn_sleeper(seconds: float):
    return subprocess.Popen([sys.executable, "-c", f"import time; time.sleep({seconds})"])


def test_register_then_unregister_tracks_live_set():
    reg.clear()
    p = _spawn_sleeper(5)
    try:
        reg.register_process(p)
        assert p in reg.snapshot()
        reg.unregister_process(p)
        assert p not in reg.snapshot()
    finally:
        p.kill()
        p.wait(timeout=5)


def test_terminate_all_kills_survivors_and_returns_count():
    reg.clear()
    p = _spawn_sleeper(30)
    reg.register_process(p)
    killed = reg.terminate_all(grace_s=0.2)
    p.wait(timeout=5)
    assert p.poll() is not None
    assert killed >= 1
    assert reg.snapshot() == set()


def test_terminate_all_ignores_already_exited():
    reg.clear()
    p = _spawn_sleeper(0.01)
    p.wait(timeout=5)
    reg.register_process(p)
    killed = reg.terminate_all(grace_s=0.2)
    assert killed == 0
