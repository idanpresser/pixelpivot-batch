"""7.3 — subprocess timeout scales with batch size.

A single global FFMPEG_TIMEOUT applied to a native command batching N files
causes false-timeout kills of large batches (the command legitimately needs
~N x the per-file budget). The effective timeout must scale with input count,
while a single-file encode keeps the tight base timeout.
"""
from unittest.mock import MagicMock

import app.core.config as config
from app.core.config import FFMPEG_TIMEOUT, batch_subprocess_timeout
from app.core.converters.magick_converter import MagickConverter


def test_single_file_timeout_is_tight():
    assert batch_subprocess_timeout(1) == FFMPEG_TIMEOUT


def test_batch_timeout_scales_linearly_with_count():
    assert batch_subprocess_timeout(200) == FFMPEG_TIMEOUT * 200


def test_batch_timeout_is_monotonic_and_floored_at_base():
    assert batch_subprocess_timeout(0) == FFMPEG_TIMEOUT      # never below base
    prev = 0.0
    for n in (1, 2, 10, 50, 200):
        t = batch_subprocess_timeout(n)
        assert t >= prev
        prev = t


class _FakeMonitor:
    def __init__(self, *a, **k): pass
    def start(self): pass
    def stop(self): return {}


def test_mogrify_batch_timeout_scales_with_chunk_size(tmp_path, monkeypatch):
    """The native mogrify batch command's communicate() timeout scales with the
    number of files in the chunk, not a flat per-file constant."""
    import os
    captured = {}
    out_dir = tmp_path / "out"

    class _FakeProc:
        def __init__(self, *a, **k):
            self.pid = 999
            self.returncode = 0
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def communicate(self, timeout=None):
            captured["timeout"] = timeout
            # bd-qk1.5: produce the expected no-suffix outputs so the batch path
            # counts success without falling back to per-file convert (which
            # would overwrite the captured batch timeout with a per-file one).
            os.makedirs(out_dir, exist_ok=True)
            for i in range(5):
                (out_dir / f"img_{i}.webp").write_bytes(b"webpdata")
            return ("", "")

    monkeypatch.setattr("app.core.converters.magick_converter.TelemetryMonitor", _FakeMonitor)
    monkeypatch.setattr("app.core.converters.magick_converter.subprocess.Popen", _FakeProc)

    conv = MagickConverter(magick_path="magick")

    n = 5
    files = []
    for i in range(n):
        p = tmp_path / f"img_{i}.png"
        p.write_bytes(b"dummy")
        files.append(str(p))
    dims = {f: (100, 100) for f in files}

    conv.convert_batch(files, str(out_dir), "webp", [80.0] * n, dimensions=dims)

    assert captured.get("timeout") == batch_subprocess_timeout(n), (
        f"mogrify communicate timeout {captured.get('timeout')} != "
        f"scaled {batch_subprocess_timeout(n)}"
    )
