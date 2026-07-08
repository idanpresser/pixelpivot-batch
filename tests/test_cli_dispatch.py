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

def test_calibrate_bootstraps_schema_before_running():
    # The CLI calibrate path bypasses the API startup that inits the DB, so it
    # must bootstrap the schema itself or create_run() hits "no such table".
    with patch("app.core.db.schema.init_db") as init_db, \
         patch("app.batch_api.calibration_runner.run_calibration") as run_cal:
        run_cal.return_value = {"run_id": 1, "calibrated": 0, "failures": 0,
                                "cells": 0, "table": None}
        cli.main(["calibrate", "--source", "/s"])
        init_db.assert_called_once()
        run_cal.assert_called_once()
