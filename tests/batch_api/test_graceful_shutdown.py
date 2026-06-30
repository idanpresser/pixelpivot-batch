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
