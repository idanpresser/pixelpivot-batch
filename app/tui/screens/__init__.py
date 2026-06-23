# app/tui/screens/__init__.py
from __future__ import annotations

from typing import Any, Dict
from .base import Screen
from . import submit, telemetry, history, tools, settings

def get_screens(state: Any, api: Any, supervisor: Any) -> Dict[str, Screen]:
    return {
        "submit": submit.create_screen(state, api, supervisor),
        "telemetry": telemetry.create_screen(state, api, supervisor),
        "history": history.create_screen(state, api, supervisor),
        "tools": tools.create_screen(state, api, supervisor),
        "settings": settings.create_screen(state, api, supervisor),
    }
