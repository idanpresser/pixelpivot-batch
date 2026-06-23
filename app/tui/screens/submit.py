# app/tui/screens/submit.py
from prompt_toolkit.layout import Window
from prompt_toolkit.layout.controls import FormattedTextControl
from app.tui.screens import Screen
from app.tui.state import UiState, FORMATS

def create_screen(state: UiState, api, supervisor) -> Screen:
    def get_text():
        tools = " ".join(f"[{'x' if t in state.selected_tools else ' '}]{t}" for t in state.enabled_tools)
        fmts = " ".join(f"[{'x' if f in state.selected_formats else ' '}]{f}" for f in FORMATS)
        return (f"Source: {state.source_dir or '<unset>'}\n"
                f"Target: {state.target_dir or '<unset>'}\n"
                f"Tools:   {tools}\nFormats: {fmts}\n"
                f"Category: {state.category}\n\n[Enter] start batch")

    container = Window(FormattedTextControl(get_text))
    return Screen(container)
