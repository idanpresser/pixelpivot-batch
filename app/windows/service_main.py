"""Entry point for PixelPivotService.exe.

Dispatch modes (child processes spawned by the service):
  --mode api   Run FastAPI via uvicorn on 0.0.0.0:8000.
  --mode gui   Run Streamlit GUI on 0.0.0.0:8503.

Bare invocation (SCM commands):
  install [auto|demand]   Register service with Windows SCM.
  start                   Start the service.
  stop                    Stop the service.
  remove                  Uninstall the service.
  debug                   Run service loop in foreground (no SCM).

On startup, data/pixelpivot_config.json (written by the tray GUI) is loaded
and applied to os.environ so child processes inherit all saved settings.
"""
from __future__ import annotations

import json
import os
import sys


def _run_api() -> None:
    import uvicorn

    uvicorn.run(
        "app.batch_api.main:app",
        host="0.0.0.0",
        port=8000,
        log_level="info",
    )


def _run_gui() -> None:
    from pathlib import Path

    # Locate the streamlit app script — must be a physical .py file.
    # In frozen --onedir builds it lands in _internal/ (sys._MEIPASS).
    if getattr(sys, "frozen", False):
        base = Path(sys._MEIPASS)  # type: ignore[attr-defined]
    else:
        base = Path(__file__).parent.parent.parent

    script = base / "app" / "web" / "batch_gui" / "main.py"
    if not script.exists():
        print(f"GUI script not found: {script}", file=sys.stderr)
        sys.exit(1)

    sys.argv = [
        "streamlit", "run", str(script),
        "--server.port", "8503",
        "--server.headless", "true",
        "--server.address", "0.0.0.0",
    ]
    from streamlit.web.cli import main as st_main
    st_main()


def _apply_saved_settings() -> None:
    """Load data/pixelpivot_config.json and set env vars for child processes."""
    from pathlib import Path
    from app.windows._settings import resolve_data_dir, SETTINGS_ENV_MAP
    cfg_path = resolve_data_dir() / "pixelpivot_config.json"

    if not cfg_path.exists():
        return
    try:
        cfg = json.loads(cfg_path.read_text())
    except Exception as e:
        sys.stderr.write(f"WARNING: Failed to parse settings file {cfg_path}: {e}\n")
        try:
            import servicemanager
            servicemanager.LogWarningMsg(f"Failed to parse settings file {cfg_path}: {e}")
        except Exception:
            pass
        return

    for key, env_var in SETTINGS_ENV_MAP.items():
        val = cfg.get(key)
        if val is None:
            continue
        os.environ[env_var] = "1" if val is True else "0" if val is False else str(val)


def main() -> None:
    _apply_saved_settings()
    # Dispatch child modes before importing pywin32 — lean startup for workers.
    if len(sys.argv) >= 3 and sys.argv[1] == "--mode":
        mode = sys.argv[2]
        if mode == "api":
            _run_api()
            return
        if mode == "gui":
            _run_gui()
            return

    from app.windows.service import PixelPivotService
    import win32serviceutil

    # Frozen build: register THIS exe as the service binary. Otherwise pywin32
    # defaults to pythonservice.exe (absent from the bundle) and the SCM
    # ImagePath points at a missing host, so the service never starts.
    if getattr(sys, "frozen", False):
        PixelPivotService._exe_name_ = sys.executable

    if len(sys.argv) == 1:
        # Launched by the Windows SCM (no verb): start the control dispatcher.
        # A frozen pywin32 service MUST host itself here — HandleCommandLine
        # only covers the console verbs and would exit with usage text,
        # causing the SCM to report error 1053 (service did not respond).
        import servicemanager

        servicemanager.Initialize()
        servicemanager.PrepareToHostSingle(PixelPivotService)
        servicemanager.StartServiceCtrlDispatcher()
    else:
        # Console verbs: install / start / stop / remove / debug
        win32serviceutil.HandleCommandLine(PixelPivotService)


if __name__ == "__main__":
    main()
