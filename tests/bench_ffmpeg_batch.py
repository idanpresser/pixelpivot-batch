"""
Wall-clock benchmark. Not part of the default test run (filename starts with
`bench_`, not `test_`, so pytest's default collection skips it).

Run manually:
    pytest tests/bench_ffmpeg_batch.py -v -s
"""

import shutil
import time
from pathlib import Path

import pytest
from PIL import Image

from app.core.converters.ffmpeg_converter import FFmpegConverter


pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None,
    reason="ffmpeg binary required",
)


def _make_png(path: Path, w: int, h: int) -> str:
    Image.new("RGB", (w, h), (123, 45, 67)).save(str(path), "PNG")
    return str(path)


def test_image2_path_is_faster_than_per_file_on_synthetic(tmp_path):
    """
    Synthetic baseline: 30 identically-sized PNGs.
      1) per-file convert() in a loop  (baseline)
      2) convert_batch() with IMAGE2_THRESHOLD=3 (hybrid path)
    Expect batch to be meaningfully faster.
    """
    N = 30
    inputs = [_make_png(tmp_path / f"img_{i}.png", 512, 512) for i in range(N)]

    conv = FFmpegConverter(ffmpeg_path=shutil.which("ffmpeg"))

    out_dir_a = tmp_path / "out_a"
    out_dir_a.mkdir()
    t0 = time.time()
    for p in inputs:
        conv.convert(p, str(out_dir_a / f"{Path(p).stem}.webp"), "webp", 80)
    t_per_file = time.time() - t0

    out_dir_b = tmp_path / "out_b"
    t0 = time.time()
    res = conv.convert_batch(inputs, str(out_dir_b), "webp", [80.0] * N)
    t_batch = time.time() - t0

    speedup = t_per_file / max(t_batch, 0.001)
    print(
        f"\n[synthetic 30x 512x512 PNGs -> webp]"
        f"\n  per-file: {t_per_file:.2f}s"
        f"\n  batch:    {t_batch:.2f}s"
        f"\n  speedup:  {speedup:.2f}x"
    )
    assert res["success_count"] == N
    assert t_batch < t_per_file, (
        f"batch ({t_batch:.2f}s) not faster than per-file ({t_per_file:.2f}s)"
    )


def test_realworld_test_examples_batch_vs_per_file(tmp_path):
    """
    Real-world: use the project's test_examples folder.
    Picks all `web_*.jpg` (typically uniform-ish dims) and runs both paths.
    Speedup is reported but not strictly asserted -- mixed-size groups may
    fall to multimap, which is still expected to beat per-file but by less.
    """
    repo_root = Path(__file__).parent.parent
    examples_dir = repo_root / "test_examples"
    if not examples_dir.exists():
        pytest.skip("test_examples folder not present")

    inputs = sorted(str(p) for p in examples_dir.glob("web_*.jpg"))
    if len(inputs) < 10:
        pytest.skip(f"need at least 10 real images, found {len(inputs)}")

    # Cap to 30 to keep the bench bounded.
    inputs = inputs[:30]
    conv = FFmpegConverter(ffmpeg_path=shutil.which("ffmpeg"))

    out_dir_a = tmp_path / "out_a"
    out_dir_a.mkdir()
    t0 = time.time()
    for p in inputs:
        conv.convert(p, str(out_dir_a / f"{Path(p).stem}.webp"), "webp", 80)
    t_per_file = time.time() - t0

    out_dir_b = tmp_path / "out_b"
    t0 = time.time()
    res = conv.convert_batch(inputs, str(out_dir_b), "webp", [80.0] * len(inputs))
    t_batch = time.time() - t0

    speedup = t_per_file / max(t_batch, 0.001)
    print(
        f"\n[real-world {len(inputs)} test_examples web_*.jpg -> webp]"
        f"\n  per-file: {t_per_file:.2f}s"
        f"\n  batch:    {t_batch:.2f}s"
        f"\n  speedup:  {speedup:.2f}x"
        f"\n  successes: {res['success_count']} / failures: {res['failure_count']}"
    )
    assert res["success_count"] == len(inputs)
    # Soft assertion -- real images vary; if it's not faster, something is wrong
    # but we want the timing visible first, not a cryptic failure.
    assert t_batch <= t_per_file, (
        f"batch ({t_batch:.2f}s) not faster than per-file ({t_per_file:.2f}s) "
        f"on real images -- speedup {speedup:.2f}x"
    )
