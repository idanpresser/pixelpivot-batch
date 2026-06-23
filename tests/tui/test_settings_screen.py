# tests/tui/test_settings_screen.py
from prompt_toolkit.layout.containers import HSplit
from app.tui.state import UiState
from app.tui.screens.settings import create_screen


def test_settings_screen_structure():
    state = UiState()
    state.settings = {
        "api": {"host": "localhost", "port": 8888},
        "paths": {"db": "test.db", "sharp_port": 9999},
        "tools": {"ffmpeg": "ff", "magick": "mag", "enabled": ["magick"]},
        "limits": {"max_workers": 4}
    }
    
    screen = create_screen(state, api=None, supervisor=None)
    assert screen.container is not None
    assert isinstance(screen.container, HSplit)
    
    children = screen.container.children
    assert len(children) >= 10
