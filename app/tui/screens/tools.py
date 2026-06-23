# app/tui/screens/tools.py
from prompt_toolkit.layout import Window
from prompt_toolkit.layout.controls import FormattedTextControl
from app.tui.screens import Screen
from app.tui.render import tools_table
from app.core import toolcheck

def create_screen(state, api, supervisor) -> Screen:
    def get_text():
        statuses = toolcheck.check_all(ffmpeg_path="ffmpeg", magick_path="magick")
        hint = "\n[s] start sharp  [x] stop sharp  [r] restart sharp"
        return tools_table(statuses) + hint

    container = Window(FormattedTextControl(get_text))
    return Screen(container)
