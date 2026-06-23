# app/tui/screens/telemetry.py
from prompt_toolkit.layout import Window
from prompt_toolkit.layout.controls import FormattedTextControl
from app.tui.screens import Screen
from app.tui.render import progress_table

def create_screen(state, api, supervisor) -> Screen:
    def get_text():
        if state.active_run_id is None or api is None:
            return "No active run. Submit a batch first."
        try:
            return progress_table(api.get_progress(state.active_run_id))
        except Exception:
            try:
                s = api.get_status(state.active_run_id)
                return f"Run {state.active_run_id}: {s.get('status')}"
            except Exception as e:
                return f"(progress unavailable: {e})"

    container = Window(FormattedTextControl(get_text))
    return Screen(container)
