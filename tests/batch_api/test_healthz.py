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
from app.core import toolcheck


def test_readiness_marks_magick_not_ok_when_binary_absent(monkeypatch):
    # E8 8.1 lock-in: readiness_checks must probe each encoder binary, so a
    # missing magick binary surfaces as an unhealthy check named "magick"
    # (not only DB/storage failures). Force the other probes healthy so the
    # encoder probe alone decides the result.
    def _fake_check_binary(name, path_str):
        if name == "magick":
            return toolcheck.ToolStatus("magick", ok=False, detail="not found")
        return toolcheck.ToolStatus(name, ok=True, detail="ok")
    monkeypatch.setattr(health.toolcheck, "check_binary", _fake_check_binary)
    monkeypatch.setattr(health, "_check_db", lambda: health.Check("db", True, "ok"))
    monkeypatch.setattr(health, "_check_storage", lambda: health.Check("storage", True, "ok"))
    monkeypatch.setattr(
        health.toolcheck, "check_sharp_daemon",
        lambda port: toolcheck.ToolStatus("sharp", ok=True, detail="ok"),
    )
    checks = {c.name: c for c in health.readiness_checks(_FakeOrch())}
    assert checks["magick"].ok is False
    assert "not found" in checks["magick"].detail
    assert checks["ffmpeg"].ok is True


def test_ready_returns_503_naming_magick_when_encoder_absent(monkeypatch):
    # Endpoint-level acceptance: magick absent -> 503 naming the missing encoder.
    def _magick_down(_orch):
        out = _all_ok(_orch)
        return [c if c.name != "magick" else health_mod.Check("magick", False, "not found") for c in out]
    monkeypatch.setattr("app.batch_api.main.readiness_checks", _magick_down)
    with TestClient(app) as client:
        resp = client.get("/healthz/ready")
    assert resp.status_code == 503
    assert "magick" in resp.json()["failed"]


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


