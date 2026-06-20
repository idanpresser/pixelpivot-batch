"""prompt_toolkit application shell: tab bar, body, log panel, status bar.

build_application() wires the layout and keybindings against a UiState, an
optional TuiApiClient, and an optional ProcessSupervisor (both optional so the
smoke test can build the app without a backend).
"""
from __future__ import annotations

from prompt_toolkit.application import Application
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout, HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl

from app.tui.state import UiState, TABS
from app.tui.screens import RENDERERS


def build_application(state: UiState, api=None, supervisor=None) -> Application:
    def tab_bar() -> ANSI:
        return ANSI(" | ".join((f"*{t}*" if t == state.active_tab else t) for t in TABS))

    def body() -> ANSI:
        return ANSI(RENDERERS[state.active_tab](state, api, supervisor))

    def log_panel() -> ANSI:
        lines = supervisor.get_logs()[-12:] if supervisor else []
        return ANSI("\n".join(lines))

    def status_bar() -> ANSI:
        st = supervisor.status() if supervisor else {}
        run = state.active_run_id if state.active_run_id is not None else "-"
        return ANSI(f"API:{st.get('api','?')} SHARP:{st.get('sharp','?')} run:{run}  [Tab] switch  [q] quit")

    kb = KeyBindings()

    @kb.add("tab")
    def _(event):
        i = TABS.index(state.active_tab)
        state.active_tab = TABS[(i + 1) % len(TABS)]

    @kb.add("q")
    def _(event):
        event.app.exit()

    root = HSplit([
        Window(FormattedTextControl(tab_bar), height=1),
        Window(FormattedTextControl(body)),
        Window(FormattedTextControl(log_panel), height=12),
        Window(FormattedTextControl(status_bar), height=1),
    ])
    app = Application(layout=Layout(root), key_bindings=kb, full_screen=True)
    app.state = state          # attach for tests and screen callbacks
    return app
