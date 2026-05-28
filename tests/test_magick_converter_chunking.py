"""Unit test for MagickConverter's pack_chunks integration -- verifies the
cmdline-byte cap forces smaller chunks when paths are long."""

from unittest.mock import patch, MagicMock
from app.core.converters.magick_converter import MagickConverter
from app.core.converters.ffmpeg_batch_helpers import pack_chunks


def test_magick_chunking_respects_cmdline_byte_cap():
    """If we synthesize 50 paths each ~200 bytes long, the count cap
    (MAGICK_MOGRIFY_CHUNK=??) might allow them all in one chunk, but the
    byte cap (MAGICK_MOGRIFY_MAX_CMDLINE_BYTES=7500) should force a split."""
    long_paths = [f"C:/very/deep/nested/path/{i:04d}_{'x' * 150}.png" for i in range(50)]

    # We test pack_chunks directly with the same parameters MagickConverter uses,
    # since exercising the full converter requires an actual magick binary.
    fake_magick_path = "C:/Program Files/ImageMagick/magick.exe"
    fake_params = ["-quality", "85"]
    fixed_overhead = len(fake_magick_path) + 64 + sum(len(t) for t in fake_params) + 32

    chunks = pack_chunks(
        [(p, "") for p in long_paths],
        max_files=100,                # generous file cap
        max_cmdline_bytes=7500,       # the real Windows-safe cap
        fixed_overhead=fixed_overhead,
    )

    # Sum preserved
    assert sum(len(c) for c in chunks) == 50
    # Should split into multiple chunks because of byte cap, NOT the file cap
    assert len(chunks) >= 2, f"Expected multiple chunks due to byte cap, got {len(chunks)}"
    # No chunk should exceed the byte cap (except a single-pair chunk, which is allowed)
    for chunk in chunks:
        approx = fixed_overhead + sum(len(i) + len(o) + 20 for i, o in chunk)
        assert approx <= 7500 or len(chunk) == 1
