import sys
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path

from app.core.converters.base import ConvertResult

def test_cavif_metadata():
    from app.core.converters.cavif_converter import CavifConverter
    conv = CavifConverter(cavif_path="/usr/bin/cavif")
    assert conv.get_name() == "cavif"
    assert conv.supported_formats() == ["avif"]

def test_cavif_rejects_non_avif():
    from app.core.converters.cavif_converter import CavifConverter
    conv = CavifConverter(cavif_path="/usr/bin/cavif")
    with pytest.raises(ValueError, match="CavifConverter only supports 'avif'"):
        conv.convert("input.png", "output.webp", "webp", 80)

@patch("app.core.converters.base.subprocess.Popen")
@patch("app.core.converters.base.TelemetryMonitor")
def test_cavif_convert_args(mock_monitor, mock_popen):
    from app.core.converters.cavif_converter import CavifConverter
    
    # Mock Popen return value
    mock_process = MagicMock()
    mock_process.returncode = 0
    mock_process.communicate.return_value = ("", "")
    mock_popen.return_value.__enter__.return_value = mock_process
    
    # Mock os.path.exists to simulate successful output creation
    with patch("app.core.converters.base.os.path.exists", return_value=True), \
         patch("app.core.converters.base.os.path.getsize", return_value=1234):
        
        conv = CavifConverter(cavif_path="/usr/bin/cavif")
        res = conv.convert("input.png", "output.avif", "avif", 80, run_id=99)
        
        assert res.success is True
        
        # Verify the command called is correct
        expected_cmd = [
            "/usr/bin/cavif",
            "--quality", "80",
            "-o", "output.avif",
            "input.png"
        ]
        mock_popen.assert_called_once()
        called_cmd = mock_popen.call_args[0][0]
        assert called_cmd == expected_cmd

@patch("app.core.converters.base.subprocess.Popen")
@patch("app.core.converters.base.TelemetryMonitor")
def test_cavif_convert_batch(mock_monitor, mock_popen):
    from app.core.converters.cavif_converter import CavifConverter
    
    # Mock single conversions
    mock_process = MagicMock()
    mock_process.returncode = 0
    mock_process.communicate.return_value = ("", "")
    mock_popen.return_value.__enter__.return_value = mock_process
    
    with patch("app.core.converters.base.os.path.exists", return_value=True), \
         patch("app.core.converters.base.os.path.getsize", return_value=1234):
        
        conv = CavifConverter(cavif_path="/usr/bin/cavif")
        res = conv.convert_batch(
            input_paths=["img1.png", "img2.png"],
            output_dir="out",
            target_format="avif",
            qualities=[80.0, 80.0],
            run_id=99
        )
        
        assert res.success_count == 2
        assert res.failure_count == 0
        assert mock_popen.call_count == 2
