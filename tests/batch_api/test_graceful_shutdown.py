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

