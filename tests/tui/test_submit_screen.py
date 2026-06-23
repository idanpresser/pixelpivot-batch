# tests/tui/test_submit_screen.py
from prompt_toolkit.layout.containers import HSplit
from app.tui.state import UiState
from app.tui.screens.submit import create_screen


def test_submit_screen_structure():
    state = UiState()
    screen = create_screen(state, api=None, supervisor=None)
    
    assert screen.container is not None
    assert isinstance(screen.container, HSplit)
    
    children = screen.container.children
    # We should have at least 5 layout nodes stacked vertically:
    # Source Dir, Target Dir, spacing, VSplit (Tools/Formats), spacing, Button
    assert len(children) >= 5
