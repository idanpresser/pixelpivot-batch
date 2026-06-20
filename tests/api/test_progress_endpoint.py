# tests/api/test_progress_endpoint.py
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
from app.batch_api.main import app
from app.batch_api.routes import get_orchestrator

client = TestClient(app)

def test_progress_returns_state_and_sample():
    orch = MagicMock()
    orch.progress = {3: {"cells_done": 1, "cells_total": 4, "current_cell": "general/magick/webp", "ok": 5, "fail": 0, "started_at": 0.0}}
    app.dependency_overrides[get_orchestrator] = lambda: orch
    try:
        with patch("app.batch_api.routes.psutil") as ps:
            ps.cpu_percent.return_value = 42.0
            ps.virtual_memory.return_value = MagicMock(used=2 * 1024 * 1024 * 1024)
            r = client.get("/api/v1/batch/3/progress")
            assert r.status_code == 200
            data = r.json()
            assert data["cells_total"] == 4
            assert data["cpu_pct"] == 42.0
            assert data["ram_mb"] == 2048.0
    finally:
        app.dependency_overrides.clear()

def test_progress_404_when_not_live():
    orch = MagicMock(); orch.progress = {}
    app.dependency_overrides[get_orchestrator] = lambda: orch
    try:
        r = client.get("/api/v1/batch/77/progress")
        assert r.status_code == 404
    finally:
        app.dependency_overrides.clear()
