"""Tray settings persistence + env-var bridge — pure logic, no Qt required.

Covers _Settings (JSON round-trip, defaults, corruption fallback) and the
SETTINGS_ENV_MAP / SETTINGS_DEFAULTS contract shared with service_main.
"""
from __future__ import annotations

import json
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="tray is win32-only")

from app.windows import tray as tray_mod
from app.windows._settings import SETTINGS_DEFAULTS, SETTINGS_ENV_MAP


def test_load_returns_defaults_when_file_absent(tmp_path):
    s = tray_mod._Settings(tmp_path)
    data = s.load()
    assert data == SETTINGS_DEFAULTS
    # must be a copy, not the shared module dict
    assert data is not SETTINGS_DEFAULTS


def test_save_then_load_round_trips(tmp_path):
    s = tray_mod._Settings(tmp_path)
    data = s.load()
    data["disk_backpressure_pct"] = 75.0
    data["calibration_enabled"] = True
    s.save(data)

    loaded = tray_mod._Settings(tmp_path).load()
    assert loaded["disk_backpressure_pct"] == 75.0
    assert loaded["calibration_enabled"] is True


def test_load_merges_partial_file_over_defaults(tmp_path):
    (tmp_path / "pixelpivot_config.json").write_text(
        json.dumps({"queue_poll_s": 2.5})
    )
    loaded = tray_mod._Settings(tmp_path).load()
    assert loaded["queue_poll_s"] == 2.5
    # untouched keys fall back to defaults
    assert loaded["chunk_ram_fraction"] == SETTINGS_DEFAULTS["chunk_ram_fraction"]


def test_corrupt_json_falls_back_to_defaults(tmp_path):
    (tmp_path / "pixelpivot_config.json").write_text("{ not valid json")
    loaded = tray_mod._Settings(tmp_path).load()
    assert loaded == SETTINGS_DEFAULTS


def test_save_creates_parent_dir(tmp_path):
    nested = tmp_path / "data"
    s = tray_mod._Settings(nested)
    s.save(dict(SETTINGS_DEFAULTS))
    assert (nested / "pixelpivot_config.json").exists()


def test_env_map_keys_match_defaults():
    # Every persisted setting must have an env-var target, and vice versa,
    # or service_main._apply_saved_settings would silently drop a value.
    assert set(SETTINGS_ENV_MAP) == set(SETTINGS_DEFAULTS)


def test_env_vars_are_pixelpivot_prefixed():
    assert all(v.startswith("PIXELPIVOT_") for v in SETTINGS_ENV_MAP.values())


def test_save_is_atomic(tmp_path, monkeypatch):
    import os
    s = tray_mod._Settings(tmp_path)
    
    replace_calls = []
    original_replace = os.replace
    def mock_replace(src, dst):
        replace_calls.append((src, dst))
        original_replace(src, dst)
        
    monkeypatch.setattr(os, "replace", mock_replace)
    
    s.save(dict(SETTINGS_DEFAULTS))
    
    assert len(replace_calls) == 1
    src, dst = replace_calls[0]
    assert src.name.endswith(".tmp")
    assert dst == s._path
    assert s._path.exists()


def test_service_main_malformed_config_logs_warning(monkeypatch):
    from unittest.mock import MagicMock
    from app.windows import service_main
    
    mock_path = MagicMock()
    mock_path.exists.return_value = True
    mock_path.read_text.return_value = "{ corrupt json"
    
    mock_path.__truediv__.return_value = mock_path
    monkeypatch.setattr("app.windows._settings.resolve_data_dir", lambda: mock_path)
    
    warning_msgs = []
    import sys
    mock_servicemanager = MagicMock()
    mock_servicemanager.LogWarningMsg = lambda msg: warning_msgs.append(msg)
    sys.modules["servicemanager"] = mock_servicemanager
    
    stderr_writes = []
    monkeypatch.setattr(sys.stderr, "write", lambda msg: stderr_writes.append(msg))
    
    service_main._apply_saved_settings()
    
    assert len(warning_msgs) == 1
    assert "Failed to parse settings file" in warning_msgs[0]
    
    assert len(stderr_writes) > 0
    assert any("Failed to parse settings file" in w for w in stderr_writes)


def test_resolve_data_dir(monkeypatch):
    from app.windows._settings import resolve_data_dir
    from pathlib import Path
    
    monkeypatch.delenv("PIXELPIVOT_DATA_DIR", raising=False)
    default_dir = resolve_data_dir()
    assert default_dir.name == "data"
    
    monkeypatch.setenv("PIXELPIVOT_DATA_DIR", "C:/custom_data_dir")
    custom_dir = resolve_data_dir()
    assert custom_dir == Path("C:/custom_data_dir")
