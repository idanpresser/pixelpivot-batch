# app/tui/screens/history.py
from prompt_toolkit.layout import Window
from prompt_toolkit.layout.controls import FormattedTextControl
from app.tui.screens import Screen
from app.tui.render import history_table

def create_screen(state, api, supervisor) -> Screen:
    def get_text():
        if api is None:
            return "(no api)"
        try:
            return history_table(api.get_history())
        except Exception as e:
            return f"(history unavailable: {e})"

    container = Window(FormattedTextControl(get_text))
    return Screen(container)
