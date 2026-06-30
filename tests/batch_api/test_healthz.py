# tests/batch_api/test_healthz.py
from fastapi.testclient import TestClient
from app.batch_api.main import app


def test_healthz_live_returns_200_alive():
    with TestClient(app) as client:
        resp = client.get("/healthz/live")
    assert resp.status_code == 200
    assert resp.json() == {"status": "alive"}


from app.batch_api import health


class _FakeConv:
    def __init__(self, **attrs):
        self.__dict__.update(attrs)


class _FakeOrch:
    def __init__(self):
        self.converters = {
            "magick": _FakeConv(magick_path="magick"),
            "ffmpeg": _FakeConv(ffmpeg_path="ffmpeg"),
            "sharp": _FakeConv(port=8765),
        }


def test_readiness_checks_returns_named_probes():
    checks = health.readiness_checks(_FakeOrch())
    names = {c.name for c in checks}
    assert {"db", "storage", "magick", "ffmpeg", "sharp"} <= names


def test_readiness_db_failure_named(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr(health, "get_connection", _boom)
    checks = {c.name: c for c in health.readiness_checks(_FakeOrch())}
    assert checks["db"].ok is False
    assert "db down" in checks["db"].detail


from app.batch_api import health as health_mod


def _all_ok(_orch):
    return [health_mod.Check(n, True, "ok") for n in ("db", "storage", "magick", "ffmpeg", "sharp")]


def test_ready_all_ok_returns_200(monkeypatch):
    monkeypatch.setattr("app.batch_api.main.readiness_checks", _all_ok)
    with TestClient(app) as client:
        resp = client.get("/healthz/ready")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ready"


def test_ready_broken_dep_returns_503_naming_check(monkeypatch):
    def _ffmpeg_down(_orch):
        out = _all_ok(_orch)
        return [c if c.name != "ffmpeg" else health_mod.Check("ffmpeg", False, "not found") for c in out]
    monkeypatch.setattr("app.batch_api.main.readiness_checks", _ffmpeg_down)
    with TestClient(app) as client:
        resp = client.get("/healthz/ready")
    assert resp.status_code == 503
    body = resp.json()
    assert "ffmpeg" in body["failed"]
    assert body["checks"]["ffmpeg"]["ok"] is False


