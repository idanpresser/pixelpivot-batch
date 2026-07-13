# tests/batch_api/test_calibration_flag_scope.py
"""Guard for bd-qk1.2: CALIBRATION_ENABLED must be scoped to the calibration
run only. A worker that runs a calibration job must restore the prior value so
subsequent normal batches do not silently record calibration/analytics rows."""
import time

import pytest

from app.core.db.connection import get_connection
from app.core.db.repositories.batch import BatchRepository
from app.batch_api import queue_manager
from app.batch_api.queue_manager import BatchQueueManager
from app.core import config


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    db_path = tmp_path / "calibration_flag_scope.db"
    import app.core.db.connection as connection
    monkeypatch.setenv("PIXELPIVOT_DB_PATH", str(db_path))
    connection.reset_engine_cache()
    from app.core.db.schema import init_db
    init_db()


class _Orch:
    def __init__(self):
        self.run_controls = {}


def _enqueue_calibration():
    repo = BatchRepository()
    with get_connection() as conn:
        return repo.create_run(
            conn, source_dir="s", target_dir="t", target_format="webp",
            tool="vips", trigger_type="calibration", status="queued", priority=100,
        )


def test_calibration_flag_restored_to_prior_value_after_worker_run(monkeypatch):
    # Prior process-global value is False (the default); it must remain False
    # after the worker processes a calibration job.
    monkeypatch.setattr(config, "CALIBRATION_ENABLED", False)

    seen = {}

    def _fake_run_calibration(*args, **kwargs):
        seen["enabled_during"] = config.CALIBRATION_ENABLED
        return {"failures": 0}

    monkeypatch.setattr(queue_manager, "run_calibration", _fake_run_calibration, raising=False)
    # calibration_runner.run_calibration is imported lazily inside the worker loop.
    from app.batch_api import calibration_runner
    monkeypatch.setattr(calibration_runner, "run_calibration", _fake_run_calibration)

    _enqueue_calibration()
    qm = BatchQueueManager(_Orch(), max_workers=1)
    qm.start()
    deadline = time.time() + 10
    while "enabled_during" not in seen and time.time() < deadline:
        time.sleep(0.05)
    qm.stop(grace_s=2.0)

    assert seen.get("enabled_during") is True, "flag must be enabled while calibration runs"
    assert config.CALIBRATION_ENABLED is False, "flag must be restored to prior value after run"
