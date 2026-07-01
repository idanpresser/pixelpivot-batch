# tests/batch_api/test_priority_queue.py
import time
from app.core.db.connection import get_connection
from app.core.db.schema import init_db
from app.core.db.repositories.batch import BatchRepository
from app.batch_api.queue_manager import BatchQueueManager


class _RecordingOrch:
    def __init__(self):
        self.run_controls = {}
        self.executed = []
    def execute_batch(self, run_id, request):
        self.executed.append(run_id)


def _enqueue(priority):
    repo = BatchRepository()
    with get_connection() as conn:
        return repo.create_run(conn, source_dir="s", target_dir="t", target_format="webp",
                               tool="ffmpeg", trigger_type="api", status="queued", priority=priority)


def test_worker_executes_high_priority_before_low():
    init_db()
    orch = _RecordingOrch()
    # Drain leftovers so ordering is deterministic.
    BatchRepository()  # noqa: ensure import side effects
    low = _enqueue(0)
    high = _enqueue(100)
    qm = BatchQueueManager(orch, max_workers=1)
    qm.start()
    deadline = time.time() + 10
    while len(orch.executed) < 2 and time.time() < deadline:
        time.sleep(0.05)
    qm.stop(grace_s=2.0)
    # High-priority row ran before the low-priority one.
    assert orch.executed.index(high) < orch.executed.index(low)


def test_submit_batch_sets_queued_status():
    init_db()
    orch = _RecordingOrch()
    repo = BatchRepository()
    with get_connection() as conn:
        rid = repo.create_run(conn, source_dir="s", target_dir="t", target_format="webp",
                               tool="ffmpeg", trigger_type="api", status="running")
    qm = BatchQueueManager(orch, max_workers=1)
    from app.batch_api.models import BatchRequest, Tool
    req = BatchRequest(source_dir="s", target_dir="t", target_format=["webp"],
                       tool=[Tool.ffmpeg], category=["general"], trigger_type="api")
    qm.submit_batch(rid, req)
    with get_connection() as conn:
        assert repo.get_run(conn, rid)["status"] == "queued"


def test_api_submit_is_high_priority(monkeypatch):
    from app.core.db.schema import init_db
    from app.core.db.connection import get_connection
    from app.core.db.repositories.batch import BatchRepository
    from app.core.config import PRIORITY_HIGH
    init_db()
    seen = {}
    repo = BatchRepository()
    orig = repo.create_run

    def _spy(conn, **kw):
        seen["priority"] = kw.get("priority")
        return orig(conn, **kw)

    monkeypatch.setattr("app.batch_api.routes.repo.create_run", _spy)
    from fastapi.testclient import TestClient
    from app.batch_api.main import app
    with TestClient(app) as client:
        client.post("/api/v1/batch/start", json={
            "source_dir": "s", "target_dir": "t", "target_format": ["webp"],
            "tool": ["ffmpeg"], "category": ["general"], "trigger_type": "api",
        })
    assert seen.get("priority") == PRIORITY_HIGH

