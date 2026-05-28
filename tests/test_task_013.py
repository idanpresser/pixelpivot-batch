"""Task 013 - native mogrify batch must drive the circuit breaker.

MagickConverter.convert_batch (the native mogrify fast path) updates
success/failure counts but historically did NOT touch the circuit breaker:
  - native success never called _reset_failures() (stale failures lingered)
  - native failure relied entirely on the per-file fallback's _run_subprocess
    to advance the breaker

These tests pin: native outcomes drive the breaker directly, accounted once
per chunk, without depending on (or double-counting via) the fallback.
"""

from pathlib import Path

import pytest

from app.core.converters.magick_converter import MagickConverter


class _FakeMonitor:
    def __init__(self, *a, **k):
        pass

    def start(self):
        pass

    def stop(self):
        return {}


def _fake_popen_cls(returncode):
    class _FakeProc:
        def __init__(self, *a, **k):
            self.pid = 424242
            self.returncode = returncode

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def communicate(self, timeout=None):
            return ("", "" if returncode == 0 else "mogrify boom")

    return _FakeProc


def _patch_native(monkeypatch, returncode):
    monkeypatch.setattr(
        "app.core.converters.magick_converter.subprocess.Popen",
        _fake_popen_cls(returncode),
    )
    monkeypatch.setattr(
        "app.core.converters.magick_converter.TelemetryMonitor", _FakeMonitor
    )


def _one_input(tmp_path):
    p = tmp_path / "a.png"
    p.write_bytes(b"not-a-real-image")
    return str(p)


def test_native_failure_trips_breaker_without_fallback(tmp_path, monkeypatch):
    conv = MagickConverter(magick_path="magick")
    _patch_native(monkeypatch, returncode=1)  # native mogrify always fails

    # Per-file fallback is a stub failure that does NOT touch the breaker, so
    # the only thing that can advance it is the native path itself.
    monkeypatch.setattr(
        conv, "convert", lambda *a, **k: {"success": False, "error": "stub", "telemetry": {}}
    )

    inp = _one_input(tmp_path)
    dims = {inp: (100, 100)}
    assert conv.is_broken is False

    for _ in range(conv.failure_threshold):
        conv.convert_batch([inp], str(tmp_path / "out"), "webp", [80.0], dimensions=dims)

    assert conv.is_broken is True


def test_native_success_resets_failures(tmp_path, monkeypatch):
    conv = MagickConverter(magick_path="magick")
    _patch_native(monkeypatch, returncode=0)  # native mogrify succeeds

    conv.consecutive_failures = 2  # stale failures from earlier trouble
    inp = _one_input(tmp_path)
    dims = {inp: (100, 100)}

    conv.convert_batch([inp], str(tmp_path / "out"), "webp", [80.0], dimensions=dims)

    assert conv.consecutive_failures == 0


def test_native_failure_recovered_by_fallback_is_not_double_counted(tmp_path, monkeypatch):
    conv = MagickConverter(magick_path="magick")
    _patch_native(monkeypatch, returncode=1)  # native fails...

    # ...but the per-file fallback fully recovers (stub success, no breaker effect).
    monkeypatch.setattr(
        conv, "convert", lambda *a, **k: {"success": True, "telemetry": {}}
    )

    inp = _one_input(tmp_path)
    dims = {inp: (100, 100)}

    conv.convert_batch([inp], str(tmp_path / "out"), "webp", [80.0], dimensions=dims)

    # Chunk recovered -> healthy, and the failure counter is not inflated.
    assert conv.consecutive_failures == 0
    assert conv.is_broken is False


# --------------------------------------------------------------------------
# Sharp native (socket pipeline) batch must drive the breaker too.
# --------------------------------------------------------------------------

class _FakeSock:
    """Minimal stand-in for the Sharp daemon socket: every request gets one
    response line with the configured success flag."""

    def __init__(self, success):
        import json as _json
        self._resp = (_json.dumps({"success": success, "error": None if success else "boom"}) + "\n").encode("utf-8")

    def sendall(self, data):
        pass

    def settimeout(self, t):
        pass

    def recv(self, n):
        return self._resp


def _make_sharp(monkeypatch, success):
    from app.core.converters.sharp_converter import SharpConverter

    conv = SharpConverter(port=8765)
    conv.daemon_process = None  # -> no TelemetryMonitor spawned
    monkeypatch.setattr(conv, "_ensure_daemon_running", lambda: None)
    monkeypatch.setattr(conv, "_get_connection", lambda: _FakeSock(success))
    return conv


def test_sharp_native_failure_trips_breaker(tmp_path, monkeypatch):
    conv = _make_sharp(monkeypatch, success=False)
    inp = str(tmp_path / "a.png")
    Path(inp).write_bytes(b"x")

    assert conv.is_broken is False
    for _ in range(conv.failure_threshold):
        conv.convert_batch([inp], str(tmp_path / "out"), "webp", [80.0])

    assert conv.is_broken is True


def test_sharp_native_success_resets_failures(tmp_path, monkeypatch):
    conv = _make_sharp(monkeypatch, success=True)
    conv.consecutive_failures = 2  # stale failures

    inp = str(tmp_path / "a.png")
    Path(inp).write_bytes(b"x")

    conv.convert_batch([inp], str(tmp_path / "out"), "webp", [80.0])

    assert conv.consecutive_failures == 0
