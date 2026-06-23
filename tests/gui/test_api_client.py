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

def test_api_client_single_instance_multiple_requests():
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json=[]))
    test_client = APIClient(base_url="http://localhost:8000/api/v1", transport=transport)
    
    cli1 = test_client._client
    test_client.get_history()
    test_client.get_history()
    cli2 = test_client._client
    assert cli1 is cli2
    
    test_client.close()

def test_streamlit_session_state_caching(monkeypatch):
    session_state = {}
    monkeypatch.setattr("streamlit.session_state", session_state)
    monkeypatch.setattr("streamlit.set_page_config", lambda **kwargs: None)
    
    class FakeSidebar:
        def markdown(self, *args, **kwargs): pass
        def info(self, *args, **kwargs): pass
        
    monkeypatch.setattr("streamlit.sidebar", FakeSidebar())
    
    class FakeTab:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        
    monkeypatch.setattr("streamlit.tabs", lambda names: [FakeTab() for _ in names])
    monkeypatch.setattr("app.web.batch_gui.main.inject_theme_css", lambda: None)
    monkeypatch.setattr("app.web.batch_gui.main.inject_custom_css", lambda: None)
    monkeypatch.setattr("app.web.batch_gui.main.render_header", lambda: None)
    monkeypatch.setattr("app.web.batch_gui.main.render_run_panel", lambda cli: None)
    monkeypatch.setattr("app.web.batch_gui.main.render_hot_folder_panel", lambda cli: None)
    monkeypatch.setattr("app.web.batch_gui.main.render_history_panel", lambda cli: None)
    
    from app.web.batch_gui.main import main
    main()
    assert "api_client" in session_state
    client1 = session_state["api_client"]
    
    main()
    client2 = session_state["api_client"]
    
    assert client1 is client2
