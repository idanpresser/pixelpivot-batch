
import os
import sys
from pathlib import Path
import pytest
from unittest.mock import patch, MagicMock
from app.core.converters.ffmpeg_converter import FFmpegConverter

def test_ffmpeg_convert_batch_signature(monkeypatch):
    """Verify that FFmpegConverter.convert_batch doesn't crash due to signature mismatch."""
    conv = FFmpegConverter(ffmpeg_path="ffmpeg")
    
    # Mock group_by_dimensions to see if it's called correctly
    with patch("app.core.converters.ffmpeg_converter.group_by_dimensions") as mock_group:
        mock_group.return_value = { (100, 100): ["test.png"] }
        
        # We also need to mock other things to avoid actual execution
        with patch.object(conv, "_run_multimap_path", return_value=(1, 0, [], {})):
            with patch("app.core.converters.ffmpeg_converter.encoder_params_for", return_value=["-some-param"]):
                res = conv.convert_batch(
                    input_paths=["test.png"],
                    output_dir="out",
                    target_format="webp",
                    qualities=[80.0],
                )
                
                # Check how group_by_dimensions was called
                args, kwargs = mock_group.call_args
                # We removed dimensions kwarg
                assert "dimensions" not in kwargs

def test_image2_gated_off_for_avif_by_default(monkeypatch):
    """With IMAGE2_ALLOW_LOSSY_FORMATS False, AVIF skips image2 and routes to multimap."""
    monkeypatch.setattr(
        "app.core.converters.ffmpeg_converter.IMAGE2_ALLOW_LOSSY_FORMATS", False
    )
    conv = FFmpegConverter(ffmpeg_path="ffmpeg")
    paths = [f"img_{i}.png" for i in range(5)]

    with patch("app.core.converters.ffmpeg_converter.group_by_dimensions",
               return_value={(100, 100): paths}), \
         patch("app.core.converters.ffmpeg_converter.all_same_resolution",
               return_value=True), \
         patch("app.core.converters.ffmpeg_converter.encoder_params_for",
               return_value=["-c:v", "libaom-av1"]), \
         patch.object(conv, "_run_image2_path",
                      return_value=(0, 0, [], {}, [])) as mock_img2, \
         patch.object(conv, "_run_multimap_path",
                      return_value=(len(paths), 0, [], {})) as mock_multi:
        conv.convert_batch(paths, "out", "avif", [50.0] * len(paths))

    assert not mock_img2.called, "image2 path must stay gated for AVIF when flag is OFF"
    assert mock_multi.called, "multimap must run instead"


def test_image2_enabled_for_avif_when_flag_on(monkeypatch):
    """With IMAGE2_ALLOW_LOSSY_FORMATS True, AVIF enters the image2 staging path."""
    monkeypatch.setattr(
        "app.core.converters.ffmpeg_converter.IMAGE2_ALLOW_LOSSY_FORMATS", True
    )
    conv = FFmpegConverter(ffmpeg_path="ffmpeg")
    paths = [f"img_{i}.png" for i in range(5)]

    with patch("app.core.converters.ffmpeg_converter.group_by_dimensions",
               return_value={(100, 100): paths}), \
         patch("app.core.converters.ffmpeg_converter.all_same_resolution",
               return_value=True), \
         patch("app.core.converters.ffmpeg_converter.encoder_params_for",
               return_value=["-c:v", "libaom-av1"]), \
         patch.object(conv, "_run_image2_path",
                      return_value=(len(paths), 0, [], {}, [])) as mock_img2, \
         patch.object(conv, "_run_multimap_path",
                      return_value=(0, 0, [], {})) as mock_multi:
        conv.convert_batch(paths, "out", "avif", [50.0] * len(paths))

    assert mock_img2.called, "image2 must run for AVIF when flag is ON"
    assert not mock_multi.called, "multimap should not be called with no leftovers"


def test_utils_probe_dimensions_creationflags():
    """Verify that probe_image_dimensions uses CREATE_NO_WINDOW on Windows."""
    if sys.platform != "win32":
        pytest.skip("Windows only test")
        
    from app.core.utils import probe_image_dimensions
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout='{"streams":[{"width":100,"height":100}]}')
        probe_image_dimensions("dummy.png")
        
        # Check creationflags
        _, kwargs = mock_run.call_args
        import subprocess
        assert kwargs.get("creationflags") == subprocess.CREATE_NO_WINDOW
