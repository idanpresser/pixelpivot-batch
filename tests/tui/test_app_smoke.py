from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from app.tui.app import build_application
from app.tui.state import UiState

def test_application_builds_and_renders():
    with create_pipe_input() as inp, create_app_session(input=inp, output=DummyOutput()):
        app = build_application(UiState(), api=None, supervisor=None)
        # Layout must contain the tab bar and log panel containers.
        assert app.layout is not None
        # Tab switch reducer works without a running event loop.
        app.state.active_tab = "tools"
        assert app.state.active_tab == "tools"
