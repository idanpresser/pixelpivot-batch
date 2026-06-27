from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
from app.batch_api.main import app
from app.batch_api.routes import get_orchestrator
from app.batch_api.run_control import RunControl

client = TestClient(app)

def test_control_pause_sets_paused_and_status():
    orch = MagicMock()
    ctrl = RunControl()
    orch.run_controls = {5: ctrl}
    app.dependency_overrides[get_orchestrator] = lambda: orch
    try:
        with patch("app.batch_api.routes.get_connection") as gc, \
             patch("app.batch_api.routes.repo") as repo:
            gc.return_value.__enter__.return_value = MagicMock()
            r = client.post("/api/v1/batch/5/control", json={"action": "pause"})
            assert r.status_code == 200
            assert ctrl.paused is True
            repo.update_status.assert_called_with(repo.update_status.call_args[0][0], 5, "paused")
    finally:
        app.dependency_overrides.clear()

def test_control_unknown_run_404():
    orch = MagicMock(); orch.run_controls = {}
    app.dependency_overrides[get_orchestrator] = lambda: orch
    try:
        r = client.post("/api/v1/batch/999/control", json={"action": "stop"})
        assert r.status_code == 404
    finally:
        app.dependency_overrides.clear()

def test_restart_clones_config_and_queues():
    orch = MagicMock(); orch.run_controls = {}
    app.dependency_overrides[get_orchestrator] = lambda: orch
    fake_qm = MagicMock()
    try:
        with patch("app.batch_api.routes.get_connection") as gc, \
             patch("app.batch_api.routes.repo") as repo, \
             patch("app.batch_api.queue_manager.get_queue_manager", lambda: fake_qm):
            gc.return_value.__enter__.return_value = MagicMock()
            repo.get_run.return_value = {
                "id": 5, "source_dir": "/src", "target_dir": "/dst",
                "target_format": "webp,avif", "tool": "magick,ffmpeg",
            }
            repo.create_run.return_value = 6
            r = client.post("/api/v1/batch/5/restart")
            assert r.status_code == 200
            assert r.json()["run_id"] == 6
            # Restart submits the cloned run to the bounded queue (no inline exec).
            fake_qm.submit_batch.assert_called_once()
    finally:
        app.dependency_overrides.clear()
