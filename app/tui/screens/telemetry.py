# app/tui/screens/telemetry.py
"""Interactive telemetry screen.

Displays a live Rich progress bar using state.progress_cache. Binds control
keys (p=pause, r=resume, s=stop, R=restart) and handles fallback to /batch/status
summary once the run is finalized.
"""
from __future__ import annotations

from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Window
from prompt_toolkit.layout.controls import FormattedTextControl

from app.tui.screens.base import Screen
from app.tui.render import progress_bar
from app.tui.actions import control


def create_screen(state, api, supervisor) -> Screen:
    kb = KeyBindings()

    @kb.add("p")
    def _(event):
        control(state, api, "pause")

    @kb.add("r")
    def _(event):
        control(state, api, "resume")

    @kb.add("s")
    def _(event):
        control(state, api, "stop")

    @kb.add("R")
    def _(event):
        control(state, api, "restart")

    def get_text():
        if state.active_run_id is None or api is None:
            return "No active run. Submit a batch first."

        if state.run_finalized and state.final_status is not None:
            s = state.final_status
            return (
                f"Run {state.active_run_id}: {s.get('status')}\n"
                f"Saved: {s.get('savings_pct', 0.0):.1f}%\n"
                f"Total cells: {s.get('cells_total', 0)}\n"
                f"Result: {s.get('result', '-')}\n\n"
                f"[R] restart"
            )

        try:
            # Try to fetch/render progress
            p_data = state.progress_cache if state.progress_cache else api.get_progress(state.active_run_id)
            return (
                progress_bar(p_data) +
                "\n\n[p] pause  [r] resume  [s] stop  [R] restart"
            )
        except Exception:
            # Fallback to status summary on 404 (finalized)
            try:
                s = api.get_status(state.active_run_id)
                return (
                    f"Run {state.active_run_id}: {s.get('status')}\n"
                    f"Saved: {s.get('savings_pct', 0.0):.1f}%\n"
                    f"Total cells: {s.get('cells_total', 0)}\n"
                    f"Result: {s.get('result', '-')}\n\n"
                    f"[R] restart"
                )
            except Exception as e:
                return f"(progress unavailable: {e})\n\n[R] restart"

    container = Window(FormattedTextControl(get_text))
    return Screen(container, key_bindings=kb)
