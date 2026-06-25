import os
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient

def test_startup_exposed_no_opt_in():
    """Binding to 0.0.0.0 without PIXELPIVOT_ALLOW_PUBLIC env var should crash startup."""
    with patch.dict(os.environ, {"PIXELPIVOT_HOST": "0.0.0.0", "PIXELPIVOT_ALLOW_PUBLIC": "", "PIXELPIVOT_API_TOKEN": ""}):
        from app.batch_api.main import app
        with pytest.raises(RuntimeError) as exc_info:
            with TestClient(app):
                pass
        assert "PIXELPIVOT_ALLOW_PUBLIC=1" in str(exc_info.value)

def test_startup_exposed_no_token():
    """Binding to 0.0.0.0 with opt-in but without API token should crash startup."""
    with patch.dict(os.environ, {"PIXELPIVOT_HOST": "0.0.0.0", "PIXELPIVOT_ALLOW_PUBLIC": "1", "PIXELPIVOT_API_TOKEN": ""}):
        from app.batch_api.main import app
        with pytest.raises(RuntimeError) as exc_info:
            with TestClient(app):
                pass
        assert "PIXELPIVOT_API_TOKEN" in str(exc_info.value)

def test_startup_loopback_default():
    """Binding to loopback host should succeed without any environment variables."""
    with patch.dict(os.environ, {"PIXELPIVOT_HOST": "127.0.0.1", "PIXELPIVOT_ALLOW_PUBLIC": "", "PIXELPIVOT_API_TOKEN": ""}):
        from app.batch_api.main import app
        with TestClient(app) as client:
            response = client.get("/")
            assert response.status_code == 200

def test_mutating_routes_auth_enforced():
    """When PIXELPIVOT_API_TOKEN is set, mutating routes should return 401 without correct token."""
    with patch.dict(os.environ, {"PIXELPIVOT_HOST": "127.0.0.1", "PIXELPIVOT_API_TOKEN": "mysecrettoken"}):
        from app.batch_api.main import app
        with TestClient(app) as client:
            # 1. Mutating POST without token -> 401
            res = client.post("/api/v1/batch/start", json={})
            assert res.status_code == 401
            assert "Unauthorized" in res.json()["detail"]

            # 2. Mutating POST with invalid token -> 401
            res = client.post("/api/v1/batch/start", headers={"X-API-Token": "wrong"}, json={})
            assert res.status_code == 401

            # 3. GET route (safe) without token -> does not return 401
            res = client.get("/api/v1/batch/history")
            assert res.status_code != 401

            # 4. Mutating POST with correct token -> goes past auth layer (returns validation error 422)
            res = client.post("/api/v1/batch/start", headers={"X-API-Token": "mysecrettoken"}, json={})
            assert res.status_code == 422
