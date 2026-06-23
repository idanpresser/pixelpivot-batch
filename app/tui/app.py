# app/tui/app.py
"""prompt_toolkit application shell: tab bar, body, log panel, status bar, toast.

build_application() wires the layout and keybindings against a UiState, an
optional TuiApiClient, and an optional ProcessSupervisor.
"""
from __future__ import annotations

import threading
import time
import weakref

from prompt_toolkit.application import Application
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.key_binding import KeyBindings, merge_key_bindings, KeyBindingsBase
from prompt_toolkit.layout import Layout, HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.containers import DynamicContainer
from prompt_toolkit.filters import Condition
from prompt_toolkit.application.current import get_app
from prompt_toolkit.layout.controls import BufferControl

from app.tui.state import UiState, TABS
from app.tui.screens import get_screens, Screen
from app.tui.widgets import toast_area


class ActiveScreenKeyBindings(KeyBindingsBase):
    def __init__(self, state: UiState, screens: dict[str, Screen]):
        self.state = state
        self.screens = screens

    def get_bindings_for_keys(self, keys):
        screen = self.screens.get(self.state.active_tab)
        if screen and screen.key_bindings:
            return screen.key_bindings.get_bindings_for_keys(keys)
        return []

    def get_bindings_starting_with_keys(self, keys):
        screen = self.screens.get(self.state.active_tab)
        if screen and screen.key_bindings:
            return screen.key_bindings.get_bindings_starting_with_keys(keys)
        return []

    @property
    def bindings(self):
        screen = self.screens.get(self.state.active_tab)
        if screen and screen.key_bindings:
            return screen.key_bindings.bindings
        return []

    @property
    def _version(self):
        screen = self.screens.get(self.state.active_tab)
        if screen and screen.key_bindings:
            return screen.key_bindings._version
        return 0


def build_application(state: UiState, api=None, supervisor=None) -> Application:
    # 1. Instantiate the screens
    screens = get_screens(state, api, supervisor)

    # 2. Tab Bar implementation
    tab_bar_control = FormattedTextControl(focusable=True)
    
    def get_tab_bar_text():
        app = get_app()
        is_focused = (app.layout.current_control == tab_bar_control) if app else False
        prefix = "[Focus: Tab Bar] " if is_focused else ""
        parts = []
        for t in TABS:
            if t == state.active_tab:
                parts.append(f"*{t}*")
            else:
                parts.append(t)
        return ANSI(prefix + " | ".join(parts))

    tab_bar_control.text = get_tab_bar_text
    tab_bar_window = Window(tab_bar_control, height=1)

    # 3. Body Container implementation
    def get_body_container():
        screen = screens.get(state.active_tab)
        if screen:
            return screen.container
        return Window(FormattedTextControl(ANSI("Screen not found")))

    body_container = DynamicContainer(get_body_container)

    # 4. Log Panel implementation
    def log_panel() -> ANSI:
        lines = supervisor.get_logs()[-12:] if supervisor else []
        return ANSI("\n".join(lines))

    # 5. Status Bar implementation
    def status_bar() -> ANSI:
        st = supervisor.status() if supervisor else {}
        run = state.active_run_id if state.active_run_id is not None else "-"
        return ANSI(f"API:{st.get('api','?')} SHARP:{st.get('sharp','?')} run:{run}  [Tab] next field  [Esc] defocus  [Ctrl-Q] quit")

    # 6. Toast Line implementation
    toast_win = toast_area(state)

    # 7. Global Key Bindings
    kb = KeyBindings()

    @Condition
    def is_not_editable_focused():
        app = get_app()
        if not app:
            return True
        control = app.layout.current_control
        if isinstance(control, BufferControl):
            return control.buffer.read_only()
        return True

    # 1-5 jump to tabs (guarded by focus filter)
    @kb.add("1", filter=is_not_editable_focused)
    def _(event):
        state.active_tab = TABS[0]

    @kb.add("2", filter=is_not_editable_focused)
    def _(event):
        state.active_tab = TABS[1]

    @kb.add("3", filter=is_not_editable_focused)
    def _(event):
        state.active_tab = TABS[2]

    @kb.add("4", filter=is_not_editable_focused)
    def _(event):
        state.active_tab = TABS[3]

    @kb.add("5", filter=is_not_editable_focused)
    def _(event):
        state.active_tab = TABS[4]

    # Tab/Shift-Tab move focus
    @kb.add("tab")
    def _(event):
        event.app.layout.focus_next()

    @kb.add("s-tab")
    def _(event):
        event.app.layout.focus_previous()

    # Esc moves focus to tab bar
    @kb.add("escape")
    def _(event):
        event.app.layout.focus(tab_bar_control)

    # q quit (guarded)
    @kb.add("q", filter=is_not_editable_focused)
    def _(event):
        event.app.exit()

    # Ctrl-Q always quits
    @kb.add("c-q")
    def _(event):
        event.app.exit()

    # 8. Layout Construction
    root = HSplit([
        tab_bar_window,
        body_container,
        Window(FormattedTextControl(log_panel), height=12),
        toast_win,
        Window(FormattedTextControl(status_bar), height=1),
    ])

    # 9. App construction
    merged_kb = merge_key_bindings([kb, ActiveScreenKeyBindings(state, screens)])
    app = Application(
        layout=Layout(root),
        key_bindings=merged_kb,
        full_screen=True,
        refresh_interval=1.0
    )
    app.state = state

    # 10. Background Poller Thread with weakref to avoid leaking/blocking on GC
    def poll_loop():
        ref = weakref.ref(app)
        has_started = False
        while True:
            a = ref()
            if a is None:
                break
            
            if a.is_running:
                has_started = True
            elif has_started:
                break
                
            if a.is_running and state.active_run_id is not None and api is not None and not state.run_finalized:
                try:
                    progress = api.get_progress(state.active_run_id)
                    state.progress_cache = progress
                    a.invalidate()
                except Exception:
                    try:
                        s = api.get_status(state.active_run_id)
                        if s and s.get("status") in ("completed", "failed", "cancelled"):
                            state.run_finalized = True
                            state.final_status = s
                            state.progress_cache = {}
                            a.invalidate()
                    except Exception:
                        pass
            time.sleep(1.0)

    poller_thread = threading.Thread(target=poll_loop, daemon=True)
    poller_thread.start()

    return app
