# tests/batch_api/test_calibrate_route.py
from unittest.mock import MagicMock
from fastapi.testclient import TestClient


def test_calibrate_route_queues_run(monkeypatch, tmp_path):
    from app.batch_api import routes

    fake_qm = MagicMock()
    monkeypatch.setattr("app.batch_api.queue_manager.get_queue_manager", lambda: fake_qm)
    monkeypatch.setattr(routes.repo, "create_run", lambda *a, **k: 4242)

    from fastapi import FastAPI
    app = FastAPI()
    app.state.orchestrator = MagicMock(interpolator=MagicMock(version="t"))
    app.include_router(routes.router, prefix="/api/v1")
    client = TestClient(app)

    resp = client.post("/api/v1/calibrate", json={
        "source_dir": str(tmp_path), "target_format": ["webp"], "tool": ["vips"],
    })
    assert resp.status_code == 200
    assert resp.json() == {"run_id": 4242, "status": "queued"}
    fake_qm.submit_calibration.assert_called_once()
