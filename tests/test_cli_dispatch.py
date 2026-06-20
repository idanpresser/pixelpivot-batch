# tests/test_cli_dispatch.py
from unittest.mock import patch
import app.cli as cli

def test_serve_invokes_uvicorn():
    with patch("app.cli._run_serve") as serve:
        cli.main(["serve", "--host", "0.0.0.0", "--port", "8001"])
        serve.assert_called_once()

def test_tui_invokes_launcher():
    with patch("app.cli._run_tui") as tui:
        cli.main(["tui"])
        tui.assert_called_once()

def test_convert_runs_validation():
    with patch("app.cli._run_convert") as conv:
        cli.main(["convert", "--source", "/s", "--target", "/d", "--dry-run"])
        conv.assert_called_once()
