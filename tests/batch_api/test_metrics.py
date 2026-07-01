# tests/batch_api/test_metrics.py
from fastapi.testclient import TestClient
from app.batch_api.main import app
from app.batch_api import metrics


def test_metrics_endpoint_scrapeable():
    with TestClient(app) as client:
        resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "pixelpivot_jobs_total" in resp.text


def test_record_job_increments_counter():
    metrics.record_job(status="completed", tool="ffmpeg", fmt="webp")
    text = metrics.render().decode()
    assert 'pixelpivot_jobs_total{' in text
    assert 'status="completed"' in text


def test_record_noop_when_disabled(monkeypatch):
    monkeypatch.setattr(metrics, "_ENABLED", False)
    # Must not raise even though recording is a no-op.
    metrics.record_job(status="failed", tool="magick", fmt="avif")
    metrics.set_queue_depth(3)
    metrics.observe_compression_ratio(0.4)
