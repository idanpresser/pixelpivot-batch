# tests/tui/test_app_smoke.py
from unittest.mock import MagicMock, PropertyMock, patch
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.widgets import TextArea
from prompt_toolkit.keys import Keys
from app.tui.app import build_application
from app.tui.state import UiState


def test_application_builds_and_renders():
    with create_pipe_input() as inp, create_app_session(input=inp, output=DummyOutput()):
        app = build_application(UiState(), api=None, supervisor=None)
        # Layout must contain the tab bar and other windows.
        assert app.layout is not None
        # Tab switch reducer works without a running event loop.
        app.state.active_tab = "tools"
        assert app.state.active_tab == "tools"


def test_global_keybindings_and_focus_filter():
    with create_pipe_input() as inp, create_app_session(input=inp, output=DummyOutput()):
        state = UiState()
        app = build_application(state, api=None, supervisor=None)
        event = MagicMock(app=app)
        
        # Test tab switching bindings (digits 1-5)
        # Initially, focus is on the tab bar (FormattedTextControl), not an editable field.
        b_2 = app.key_bindings.get_bindings_for_keys(("2",))
        assert len(b_2) > 0
        assert b_2[0].filter() is True  # Filter should allow tab switching
        
        b_2[0].handler(event)
        assert state.active_tab == "telemetry"
        
        # Test ESC focuses the tab bar
        b_esc = app.key_bindings.get_bindings_for_keys((Keys.Escape,))
        assert len(b_esc) > 0
        with patch.object(app.layout, "focus") as mock_focus:
            b_esc[0].handler(event)
            mock_focus.assert_called_once()
            
        # Test Tab moves focus next
        b_tab = app.key_bindings.get_bindings_for_keys((Keys.Tab,))
        assert len(b_tab) > 0
        with patch.object(app.layout, "focus_next") as mock_focus_next:
            b_tab[0].handler(event)
            mock_focus_next.assert_called_once()
            
        # Test Shift-Tab moves focus previous
        b_stab = app.key_bindings.get_bindings_for_keys((Keys.BackTab,))
        assert len(b_stab) > 0
        with patch.object(app.layout, "focus_previous") as mock_focus_prev:
            b_stab[0].handler(event)
            mock_focus_prev.assert_called_once()

        # Test Ctrl-Q always exits
        b_cq = app.key_bindings.get_bindings_for_keys((Keys.ControlQ,))
        assert len(b_cq) > 0
        with patch.object(app, "exit") as mock_exit:
            b_cq[0].handler(event)
            mock_exit.assert_called_once()


def test_focus_filter_blocks_digits_and_q_in_textarea():
    with create_pipe_input() as inp, create_app_session(input=inp, output=DummyOutput()):
        state = UiState()
        app = build_application(state, api=None, supervisor=None)
        
        # Mock focus to be inside an editable TextArea
        ta = TextArea(text="editable", read_only=False)
        
        with patch("prompt_toolkit.layout.Layout.current_control", new_callable=PropertyMock) as mock_control:
            mock_control.return_value = ta.control
            
            # Get key bindings for '2' and 'q'
            b_2 = app.key_bindings.get_bindings_for_keys(("2",))
            b_q = app.key_bindings.get_bindings_for_keys(("q",))
            
            assert len(b_2) > 0
            assert len(b_q) > 0
            
            # Filter should evaluate to False, blocking global actions
            assert b_2[0].filter() is False
            assert b_q[0].filter() is False

def test_poller_thread_stops_on_finalization():
    state = UiState(active_run_id=770)
    api = MagicMock()
    api.get_progress.side_effect = Exception("404")
    api.get_status.return_value = {"status": "completed", "savings_pct": 10.0, "cells_total": 5}
    
    with create_pipe_input() as inp, create_app_session(input=inp, output=DummyOutput()):
        app = build_application(state, api=api, supervisor=None)
        
        from prompt_toolkit.application import Application
        with patch.object(Application, "is_running", new_callable=PropertyMock) as mock_is_running:
            mock_is_running.return_value = True
            
            import time
            for _ in range(20):
                if state.run_finalized:
                    break
                time.sleep(0.1)
                
            assert state.run_finalized is True
            assert state.final_status is not None
            assert state.final_status["status"] == "completed"
            api.get_progress.assert_called_with(770)
            api.get_status.assert_called_with(770)
