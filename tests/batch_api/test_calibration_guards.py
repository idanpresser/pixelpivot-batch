# tests/batch_api/test_calibration_guards.py
from app.batch_api import calibration_runner


def test_run_calibration_skips_resolution_unsupported(monkeypatch, tmp_path):
    # jxl with a 10x10 image must be rejected by is_resolution_supported
    # (degenerate-dim native-crash guard) before any convert() call.
    called = {"convert": 0}

    class Spy:
        def get_name(self): return "vips"
        def convert(self, *a, **k):
            called["convert"] += 1
            return {"success": True, "fatal_error": False, "bytes_written": 1, "duration_ms": 1.0}

    monkeypatch.setattr(calibration_runner, "decode_rgb", lambda _p: __import__("numpy").zeros((10, 10, 3), "uint8"))
    monkeypatch.setattr(calibration_runner, "probe_image_dimensions", lambda _p: (10, 10))

    img = tmp_path / "tiny.png"
    img.write_bytes(b"x")

    class FakeOrch:
        converters = {"vips": Spy()}
        class interpolator:
            version = "t"
            @staticmethod
            def get_interpolated_quality(*a, **k): return 80.0
    monkeypatch.setattr(calibration_runner, "BatchOrchestrator", lambda: FakeOrch())
    monkeypatch.setattr(calibration_runner, "register_image", lambda *a, **k: 1)
    monkeypatch.setattr(calibration_runner, "insert_conversion", lambda *a, **k: 1)

    from app.core import config
    monkeypatch.setattr(config, "CALIBRATION_ENABLED", True)

    summary = calibration_runner.run_calibration(
        str(tmp_path), ["general"], ["vips"], ["jxl"], sample=5, regenerate_table=False,
    )
    assert called["convert"] == 0
    assert summary["failures"] >= 1
