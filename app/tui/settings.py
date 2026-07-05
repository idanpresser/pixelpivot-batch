# app/tui/settings.py
"""TUI settings: defaults, file<-env<-default precedence, and a tiny TOML writer.

Read uses stdlib tomllib (3.11+). Write uses an in-repo serializer to avoid a
new dependency (air-gap constraint). Precedence (highest first): file, env,
DEFAULTS. Each key is classified 'live' (applied immediately) or 'restart'
(needs API/daemon restart).
"""
from __future__ import annotations

import copy
import os
import tomllib
from pathlib import Path
from typing import Any, Dict

DEFAULTS: Dict[str, Dict[str, Any]] = {
    "api":      {"host": "127.0.0.1", "port": 8000},
    "paths":    {"db": "./data/pixelpivot.db", "sharp_port": 8765},
    "tools":    {"ffmpeg": "", "magick": "",
                 "sharp_script": "services/sharp-daemon/sharp_daemon.js",
                 "enabled": ["magick", "ffmpeg", "vips", "sharp"]},
    "security": {"allowed_root": ""},
    "limits":   {"max_workers": 0},
    "batch":    {"default_tool": "ffmpeg", "default_format": "avif", "default_quality": 90},
}

# (section, key) -> env var that may override the default.
_ENV_MAP = {
    ("paths", "db"): "PIXELPIVOT_DB_PATH",
    ("security", "allowed_root"): "PIXELPIVOT_ALLOWED_ROOT",
    ("limits", "max_workers"): "PIXELPIVOT_CONCURRENT_ENCODES_MAX_WORKERS",
}

# Keys safe to apply without restarting the API/daemon.
_LIVE = {("batch", "default_tool"), ("batch", "default_format"),
         ("batch", "default_quality"), ("security", "allowed_root"),
         ("tools", "enabled")}


def classify(section: str, key: str) -> str:
    return "live" if (section, key) in _LIVE else "restart"


def _apply_env(cfg: Dict[str, Dict[str, Any]]) -> None:
    for (section, key), env in _ENV_MAP.items():
        val = os.getenv(env)
        if val is None:
            continue
        if isinstance(DEFAULTS[section][key], int):
            try:
                val = int(val)
            except ValueError:
                continue
        cfg[section][key] = val


def _deep_merge(base: Dict[str, Any], overlay: Dict[str, Any]) -> None:
    for k, v in overlay.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v


def load_settings(path: str | Path) -> Dict[str, Dict[str, Any]]:
    cfg = copy.deepcopy(DEFAULTS)
    _apply_env(cfg)
    p = Path(path)
    if p.exists():
        with open(p, "rb") as f:
            _deep_merge(cfg, tomllib.load(f))
    return cfg


def _fmt(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, list):
        return "[" + ", ".join(_fmt(x) for x in v) + "]"
    return '"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"'


def dumps_toml(cfg: Dict[str, Dict[str, Any]]) -> str:
    lines: list[str] = []
    for section, body in cfg.items():
        lines.append(f"[{section}]")
        for key, val in body.items():
            lines.append(f"{key} = {_fmt(val)}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def save_settings(path: str | Path, cfg: Dict[str, Dict[str, Any]]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(dumps_toml(cfg), encoding="utf-8")
