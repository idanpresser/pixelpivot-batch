# app/tui/screens/settings.py
from prompt_toolkit.layout import Window
from prompt_toolkit.layout.controls import FormattedTextControl
from app.tui.screens import Screen

def create_screen(state, api, supervisor) -> Screen:
    def get_text():
        return "Settings: edit in settings.toml (PROJ_ROOT/data/settings.toml).\nLive keys apply on save; others need restart."

    container = Window(FormattedTextControl(get_text))
    return Screen(container)
