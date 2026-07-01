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


from app.core.ffmpeg.process import FFmpegProcess
from app.core import process_registry as reg_mod


def test_ffmpeg_process_registers_while_alive(monkeypatch):
    reg_mod.clear()
    seen = {}
    real_register = reg_mod.register_process

    def _spy(proc):
        seen["registered"] = True
        seen["in_set_at_register"] = proc in reg_mod.snapshot() or True
        return real_register(proc)

    monkeypatch.setattr(reg_mod, "register_process", _spy)
    # ffmpeg binary need not exist for the spawn-registration assertion;
    # use a trivial cross-platform command via the python executable instead.
    import sys
    fp = FFmpegProcess(ffmpeg_path=sys.executable, args=["-c", "import time; time.sleep(0.2)"], wall_timeout_s=30.0)
    fp.spawn()
    assert seen.get("registered") is True
    fp._proc.wait(timeout=5)


