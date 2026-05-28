"""
RED tests: AVIF conversion of real images via all converters.
Each converter's .convert() and .convert_batch() are called with real files
from test_examples/. Tests skip if the required system tool is not available.
"""
import shutil
import pytest
from pathlib import Path

from app.core.converters.magick_converter import MagickConverter
from app.core.converters.ffmpeg_converter import FFmpegConverter
from app.core.converters.vips_converter import VipsConverter

TEST_IMAGES = Path("test_examples")

# One image per category — broad coverage without slow runtimes
SAMPLE_IMAGES = [
    TEST_IMAGES / "highRes_0055_4B91E53B4F08F2912ED97EF166C3EFE3C.jpg",
    TEST_IMAGES / "lowContrst_0510_23030E51A4400884518F3B044251A27C2.png",
    TEST_IMAGES / "web_0230_C8DCDE699C89E00CCC430B2B5224A55C9.jpg",
]


def _magick_available() -> bool:
    return shutil.which("magick") is not None


def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def _vips_available() -> bool:
    try:
        import pyvips  # noqa: F401
        return True
    except (ImportError, OSError):
        return False


# ---------------------------------------------------------------------------
# MagickConverter — AVIF via ImageMagick 7
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _magick_available(), reason="ImageMagick (magick) not on PATH")
class TestMagickAvif:
    def test_converts_single_jpg_to_avif(self, tmp_path):
        converter = MagickConverter(magick_path="magick")
        img = SAMPLE_IMAGES[0]
        out = tmp_path / f"{img.stem}.avif"
        result = converter.convert(str(img), str(out), "avif", 75)
        assert result["success"], f"magick AVIF failed: {result.get('error')}"
        assert out.exists() and out.stat().st_size > 0

    def test_converts_single_png_to_avif(self, tmp_path):
        converter = MagickConverter(magick_path="magick")
        img = SAMPLE_IMAGES[1]
        out = tmp_path / f"{img.stem}.avif"
        result = converter.convert(str(img), str(out), "avif", 75)
        assert result["success"], f"magick AVIF (PNG) failed: {result.get('error')}"
        assert out.exists() and out.stat().st_size > 0

    def test_batch_converts_all_samples(self, tmp_path):
        converter = MagickConverter(magick_path="magick")
        paths = [str(p) for p in SAMPLE_IMAGES]
        qualities = [75.0] * len(paths)
        result = converter.convert_batch(paths, str(tmp_path), "avif", qualities)
        assert result["failure_count"] == 0, f"Batch failures: {result.get('errors')}"
        assert result["success_count"] == len(paths)
        for img in SAMPLE_IMAGES:
            assert (tmp_path / f"{img.stem}.avif").exists(), f"Missing output: {img.stem}.avif"

    def test_output_is_smaller_than_input(self, tmp_path):
        """AVIF output should compress a high-res JPEG noticeably."""
        converter = MagickConverter(magick_path="magick")
        img = SAMPLE_IMAGES[0]
        out = tmp_path / f"{img.stem}.avif"
        converter.convert(str(img), str(out), "avif", 75)
        assert out.stat().st_size < img.stat().st_size, (
            f"AVIF ({out.stat().st_size}B) is not smaller than source ({img.stat().st_size}B)"
        )


# ---------------------------------------------------------------------------
# FFmpegConverter — AVIF via libaom-av1
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _ffmpeg_available(), reason="FFmpeg not on PATH")
class TestFFmpegAvif:
    def test_converts_single_jpg_to_avif(self, tmp_path):
        converter = FFmpegConverter(ffmpeg_path="ffmpeg")
        img = SAMPLE_IMAGES[0]
        out = tmp_path / f"{img.stem}.avif"
        # CRF 30 = good quality for libaom-av1 (0=lossless, 63=worst)
        result = converter.convert(str(img), str(out), "avif", 30)
        assert result["success"], f"ffmpeg AVIF failed: {result.get('error')}"
        assert out.exists() and out.stat().st_size > 0

    def test_converts_single_png_to_avif(self, tmp_path):
        converter = FFmpegConverter(ffmpeg_path="ffmpeg")
        img = SAMPLE_IMAGES[1]
        out = tmp_path / f"{img.stem}.avif"
        result = converter.convert(str(img), str(out), "avif", 30)
        assert result["success"], f"ffmpeg AVIF (PNG) failed: {result.get('error')}"
        assert out.exists() and out.stat().st_size > 0

    def test_batch_converts_all_samples(self, tmp_path):
        converter = FFmpegConverter(ffmpeg_path="ffmpeg")
        paths = [str(p) for p in SAMPLE_IMAGES]
        qualities = [30.0] * len(paths)
        result = converter.convert_batch(paths, str(tmp_path), "avif", qualities)
        assert result["failure_count"] == 0, f"Batch failures: {result.get('errors')}"
        assert result["success_count"] == len(paths)


# ---------------------------------------------------------------------------
# VipsConverter — AVIF via libheif + libaom
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _vips_available(), reason="pyvips not installed")
class TestVipsAvif:
    def test_converts_single_jpg_to_avif(self, tmp_path):
        converter = VipsConverter()
        img = SAMPLE_IMAGES[0]
        out = tmp_path / f"{img.stem}.avif"
        result = converter.convert(str(img), str(out), "avif", 75)
        assert result["success"], f"vips AVIF failed: {result.get('error')}"
        assert out.exists() and out.stat().st_size > 0

    def test_converts_single_png_to_avif(self, tmp_path):
        converter = VipsConverter()
        img = SAMPLE_IMAGES[1]
        out = tmp_path / f"{img.stem}.avif"
        result = converter.convert(str(img), str(out), "avif", 75)
        assert result["success"], f"vips AVIF (PNG) failed: {result.get('error')}"
        assert out.exists() and out.stat().st_size > 0

    def test_batch_converts_all_samples(self, tmp_path):
        converter = VipsConverter()
        paths = [str(p) for p in SAMPLE_IMAGES]
        qualities = [75.0] * len(paths)
        result = converter.convert_batch(paths, str(tmp_path), "avif", qualities)
        assert result["failure_count"] == 0, f"Batch failures: {result.get('errors')}"
        assert result["success_count"] == len(paths)
