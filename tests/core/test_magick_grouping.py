import os
import pytest
from unittest.mock import MagicMock, patch
from app.core.converters.magick_converter import MagickConverter

def test_magick_batch_grouping_by_res(tmp_path):
    """
    Verify that MagickConverter groups by quality AND resolution bucket.
    """
    conv = MagickConverter(magick_path="magick")

    input_paths = ["small1.jpg", "small2.jpg", "large1.jpg"]
    qualities = [80, 80, 80]
    output_dir = str(tmp_path / "out")

    def mock_get_bucket(p):
        if "small" in p: return "small"
        return "large"

    def _write_outputs(*_a, **_k):
        # bd-qk1.5: no-suffix path now verifies outputs exist, so mogrify must
        # actually produce them for the batch path to count success (no fallback).
        os.makedirs(output_dir, exist_ok=True)
        for stem in ("small1", "small2", "large1"):
            with open(os.path.join(output_dir, f"{stem}.webp"), "wb") as fh:
                fh.write(b"webpdata")
        return ("ok", "")

    with patch("app.core.converters.magick_converter.get_resolution_bucket_from_path", side_effect=mock_get_bucket), \
         patch("subprocess.Popen") as mock_popen:

        mock_proc = mock_popen.return_value
        mock_proc.__enter__.return_value = mock_proc
        mock_proc.communicate.side_effect = _write_outputs
        mock_proc.returncode = 0
        mock_proc.pid = 1234 # Real integer PID

        conv.convert_batch(input_paths, output_dir, "webp", qualities)

        # Should have called Popen TWICE (one per group)
        assert mock_popen.call_count == 2
