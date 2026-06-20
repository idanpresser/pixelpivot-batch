# tests/tui/test_settings.py
from app.tui.settings import (
    DEFAULTS, load_settings, save_settings, classify, dumps_toml,
)

def test_defaults_when_no_file(tmp_path):
    cfg = load_settings(tmp_path / "settings.toml")
    assert cfg["api"]["port"] == DEFAULTS["api"]["port"]

def test_env_overrides_default(tmp_path, monkeypatch):
    monkeypatch.setenv("PIXELPIVOT_DB_PATH", "/env/db.sqlite")
    cfg = load_settings(tmp_path / "settings.toml")
    assert cfg["paths"]["db"] == "/env/db.sqlite"

def test_file_overrides_env(tmp_path, monkeypatch):
    monkeypatch.setenv("PIXELPIVOT_DB_PATH", "/env/db.sqlite")
    path = tmp_path / "settings.toml"
    path.write_text('[paths]\ndb = "/file/db.sqlite"\n', encoding="utf-8")
    cfg = load_settings(path)
    assert cfg["paths"]["db"] == "/file/db.sqlite"

def test_roundtrip_save_load(tmp_path):
    path = tmp_path / "settings.toml"
    cfg = load_settings(path)
    cfg["batch"]["default_format"] = "jxl"
    save_settings(path, cfg)
    assert load_settings(path)["batch"]["default_format"] == "jxl"

def test_classify_live_vs_restart():
    assert classify("batch", "default_format") == "live"
    assert classify("api", "port") == "restart"

def test_dumps_toml_handles_types():
    out = dumps_toml({"s": {"a": "x", "b": 3, "c": 1.5, "d": True, "e": ["m", "n"]}})
    assert '[s]' in out and 'a = "x"' in out and 'b = 3' in out
    assert 'd = true' in out and 'e = ["m", "n"]' in out
