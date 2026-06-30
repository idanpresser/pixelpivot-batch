import socket
import pytest
from app.core.converters.sharp_converter import SharpConverter

def test_daemon_failure_falls_back_to_vips(monkeypatch, tmp_path):
    src = tmp_path / "a.png"
    src.write_bytes(b"fake")
    out = tmp_path / "a.webp"

    conv = SharpConverter()

    # Avoid attempting to run the daemon process
    monkeypatch.setattr(conv, "_ensure_daemon_running", lambda *a, **k: None)

    # Force the daemon connection to fail
    def dead_connection(*a, **k):
        raise socket.error("daemon gone")
    monkeypatch.setattr(conv, "_get_connection", dead_connection)

    called = {"vips": False}
    def fake_vips_convert(self, in_path, out_path, fmt, q, run_id=None, **kwargs):
        called["vips"] = True
        # Return a ConvertResult to match the expected return type
        from app.core.converters.base import ConvertResult
        return ConvertResult(success=True, error=None, duration_ms=10, bytes_written=10)
    monkeypatch.setattr("app.core.converters.vips_converter.VipsConverter.convert", fake_vips_convert, raising=False)

    res = conv.convert(str(src), str(out), "webp", 80)
    assert called["vips"] is True
    assert res["success"] is True
    assert res["tool"] == "vips"   # tool overwritten, not left as 'sharp'
