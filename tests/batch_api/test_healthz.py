# tests/batch_api/test_healthz.py
from fastapi.testclient import TestClient
from app.batch_api.main import app


def test_healthz_live_returns_200_alive():
    with TestClient(app) as client:
        resp = client.get("/healthz/live")
    assert resp.status_code == 200
    assert resp.json() == {"status": "alive"}
