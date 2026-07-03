"""7.2 — RAM-aware cap on the native image2 subprocess path.

The multimap path already bounds files-per-command by decoded-frame footprint
(dynamic_max_files). The image2 demuxer path did not: it fed an entire uniform-
size sub-group into a single ffmpeg command, so a large batch of big frames had
no projected-RAM ceiling. This test asserts image2 splits into chunks whose size
is derived from the frame footprint, matching the multimap guarantee.
"""
from unittest.mock import patch

import app.core.converters.ffmpeg_converter as ffm
from app.core.converters.ffmpeg_converter import FFmpegConverter
from app.core.converters.chunk_sizing import dynamic_max_files


class _FakeResult:
    success = False
    error = "no output (test)"


class _FakeProc:
    """Records every spawn so we can count image2 chunks."""

    instances = []

    def __init__(self, ffmpeg_path, args, wall_timeout_s=None):
        self.args = args
        _FakeProc.instances.append(self)

    def spawn(self):
        return 4321

    def run(self):
        return _FakeResult()


class _FakeMonitor:
    def __init__(self, *a, **k): pass
    def start(self): pass
    def stop(self): return {}


def test_image2_path_chunks_by_frame_footprint(tmp_path, monkeypatch):
    _FakeProc.instances = []

    # 20 uniform 2000x2000 frames -> 4.0 MP each.
    wh = (2000, 2000)
    mp = (wh[0] * wh[1]) / 1_000_000.0  # 4.0
    n_files = 20
    paths = []
    for i in range(n_files):
        p = tmp_path / f"src_{i:03d}.png"
        p.write_bytes(b"dummy")
        paths.append(str(p))

    # Pin the RAM budget so dynamic_max_files == 4 (16 MB/frame, 64 MB budget).
    per_image = 4 * 1_000_000 * mp          # 16e6 bytes
    ram_budget = 4 * per_image              # room for exactly 4 frames -> 64e6

    class _VM:
        available = ram_budget / ffm.CHUNK_RAM_BUDGET_FRACTION

    monkeypatch.setattr("psutil.virtual_memory", lambda: _VM())
    monkeypatch.setattr(ffm, "FFmpegProcess", _FakeProc)
    monkeypatch.setattr(ffm, "TelemetryMonitor", _FakeMonitor)

    expected_chunk_size = dynamic_max_files(mp, ram_budget, ceiling=ffm.FFMPEG_BATCH_MAX_FILES)
    assert expected_chunk_size == 4  # sanity on the fixture math

    conv = FFmpegConverter(ffmpeg_path="ffmpeg")
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    conv._run_image2_path(
        paths,
        str(out_dir),
        "webp",
        80.0,
        ["-c:v", "libwebp"],
        run_id=None,
        wh=wh,
    )

    import math
    expected_chunks = math.ceil(n_files / expected_chunk_size)  # ceil(20/4) = 5
    assert len(_FakeProc.instances) == expected_chunks, (
        f"image2 spawned {len(_FakeProc.instances)} ffmpeg commands; "
        f"expected {expected_chunks} RAM-bounded chunks"
    )
