# app/tui/screens/tools.py
"""Interactive tools screen.

Displays live health statuses for conversion backends (imagemagick, ffmpeg, vips,
sharp). Binds control keys (s=start sharp, x=stop sharp, r=restart sharp) to
manage the sharp node daemon process via supervisor.
"""
from __future__ import annotations

from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Window
from prompt_toolkit.layout.controls import FormattedTextControl

from app.tui.screens.base import Screen
from app.tui.render import tools_table
from app.core import toolcheck
from app.tui.actions import sharp


def create_screen(state, api, supervisor) -> Screen:
    kb = KeyBindings()

    @kb.add("s")
    def _(event):
        sharp(state, supervisor, "start")

    @kb.add("x")
    def _(event):
        sharp(state, supervisor, "stop")

    @kb.add("r")
    def _(event):
        sharp(state, supervisor, "restart")

    def get_text():
        settings = state.settings or {}
        ffmpeg_path = settings.get("tools", {}).get("ffmpeg", "") or "ffmpeg"
        magick_path = settings.get("tools", {}).get("magick", "") or "magick"

        statuses = toolcheck.check_all(ffmpeg_path=ffmpeg_path, magick_path=magick_path)
        hint = "\n[s] start sharp  [x] stop sharp  [r] restart sharp"
        return tools_table(statuses) + hint

    container = Window(FormattedTextControl(get_text))
    return Screen(container, key_bindings=kb)
