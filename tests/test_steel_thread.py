"""Steel thread test: one real image through each converter, sequential, with peak-RAM reporting.

Purpose: catch OOM before running the full suite. Each converter gets one 800x600 PNG.
Peak RSS delta per converter must stay under 300 MB. Test is skipped if real binaries
are absent (CI without vendored bins).

Run with: pytest tests/test_steel_thread.py -v -s
"""
import gc
import os
import sys
import time
import shutil
import tempfile
from pathlib import Path

import psutil
import pytest
from PIL import Image


PROJ = Path(__file__).resolve().parent.parent
BIN_FFMPEG = PROJ / "bin" / "ffmpeg" / "ffmpeg.exe"
BIN_MAGICK = PROJ / "bin" / "magick" / "magick.exe"
BIN_VIPS   = PROJ / "bin" / "vips" / "bin" / "vips.exe"

PEAK_RAM_LIMIT_MB = 300


def _rss_mb() -> float:
    return psutil.Process().memory_info().rss / (1024 * 1024)


def _make_test_image(path: Path, size=(800, 600)) -> None:
    img = Image.new("RGB", size, color=(128, 64, 192))
    img.save(str(path), "PNG")


def _run_converter(name: str, tmp_src: Path, tmp_dst: Path, quality: float = 80.0) -> dict:
    """Import and run one converter. Returns timing and peak RSS delta."""
    gc.collect()
    rss_before = _rss_mb()
    t0 = time.perf_counter()

    if name == "magick":
        if not BIN_MAGICK.exists():
            return {"skipped": True, "reason": "magick binary absent"}
        from app.core.converters.magick_converter import MagickConverter
        conv = MagickConverter(magick_path=str(BIN_MAGICK))
    elif name == "ffmpeg":
        if not BIN_FFMPEG.exists():
            return {"skipped": True, "reason": "ffmpeg binary absent"}
        from app.core.converters.ffmpeg_converter import FFmpegConverter
        conv = FFmpegConverter(ffmpeg_path=str(BIN_FFMPEG))
    elif name == "vips":
        try:
            import pyvips  # noqa
        except ImportError:
            return {"skipped": True, "reason": "pyvips not installed"}
        from app.core.converters.vips_converter import VipsConverter
        conv = VipsConverter()
    elif name == "sharp":
        from app.core.converters.sharp_converter import SharpConverter
        conv = SharpConverter(port=8765)
    else:
        return {"skipped": True, "reason": f"unknown converter {name}"}

    src = tmp_src / "test_input.png"
    out = str(tmp_dst / f"test_output_{name}.avif")

    result = conv.convert(str(src), out, "avif", quality)

    elapsed = time.perf_counter() - t0
    gc.collect()
    rss_after = _rss_mb()

    return {
        "skipped": False,
        "success": result.get("success", False),
        "error": result.get("error"),
        "elapsed_s": round(elapsed, 2),
        "rss_before_mb": round(rss_before, 1),
        "rss_after_mb": round(rss_after, 1),
        "rss_delta_mb": round(rss_after - rss_before, 1),
    }


@pytest.fixture(scope="module")
def image_dirs(tmp_path_factory):
    src = tmp_path_factory.mktemp("src")
    dst = tmp_path_factory.mktemp("dst")
    _make_test_image(src / "test_input.png")
    return src, dst


# Tool-native quality per (tool, format). ffmpeg AVIF uses CRF (0-63 lower=better).
QUALITY_BY_TOOL = {
    "magick": 80.0,
    "ffmpeg": 28.0,  # libaom-av1 CRF, must be <= 63
    "vips":   80.0,
    "sharp":  80.0,
}


@pytest.mark.parametrize("converter", ["magick", "ffmpeg", "vips", "sharp"])
def test_converter_steel_thread(converter, image_dirs, capsys):
    """One image through one converter. Assert success and RAM delta < limit."""
    src, dst = image_dirs
    stats = _run_converter(converter, src, dst, quality=QUALITY_BY_TOOL[converter])

    if stats.get("skipped"):
        pytest.skip(stats["reason"])

    with capsys.disabled():
        print(
            f"\n[{converter}] "
            f"success={stats['success']}  "
            f"elapsed={stats['elapsed_s']}s  "
            f"RAM: {stats['rss_before_mb']:.1f} -> {stats['rss_after_mb']:.1f} MB  "
            f"delta={stats['rss_delta_mb']:+.1f} MB"
        )
        if stats.get("error"):
            print(f"  error: {stats['error']}")

    assert stats["rss_delta_mb"] < PEAK_RAM_LIMIT_MB, (
        f"{converter} leaked {stats['rss_delta_mb']:.1f} MB (limit {PEAK_RAM_LIMIT_MB} MB)"
    )
    assert stats["success"], f"{converter} conversion failed: {stats.get('error')}"


def test_full_suite_memory_guard():
    """Verify current process RSS is under 500 MB before the suite continues.

    If this fails, the test runner itself is already bloated — likely from
    running multiple pytest processes in parallel or a prior leaked fixture.
    """
    rss = _rss_mb()
    print(f"\nCurrent process RSS: {rss:.1f} MB")
    assert rss < 500, (
        f"Test runner RSS {rss:.1f} MB exceeds 500 MB threshold. "
        "Do not run multiple pytest processes in parallel."
    )
