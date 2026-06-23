# app/tui/screens/settings.py
"""Interactive settings screen.

Allows editing settings parameters (API host/port, database path, daemon port,
tool binary paths, and max workers limit) via TextAreas, and enabled tools via
a CheckboxList. Wires the Save button to save_settings.
"""
from __future__ import annotations

import copy
from prompt_toolkit.layout import HSplit, VSplit, Window
from prompt_toolkit.widgets import Frame, TextArea, Label

from app.tui.screens.base import Screen
from app.tui.widgets import checkbox_list_wrapper, button
from app.tui.actions import save_settings
from app.core.paths import PROJ_ROOT


def create_screen(state, api, supervisor) -> Screen:
    settings = state.settings
    if not settings:
        from app.tui.settings import DEFAULTS
        state.settings = copy.deepcopy(DEFAULTS)
        settings = state.settings

    def make_field(label_text: str, section: str, key: str, is_int: bool = False):
        val = str(settings.get(section, {}).get(key, ""))
        ta = TextArea(text=val, multiline=False)

        def on_change(buffer):
            v = buffer.text
            if is_int:
                try:
                    state.settings[section][key] = int(v)
                except ValueError:
                    pass
            else:
                state.settings[section][key] = v

        ta.buffer.on_text_changed += on_change
        return VSplit([Label(text=label_text, width=16), ta], padding=1)

    api_host = make_field("API Host:", "api", "host")
    api_port = make_field("API Port:", "api", "port", is_int=True)
    db_path = make_field("Database Path:", "paths", "db")
    sharp_port = make_field("Sharp Port:", "paths", "sharp_port", is_int=True)
    ffmpeg_path = make_field("FFmpeg Path:", "tools", "ffmpeg")
    magick_path = make_field("Magick Path:", "tools", "magick")
    max_workers = make_field("Max Workers:", "limits", "max_workers", is_int=True)

    all_tools = [("magick", "Magick"), ("ffmpeg", "FFmpeg"), ("vips", "Vips"), ("sharp", "Sharp")]
    enabled_cb = checkbox_list_wrapper(
        values=all_tools,
        on_change=lambda vals: state.settings.get("tools", {}).update({"enabled": list(vals)}),
        initial_values=settings.get("tools", {}).get("enabled", [])
    )

    save_btn = button(
        "Save Settings",
        lambda: save_settings(state, PROJ_ROOT / "data" / "settings.toml")
    )

    container = HSplit([
        api_host,
        api_port,
        db_path,
        sharp_port,
        ffmpeg_path,
        magick_path,
        max_workers,
        Window(height=1),
        Frame(title="Enabled Tools (Live Apply)", body=enabled_cb),
        Window(height=1),
        save_btn,
    ])

    return Screen(container)
