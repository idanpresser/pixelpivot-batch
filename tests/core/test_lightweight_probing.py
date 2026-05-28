import pytest
from unittest.mock import MagicMock, patch
from app.core.utils import probe_image_dimensions

def test_probe_image_dimensions_ffprobe():
    """
    Verify that probe_image_dimensions uses ffprobe and parses JSON.
    """
    with patch("subprocess.run") as mock_run:
        mock_run.return_value.stdout = '{"streams": [{"width": 1920, "height": 1080}]}'
        mock_run.return_value.returncode = 0
        
        w, h = probe_image_dimensions("test.jpg")
        
        assert w == 1920
        assert h == 1080
        assert "ffprobe" in mock_run.call_args[0][0][0]

def test_probe_image_dimensions_fallback():
    """
    Verify that if ffprobe fails, it falls back to PIL.
    """
    with patch("subprocess.run", side_effect=FileNotFoundError("no ffprobe")), \
         patch("PIL.Image.open") as mock_open:
        
        mock_img = mock_open.return_value.__enter__.return_value
        mock_img.size = (800, 600)
        
        w, h = probe_image_dimensions("test.png")
        
        assert w == 800
        assert h == 600
        assert mock_open.called
