# tests/tui/test_tools_screen.py
from unittest.mock import patch
from app.tui.state import UiState
from app.tui.screens.tools import create_screen


def test_tools_screen_renders_health_statuses():
    state = UiState()
    state.settings = {
        "tools": {"ffmpeg": "custom-ffmpeg", "magick": "custom-magick"}
    }
    
    from app.core.toolcheck import ToolStatus
    mock_statuses = [
        ToolStatus("magick", True, "7.1", "/x"),
        ToolStatus("ffmpeg", True, "6.0", "/y"),
    ]
    
    with patch("app.core.toolcheck.check_all", return_value=mock_statuses) as mock_check:
        screen = create_screen(state, api=None, supervisor=None)
        text = screen.container.content.text()
        
        mock_check.assert_called_once_with(ffmpeg_path="custom-ffmpeg", magick_path="custom-magick", cavif_path="cavif")
        
        assert "magick" in text
        assert "ffmpeg" in text
        assert "OK" in text
        assert "[s] start sharp" in text
