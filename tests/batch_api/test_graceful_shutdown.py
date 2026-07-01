# tests/batch_api/test_graceful_shutdown.py
from app.batch_api.queue_manager import BatchQueueManager


class _NoopOrch:
    def __init__(self):
        self.run_controls = {}


def test_queue_stop_accepts_grace_arg():
    qm = BatchQueueManager(_NoopOrch(), max_workers=1)
    qm.start()
    # Should accept an explicit grace window without raising.
    qm.stop(grace_s=0.5)
    assert qm._stopped is True


from app.batch_api.shutdown import graceful_shutdown
from app.core import process_registry as reg


class _Recorder:
    def __init__(self):
        self.calls = []


def test_graceful_shutdown_stops_both_lanes_then_terminates(monkeypatch):
    order = []

    class _HF:
        def stop(self):
            order.append("hotfolder_stop")

    class _QM:
        def stop(self, grace_s):
            order.append(("queue_stop", grace_s))

    terminated = {}

    def _fake_terminate_all(grace_s):
        order.append("terminate_all")
        terminated["grace"] = grace_s
        return 2

    monkeypatch.setattr(reg, "terminate_all", _fake_terminate_all)

    killed = graceful_shutdown(
        hot_folder_manager=_HF(),
        queue_manager=_QM(),
        grace_s=7.0,
        registry=reg,
    )

    # Hot folder must stop FIRST (no new batches), then queue drains, then kill survivors.
    assert order == ["hotfolder_stop", ("queue_stop", 7.0), "terminate_all"]
    assert killed == 2


def test_graceful_shutdown_tolerates_none_lanes(monkeypatch):
    monkeypatch.setattr(reg, "terminate_all", lambda grace_s: 0)
    # Must not raise when a lane was never initialized.
    assert graceful_shutdown(hot_folder_manager=None, queue_manager=None, grace_s=1.0, registry=reg) == 0


from fastapi.testclient import TestClient


def test_lifespan_shutdown_invokes_graceful_shutdown(monkeypatch):
    called = {}

    def _spy(hot_folder_manager, queue_manager, grace_s, **kw):
        called["grace_s"] = grace_s
        called["had_hf"] = hot_folder_manager is not None
        return 0

    monkeypatch.setattr("app.batch_api.main.graceful_shutdown", _spy)
    from app.batch_api.main import app
    with TestClient(app):
        pass  # entering+exiting the context triggers startup then shutdown
    assert "grace_s" in called
    assert called["had_hf"] is True


import subprocess
import sys
from app.batch_api.run_control import RunControl
from app.batch_api.shutdown import graceful_shutdown
from app.core import process_registry as reg


class _OrchWithRun:
    def __init__(self, run_id):
        self.run_controls = {run_id: RunControl()}


def test_mid_batch_sigterm_cancels_and_reaps(monkeypatch):
    reg.clear()
    run_id = 1
    orch = _OrchWithRun(run_id)

    # Simulate an in-flight chunk: a real child process registered as live.
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    reg.register_process(child)

    class _QM:
        def __init__(self, orch):
            self.orchestrator = orch
        def stop(self, grace_s):
            # Cooperative cancel of the in-flight run, mirroring the real stop().
            self.orchestrator.run_controls[run_id].cancel()

    class _HF:
        def stop(self):
            pass

    killed = graceful_shutdown(hot_folder_manager=_HF(), queue_manager=_QM(orch), grace_s=0.5)

    child.wait(timeout=5)
    assert orch.run_controls[run_id].cancelled is True   # batch was told to stop
    assert child.poll() is not None                       # no orphan child survives
    assert killed >= 1
    assert reg.snapshot() == set()                        # registry drained



