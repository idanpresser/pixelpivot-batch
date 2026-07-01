# tests/batch_api/test_disk_backpressure.py
from app.batch_api.image_guards import disk_pct_over_threshold
from app.batch_api import image_guards
import collections
import time
from app.batch_api.queue_manager import BatchQueueManager


def test_over_threshold_true_when_full(monkeypatch):
    Usage = collections.namedtuple("Usage", "total used free")
    # 95% used
    monkeypatch.setattr(image_guards.shutil, "disk_usage", lambda p: Usage(100, 95, 5))
    assert disk_pct_over_threshold("/some/target", 90.0) is True


def test_under_threshold_false(monkeypatch):
    Usage = collections.namedtuple("Usage", "total used free")
    monkeypatch.setattr(image_guards.shutil, "disk_usage", lambda p: Usage(100, 50, 50))
    assert disk_pct_over_threshold("/some/target", 90.0) is False


def test_probes_resolved_target_not_root(monkeypatch):
    seen = {}
    Usage = collections.namedtuple("Usage", "total used free")
    def _fake(path):
        seen["path"] = path
        return Usage(100, 10, 90)
    monkeypatch.setattr(image_guards.shutil, "disk_usage", _fake)
    disk_pct_over_threshold("D:/mount/out", 90.0)
    import os
    assert seen["path"] == os.path.abspath("D:/mount/out")


class _Orch:
    def __init__(self):
        self.run_controls = {}


def test_worker_pauses_until_disk_frees(monkeypatch):
    from app.batch_api import queue_manager as qm_mod
    states = iter([True, True, False])  # over, over, then freed
    monkeypatch.setattr(qm_mod, "DISK_BACKPRESSURE_POLL_S", 0.01)
    monkeypatch.setattr("app.batch_api.image_guards.disk_pct_over_threshold",
                        lambda target, pct: next(states, False))
    qm = BatchQueueManager(_Orch(), max_workers=1)
    start = time.time()
    qm._disk_backpressure_wait("D:/out")  # returns once the iterator yields False
    assert time.time() - start >= 0.02  # waited at least two poll cycles
