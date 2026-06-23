import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, patch
from app.batch_api.main import app
from app.batch_api.routes import get_orchestrator

client = TestClient(app)

def test_start_batch():
    payload = {
        "source_dir": "/src",
        "target_dir": "/dst",
        "target_format": ["webp"],
        "tool": ["magick"],
        "category": ["general"]
    }
    
    mock_orchestrator = MagicMock()
    app.dependency_overrides[get_orchestrator] = lambda: mock_orchestrator
    
    # Mock the repository and DB connection
    try:
        with patch("app.batch_api.routes.get_connection") as mock_get_conn:
            mock_get_conn.return_value.__enter__.return_value = MagicMock()
            
            with patch("app.batch_api.routes.repo") as mock_repo:
                mock_repo.create_run.return_value = 123
                
                response = client.post("/api/v1/batch/start", json=payload)
                
                assert response.status_code == 200
                assert response.json()["run_id"] == 123
                assert response.json()["status"] == "queued"
                
                mock_repo.create_run.assert_called_once()
                mock_orchestrator.execute_batch.assert_called_once()
    finally:
        app.dependency_overrides.clear()

def test_get_batch_status():
    mock_orchestrator = MagicMock()
    mock_orchestrator.progress = {}
    app.dependency_overrides[get_orchestrator] = lambda: mock_orchestrator
    try:
        with patch("app.batch_api.routes.get_connection") as mock_get_conn:
            mock_get_conn.return_value.__enter__.return_value = MagicMock()
            
            with patch("app.batch_api.routes.repo") as mock_repo:
                mock_repo.get_run.return_value = {
                    "id": 123,
                    "status": "completed",
                    "total_images": 10,
                    "created_at": "2026-05-14T12:00:00",
                    "completed_at": "2026-05-14T12:05:00"
                }
                mock_repo.get_summary.return_value = {"duration_ms": 300000}
                
                response = client.get("/api/v1/batch/status/123")
                
                assert response.status_code == 200
                data = response.json()
                assert data["run_id"] == 123
                assert data["status"] == "completed"
                assert data["summary"]["duration_ms"] == 300000
    finally:
        app.dependency_overrides.clear()

def test_get_batch_status_in_flight():
    mock_orchestrator = MagicMock()
    mock_orchestrator.progress = {
        123: {
            "cells_done": 3,
            "cells_total": 10,
            "current_cell": "general/magick/webp",
            "ok": 2,
            "fail": 1,
            "started_at": 1000.0,
        }
    }
    app.dependency_overrides[get_orchestrator] = lambda: mock_orchestrator
    try:
        with patch("app.batch_api.routes.get_connection") as mock_get_conn:
            mock_get_conn.return_value.__enter__.return_value = MagicMock()
            
            with patch("app.batch_api.routes.repo") as mock_repo:
                mock_repo.get_run.return_value = {
                    "id": 123,
                    "status": "running",
                    "total_images": 10,
                    "created_at": "2026-05-14T12:00:00",
                    "completed_at": None
                }
                
                response = client.get("/api/v1/batch/status/123")
                
                assert response.status_code == 200
                data = response.json()
                assert data["run_id"] == 123
                assert data["status"] == "running"
                assert data["cells_done"] == 3
                assert data["cells_total"] == 10
                assert data["current_cell"] == "general/magick/webp"
                assert data["ok"] == 2
                assert data["fail"] == 1
    finally:
        app.dependency_overrides.clear()

