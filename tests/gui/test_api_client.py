import httpx
import pytest
import respx
from app.web.batch_gui.api_client import APIClient

@pytest.fixture
def client():
    return APIClient(base_url="http://localhost:8000/api/v1")

@respx.mock
def test_api_client_start_batch_returns_run_id(client):
    respx.post("http://localhost:8000/api/v1/batch/start").mock(
        return_value=httpx.Response(200, json={"run_id": 42, "status": "queued"})
    )
    result = client.start_batch(
        source_dir="/data/in", target_dir="/data/out",
        target_format=["avif"], tool=["ffmpeg"], category=["general"]
    )
    assert result["run_id"] == 42
    assert result["status"] == "queued"

@respx.mock
def test_api_client_poll_status_returns_completed(client):
    respx.get("http://localhost:8000/api/v1/batch/status/42").mock(
        return_value=httpx.Response(200, json={
            "run_id": 42, "status": "completed",
            "total_images": 3, "summary": {"success_count": 3}
        })
    )
    status = client.get_status(42)
    assert status["status"] == "completed"

@respx.mock
def test_api_error_handling(client):
    respx.post("http://localhost:8000/api/v1/batch/start").mock(
        return_value=httpx.Response(500, json={"detail": "Internal error"})
    )
    
    with pytest.raises(Exception) as excinfo:
        client.start_batch("/src", "/dst", "webp", "magick")
    assert "Internal error" in str(excinfo.value)
