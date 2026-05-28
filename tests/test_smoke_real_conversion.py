"""End-to-end smoke test for the converter pipeline.

Generates a tiny PNG with Pillow, runs the real ImageMagick binary via
``MagickConverter.convert``, and asserts the output is a non-empty,
re-openable image. Skips automatically when ``magick`` is not on PATH so
machines without ImageMagick still run a green suite.

The rest of the test suite mocks ``subprocess.Popen``; this is the only
test that exercises the real command-line surface — a regression in
argument ordering or the ``_run_subprocess`` output-file-exists check
will surface here and nowhere else.
"""

from __future__ import annotations

import shutil

import pytest
from PIL import Image

from app.core.converters.magick_converter import MagickConverter

MAGICK_AVAILABLE = shutil.which("magick") is not None
SKIP_REASON = "magick binary not on PATH"


@pytest.mark.smoke
@pytest.mark.skipif(not MAGICK_AVAILABLE, reason=SKIP_REASON)
def test_magick_converts_real_png_to_webp(tmp_path):
    src = tmp_path / "tiny.png"
    Image.new("RGB", (32, 32), color=(120, 180, 40)).save(src)
    assert src.stat().st_size > 0

    out = tmp_path / "tiny.webp"
    converter = MagickConverter(magick_path="magick")
    result = converter.convert(
        input_path=str(src),
        output_path=str(out),
        target_format="webp",
        quality=80,
    )

    assert result["success"] is True, f"convert failed: {result.get('error')}"
    assert out.exists(), "output file was not produced"
    assert out.stat().st_size > 0, "output file is empty"

    with Image.open(out) as img:
        img.verify()
        assert img.format == "WEBP"


@pytest.mark.smoke
@pytest.mark.skipif(not MAGICK_AVAILABLE, reason=SKIP_REASON)
def test_magick_batch_real_pngs_to_webp(tmp_path):
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    paths = []
    for i, color in enumerate([(255, 0, 0), (0, 255, 0), (0, 0, 255)]):
        p = src_dir / f"img_{i}.png"
        Image.new("RGB", (16, 16), color=color).save(p)
        paths.append(str(p))

    out_dir = tmp_path / "out"
    converter = MagickConverter(magick_path="magick")
    result = converter.convert_batch(
        input_paths=paths,
        output_dir=str(out_dir),
        target_format="webp",
        qualities=[80.0, 80.0, 80.0],
    )

    assert result["success_count"] == 3, (
        f"expected 3 successes, got {result['success_count']} "
        f"failures={result['failure_count']} errors={result.get('errors')}"
    )
    assert result["failure_count"] == 0

    for p in paths:
        from pathlib import Path

        out = out_dir / f"{Path(p).stem}.webp"
        assert out.exists() and out.stat().st_size > 0
