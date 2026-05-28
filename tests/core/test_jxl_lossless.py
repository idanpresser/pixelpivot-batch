import pytest
from unittest.mock import MagicMock, patch
from app.core.converters.ffmpeg_converter import FFmpegConverter
from app.core.converters.magick_converter import MagickConverter
from app.core.converters.vips_converter import VipsConverter

def test_jxl_lossless_ffmpeg():
    """
    Verify FFmpeg lossless JXL flags.
    """
    conv = FFmpegConverter(ffmpeg_path="ffmpeg")
    with patch.object(conv, "_run_ffmpeg") as mock_run:
        conv.convert("in.jpg", "out.jxl", "jxl", 100)
        params = mock_run.call_args[0][1]
        
        assert "-distance" in params
        assert params[params.index("-distance")+1] == "0.0"

def test_jxl_lossless_vips():
    """
    Verify Vips lossless JXL call.
    """
    conv = VipsConverter()
    with patch("app.core.converters.vips_converter.pyvips") as mock_vips:
        mock_img = mock_vips.Image.new_from_file.return_value
        
        conv.convert("in.jpg", "out.jxl", "jxl", 100)
        
        # Check jxlsave kwargs
        args, kwargs = mock_img.jxlsave.call_args
        assert kwargs.get("lossless") is True
        # For lossless, distance should be 0 or omitted
        assert kwargs.get("distance", 0) == 0

def test_jxl_lossless_magick():
    """
    Verify Magick lossless JXL flags.
    """
    conv = MagickConverter(magick_path="magick")
    with patch.object(conv, "_run_subprocess") as mock_run:
        conv.convert("in.jpg", "out.jxl", "jxl", 100)
        cmd = mock_run.call_args[0][0]
        
        # ImageMagick uses -define jxl:distance=0.0 for lossless
        found = False
        for arg in cmd:
            if "jxl:distance=0.0" in arg:
                found = True
                break
        assert found
