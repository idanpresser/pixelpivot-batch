# tests/tui/test_widgets.py
from app.tui.state import UiState
from app.tui.widgets import checkbox_list_wrapper, path_field


def test_checkbox_list_wrapper_toggling():
    state = UiState()
    
    # Create checkbox list for formats
    cb = checkbox_list_wrapper(
        values=[("webp", "WebP"), ("avif", "AVIF"), ("jxl", "JXL")],
        on_change=lambda vals: setattr(state, "selected_formats", vals),
        initial_values=["avif"]
    )
    
    assert cb.current_values == ["avif"]
    
    # Toggle 'webp' (first item)
    cb._selected_index = 0
    cb._handle_enter()
    
    # Check that state is updated
    assert "webp" in state.selected_formats
    assert "avif" in state.selected_formats
    assert set(cb.current_values) == {"avif", "webp"}


def test_path_field_text_changed():
    state = UiState()
    
    field = path_field(
        label_text="Source",
        text_changed_handler=lambda val: setattr(state, "source_dir", val),
        initial_text="/init"
    )
    
    assert field.text_area.text == "/init"
    
    # Simulate text change in buffer
    field.text_area.buffer.text = "/new/path"
    
    assert state.source_dir == "/new/path"
