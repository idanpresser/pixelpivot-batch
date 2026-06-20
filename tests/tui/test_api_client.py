# tests/tui/test_api_client.py
import httpx
from app.tui.api_client import TuiApiClient

def _client_with(handler):
    transport = httpx.MockTransport(handler)
    api = TuiApiClient("http://test/api/v1")
    api._transport = transport     # injected for tests
    return api

def test_start_batch_posts_payload():
    seen = {}
    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"run_id": 9, "status": "queued"})
    api = _client_with(handler)
    out = api.start_batch("/s", "/d", ["webp"], ["magick"], ["general"])
    assert out["run_id"] == 9
    assert seen["url"].endswith("/batch/start")

def test_get_progress():
    def handler(request):
        return httpx.Response(200, json={"cells_done": 1, "cells_total": 2, "cpu_pct": 10.0, "ram_mb": 1.0})
    api = _client_with(handler)
    assert api.get_progress(9)["cells_total"] == 2

def test_control_and_restart():
    def handler(request):
        if request.url.path.endswith("/control"):
            return httpx.Response(200, json={"run_id": 9, "action": "pause"})
        return httpx.Response(200, json={"run_id": 10, "status": "queued"})
    api = _client_with(handler)
    assert api.control(9, "pause")["action"] == "pause"
    assert api.restart(9)["run_id"] == 10
