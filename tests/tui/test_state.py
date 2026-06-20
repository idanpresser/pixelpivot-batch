import pytest
from app.tui.state import UiState, build_batch_payload

def test_toggle_tool_respects_enabled():
    s = UiState(enabled_tools=["magick", "ffmpeg"])
    s.toggle_tool("magick")
    assert "magick" in s.selected_tools
    s.toggle_tool("magick")
    assert "magick" not in s.selected_tools

def test_toggle_disabled_tool_is_noop():
    s = UiState(enabled_tools=["ffmpeg"])
    s.toggle_tool("vips")          # not enabled
    assert "vips" not in s.selected_tools

def test_build_payload_requires_selections():
    s = UiState(enabled_tools=["magick"])
    s.source_dir = "/s"; s.target_dir = "/d"
    with pytest.raises(ValueError):
        build_batch_payload(s)     # no tool/format selected
    s.toggle_tool("magick"); s.toggle_format("webp")
    payload = build_batch_payload(s)
    assert payload["tool"] == ["magick"]
    assert payload["target_format"] == ["webp"]
    assert payload["category"] == ["general"]
