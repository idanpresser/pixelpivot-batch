from fastapi.testclient import TestClient
from app.batch_api.main import app
from app.core import tracing

def test_api_middleware_adopts_x_trace_id():
    client = TestClient(app)
    
    # 1. Send request with X-Trace-Id header
    response = client.get("/healthz/live", headers={"X-Trace-Id": "test-trace-123"})
    assert response.status_code == 200
    assert response.headers.get("X-Trace-Id") == "test-trace-123"

    # 2. Send request without X-Trace-Id header
    response2 = client.get("/healthz/live")
    assert response2.status_code == 200
    assert response2.headers.get("X-Trace-Id") is not None
    assert response2.headers.get("X-Trace-Id").startswith("req-")
