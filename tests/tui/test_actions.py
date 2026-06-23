# tests/tui/test_actions.py
from unittest.mock import MagicMock, patch
from app.tui.state import UiState
from app.tui.actions import submit, control, sharp, save_settings


def test_submit_successful():
    state = UiState(
        source_dir="src",
        target_dir="dst",
        selected_tools=["magick"],
        selected_formats=["webp"]
    )
    api = MagicMock()
    api.start_batch.return_value = {"run_id": 42}
    
    submit(state, api)
    
    assert state.active_run_id == 42
    assert state.active_tab == "telemetry"
    assert state.toast is None
    api.start_batch.assert_called_once_with(
        source_dir="src",
        target_dir="dst",
        tool=["magick"],
        target_format=["webp"],
        category=["general"]
    )


def test_submit_validation_error():
    state = UiState(source_dir="", target_dir="")  # empty dirs
    api = MagicMock()
    
    submit(state, api)
    
    assert state.active_run_id is None
    assert state.active_tab == "submit"
    assert "required" in state.toast
    api.start_batch.assert_not_called()


def test_submit_api_error():
    state = UiState(
        source_dir="src",
        target_dir="dst",
        selected_tools=["magick"],
        selected_formats=["webp"]
    )
    api = MagicMock()
    api.start_batch.side_effect = RuntimeError("API down")
    
    submit(state, api)
    
    assert state.active_run_id is None
    assert "API Error" in state.toast


def test_control_pause_resume():
    state = UiState(active_run_id=42)
    api = MagicMock()
    
    control(state, api, "pause")
    api.control.assert_called_once_with(42, "pause")


def test_control_restart():
    state = UiState(active_run_id=42)
    api = MagicMock()
    api.restart.return_value = {"run_id": 43}
    
    control(state, api, "restart")
    api.restart.assert_called_once_with(42)
    assert state.active_run_id == 43
    assert state.active_tab == "telemetry"


def test_sharp_daemon_actions():
    state = UiState()
    state.settings = {
        "tools": {"sharp_script": "path/to/script.js"},
        "paths": {"sharp_port": 1234}
    }
    supervisor = MagicMock()
    
    # Test stop
    sharp(state, supervisor, "stop")
    supervisor.stop.assert_called_once_with("sharp")
    
    # Test start
    supervisor.reset_mock()
    with patch("app.tui.actions._find_node_cmd", return_value="node"):
        sharp(state, supervisor, "start")
        supervisor.start.assert_called_once()
        cmd = supervisor.start.call_args[0][1]
        assert cmd[0] == "node"
        assert "script.js" in cmd[1]
        assert cmd[2] == "1234"


def test_save_settings_live_vs_restart(tmp_path):
    settings_file = tmp_path / "settings.toml"
    # Write initial settings
    settings_file.write_text("[tools]\nenabled = ['magick']\n[paths]\nsharp_port = 8765\n")
    
    state = UiState()
    # Modify tools.enabled (live-apply) and paths.sharp_port (restart-required)
    state.settings = {
        "tools": {"enabled": ["magick", "ffmpeg"]},
        "paths": {"sharp_port": 9999}
    }
    
    save_settings(state, settings_file)
    
    assert state.enabled_tools == ["magick", "ffmpeg"]
    assert "Restart required" in state.toast
    assert "paths.sharp_port" in state.toast
