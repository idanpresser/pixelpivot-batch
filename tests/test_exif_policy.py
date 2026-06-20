"""Lock-in tests for bd-vbm: uniform EXIF-preservation policy across converters.

Policy (docs/EXIF_POLICY.md): preserve EXIF metadata everywhere.

ffmpeg is the only converter that drops metadata by default, so it must pass
-map_metadata explicitly. magick/vips/sharp preserve by default; the guard here
is that no converter passes a metadata-stripping flag (e.g. ImageMagick -strip).
"""
from pathlib import Path

from app.core.converters.ffmpeg_converter import FFmpegConverter


def test_ffmpeg_single_convert_maps_metadata():
    """Single-image ffmpeg args carry -map_metadata 0 to preserve EXIF."""
    conv = FFmpegConverter("ffmpeg")
    args = conv._build_args("in.jpg", "out.webp", ["-c:v", "libwebp"])
    assert "-map_metadata" in args
    assert args[args.index("-map_metadata") + 1] == "0"


def test_magick_converter_does_not_strip_metadata():
    """MagickConverter source carries no -strip flag (would drop EXIF)."""
    src = (Path(__file__).resolve().parents[1]
           / "app" / "core" / "converters" / "magick_converter.py").read_text(encoding="utf-8")
    assert "-strip" not in src


def test_exif_policy_doc_exists_and_states_preserve():
    """The EXIF policy is documented and the decision is 'preserve'."""
    doc = (Path(__file__).resolve().parents[1] / "docs" / "EXIF_POLICY.md")
    assert doc.exists()
    text = doc.read_text(encoding="utf-8").lower()
    assert "preserve" in text
