import pytest
from unittest.mock import MagicMock, patch
from app.core.converters.ffmpeg_converter import FFmpegConverter
from app.core.converters.magick_converter import MagickConverter

def test_jxl_quality_mapping_ffmpeg():
    """
    Verify that FFmpegConverter maps quality to distance for JXL.
    """
    conv = FFmpegConverter(ffmpeg_path="ffmpeg")
    
    # Mock _run_ffmpeg to check args
    with patch.object(conv, "_run_ffmpeg") as mock_run:
        conv.convert("in.jpg", "out.jxl", "jxl", 90)
        
        # Check args passed to _run_ffmpeg
        args, kwargs = mock_run.call_args
        params = args[1] # second arg is params
        
        # Should contain -distance 1.0
        assert "-distance" in params
        idx = params.index("-distance")
        assert params[idx+1] == "1.0"

def test_jxl_quality_mapping_magick():
    """
    Verify that MagickConverter maps quality to distance for JXL.
    """
    conv = MagickConverter(magick_path="magick")
    
    # Mock _run_subprocess to check cmd
    with patch.object(conv, "_run_subprocess") as mock_run:
        conv.convert("in.jpg", "out.jxl", "jxl", 90)
        
        args, kwargs = mock_run.call_args
        cmd = args[0]
        
        # ImageMagick uses -define jxl:distance=1.0 OR just -quality mapped correctly.
        # But if we use -quality, ImageMagick might do its own mapping.
        # The task says "pass -define jxl:distance=1.0"
        
        found = False
        for arg in cmd:
            if "jxl:distance=1.0" in arg:
                found = True
                break
        assert found

def test_jxl_quality_extremes():
    """
    Verify mapping for 100 and 0 quality.
    """
    from app.core.converters.ffmpeg_converter import FFmpegConverter
    conv = FFmpegConverter(ffmpeg_path="ffmpeg")
    
    with patch.object(conv, "_run_ffmpeg") as mock_run:
        # Quality 100 -> Distance 0.0
        conv.convert("in.jpg", "out.jxl", "jxl", 100)
        params = mock_run.call_args[0][1]
        assert params[params.index("-distance")+1] == "0.0"
        
        # Quality 0 -> Distance 10.0
        conv.convert("in.jpg", "out.jxl", "jxl", 0)
        params = mock_run.call_args[0][1]
        assert params[params.index("-distance")+1] == "10.0"
