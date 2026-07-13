"""Tray _Api client — stdlib urllib, mocked so no network is touched.

Verifies each endpoint builds the right request (method, URL, body) and that
failures degrade to None / [] rather than raising into the Qt event loop.
"""
from __future__ import annotations

import json
import sys
from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="tray is win32-only")

from app.windows import tray as tray_mod


@pytest.fixture
def capture(monkeypatch):
    """Patch urlopen; capture the Request and return a canned JSON payload."""
    state = {"request": None, "payload": {"ok": True}, "raise": False}

    @contextmanager
    def fake_urlopen(req, timeout=None):
        state["request"] = req
        state["timeout"] = timeout
        if state["raise"]:
            raise OSError("boom")
        resp = MagicMock()
        resp.read.return_value = json.dumps(state["payload"]).encode()
        yield resp

    monkeypatch.setattr(tray_mod.urllib.request, "urlopen", fake_urlopen)
    return state


def test_health_hits_ready_endpoint(capture):
    capture["payload"] = {"ready": True}
    api = tray_mod._Api()
    out = api.health()
    assert out == {"ready": True}
    assert capture["request"].full_url == "http://localhost:8000/healthz/ready"
    assert capture["request"].get_method() == "GET"


def test_batch_start_posts_json_body(capture):
    capture["payload"] = {"run_id": 42, "status": "queued"}
    api = tray_mod._Api()
    out = api.batch_start({"source_dir": "C:/a", "tool": ["magick"]})
    assert out["run_id"] == 42
    req = capture["request"]
    assert req.full_url == "http://localhost:8000/api/v1/batch/start"
    assert req.get_method() == "POST"
    assert req.get_header("Content-type") == "application/json"
    assert json.loads(req.data) == {"source_dir": "C:/a", "tool": ["magick"]}


def test_batch_control_targets_run_id(capture):
    api = tray_mod._Api()
    api.batch_control(7, "pause")
    req = capture["request"]
    assert req.full_url == "http://localhost:8000/api/v1/batch/7/control"
    assert json.loads(req.data) == {"action": "pause"}


def test_batch_restart_posts_empty_body(capture):
    api = tray_mod._Api()
    api.batch_restart(9)
    req = capture["request"]
    assert req.full_url == "http://localhost:8000/api/v1/batch/9/restart"
    assert req.get_method() == "POST"


def test_hotfolder_delete_uses_delete_method(capture):
    api = tray_mod._Api()
    api.hotfolder_delete("abc")
    req = capture["request"]
    assert req.full_url == "http://localhost:8000/api/v1/hotfolder/abc"
    assert req.get_method() == "DELETE"


def test_hotfolder_register_posts_payload(capture):
    api = tray_mod._Api()
    api.hotfolder_register({"source_dir": "C:/w"})
    req = capture["request"]
    assert req.full_url == "http://localhost:8000/api/v1/hotfolder/register"
    assert json.loads(req.data) == {"source_dir": "C:/w"}


def test_calibrate_posts_payload(capture):
    api = tray_mod._Api()
    api.calibrate({"sample": 30})
    assert capture["request"].full_url == "http://localhost:8000/api/v1/calibrate"


def test_batch_history_returns_list(capture):
    capture["payload"] = [{"run_id": 1}, {"run_id": 2}]
    api = tray_mod._Api()
    assert api.batch_history() == [{"run_id": 1}, {"run_id": 2}]


def test_batch_history_coerces_non_list_to_empty(capture):
    capture["payload"] = {"error": "nope"}  # dict, not list
    api = tray_mod._Api()
    assert api.batch_history() == []


def test_failure_returns_none(capture):
    capture["raise"] = True
    api = tray_mod._Api()
    assert api.health() is None
    assert api.batch_start({}) is None


def test_failure_history_returns_empty(capture):
    capture["raise"] = True
    api = tray_mod._Api()
    assert api.batch_history() == []
    assert api.hotfolders() == []


def test_timeout_is_short(capture):
    api = tray_mod._Api()
    api.health()
    assert capture["timeout"] == tray_mod._Api.TIMEOUT
    assert tray_mod._Api.TIMEOUT <= 2.0  # must not stall the UI thread


def test_trace_id_propagated_in_headers(capture):
    token = tray_mod._current_trace_id.set("tray-test-12345")
    try:
        api = tray_mod._Api()
        api.health()
        req = capture["request"]
        assert req.get_header("X-trace-id") == "tray-test-12345"
    finally:
        tray_mod._current_trace_id.reset(token)


def test_api_token_header_propagated(capture, monkeypatch):
    monkeypatch.setenv("PIXELPIVOT_API_TOKEN", "secret-token-123")
    api = tray_mod._Api()
    api.health()
    req = capture["request"]
    assert req.get_header("X-api-token") == "secret-token-123"


def test_httperror_returns_parsed_dict(monkeypatch):
    import io
    import urllib.error

    def fake_urlopen(req, timeout=None):
        err_fp = io.BytesIO(json.dumps({"ready": False, "status": "degraded"}).encode())
        raise urllib.error.HTTPError(req.full_url, 503, "Service Unavailable", {}, err_fp)

    monkeypatch.setattr(tray_mod.urllib.request, "urlopen", fake_urlopen)
    api = tray_mod._Api()
    res = api.health()
    assert res["_http_code"] == 503
    assert res["status"] == "degraded"

