"""Wire the supervisor + API client + prompt_toolkit app for `cli tui`.

Spawns the API as a child, waits for readiness, builds the app, and guarantees
child shutdown on exit. Sharp is started on demand from the Tools screen, not
here (on-demand policy).
"""
from __future__ import annotations

import sys

from app.tui.app import build_application
from app.tui.api_client import TuiApiClient
from app.tui.settings import load_settings
from app.tui.state import UiState
from app.tui.supervisor import ProcessSupervisor
from app.core.paths import PROJ_ROOT


def run_tui() -> None:
    cfg = load_settings(PROJ_ROOT / "data" / "settings.toml")
    host, port = cfg["api"]["host"], cfg["api"]["port"]
    base_url = f"http://{host}:{port}/api/v1"

    sup = ProcessSupervisor()
    sup.start("api", [sys.executable, "-m", "app.cli", "serve",
                      "--host", str(host), "--port", str(port)])
    ready = sup.wait_ready(f"http://{host}:{port}/")
    api = TuiApiClient(base_url)

    state = UiState(
        enabled_tools=list(cfg["tools"]["enabled"]),
        settings=cfg
    )
    app = build_application(state, api=api, supervisor=sup)
    if not ready:
        sup_logs = "\n".join(sup.get_logs()[-5:])
        print(f"API did not become ready; recent logs:\n{sup_logs}", file=sys.stderr)
    try:
        app.run()
    finally:
        api.close()
        sup.shutdown()
