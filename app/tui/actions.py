# app/tui/actions.py
"""Action handlers wiring TUI state, API client, and process supervisor.

Handles batch submit, run control (pause, resume, stop, restart), sharp daemon
lifecycle, and settings persistence with live/restart key classification.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Any

from app.core.paths import PROJ_ROOT
from app.tui.state import UiState, build_batch_payload
from app.tui.settings import load_settings, save_settings as save_toml, classify


def submit(state: UiState, api: Any) -> None:
    state.toast = None
    state.progress_cache = {}
    state.run_finalized = False
    state.final_status = None
    if api is None:
        state.toast = "API client not available"
        return
    try:
        payload = build_batch_payload(state)
        res = api.start_batch(
            source_dir=payload["source_dir"],
            target_dir=payload["target_dir"],
            target_format=payload["target_format"],
            tool=payload["tool"],
            category=payload["category"]
        )
        state.active_run_id = res.get("run_id")
        state.active_tab = "telemetry"
    except ValueError as e:
        state.toast = str(e)
    except Exception as e:
        state.toast = f"API Error: {e}"


def control(state: UiState, api: Any, action: str) -> None:
    state.toast = None
    if api is None:
        state.toast = "API client not available"
        return
    if state.active_run_id is None:
        state.toast = "No active run"
        return
    try:
        if action == "restart":
            state.progress_cache = {}
            state.run_finalized = False
            state.final_status = None
            res = api.restart(state.active_run_id)
            if res and "run_id" in res:
                state.active_run_id = res["run_id"]
            state.active_tab = "telemetry"
        else:
            api.control(state.active_run_id, action)
    except Exception as e:
        state.toast = f"Control Error: {e}"


def _find_node_cmd() -> str:
    portable_node = (
        os.path.join(PROJ_ROOT, "node", "node.exe")
        if sys.platform == "win32"
        else os.path.join(PROJ_ROOT, "node", "node")
    )
    if os.path.exists(portable_node):
        return portable_node
    node_cmd = shutil.which("node") or shutil.which("nodejs")
    if not node_cmd:
        raise RuntimeError("Node.js binary not found")
    return node_cmd


def sharp(state: UiState, supervisor: Any, action: str) -> None:
    state.toast = None
    if supervisor is None:
        state.toast = "Process supervisor not available"
        return
    try:
        settings = state.settings or {}
        sharp_script = settings.get("tools", {}).get("sharp_script", "services/sharp-daemon/sharp_daemon.js")
        sharp_port = settings.get("paths", {}).get("sharp_port", 8765)

        if action == "stop":
            supervisor.stop("sharp")
        elif action in ("start", "restart"):
            node_cmd = _find_node_cmd()
            cmd = [node_cmd, str(PROJ_ROOT / sharp_script), str(sharp_port)]
            if action == "restart":
                supervisor.restart("sharp", cmd)
            else:
                supervisor.start("sharp", cmd)
        else:
            state.toast = f"Unknown sharp action: {action}"
    except Exception as e:
        state.toast = f"Sharp Daemon Error: {e}"


def save_settings(state: UiState, path: str | Path) -> None:
    state.toast = None
    try:
        # Load old settings to compare
        old_cfg = load_settings(path)
        # Save new settings
        save_toml(path, state.settings)
        
        # Apply live keys (tools.enabled -> state.enabled_tools)
        if "tools" in state.settings and "enabled" in state.settings["tools"]:
            state.enabled_tools = list(state.settings["tools"]["enabled"])

        # Check for changed restart-required keys
        changed_restart = []
        for section, body in state.settings.items():
            if section not in old_cfg:
                continue
            for key, val in body.items():
                if key not in old_cfg[section]:
                    continue
                if val != old_cfg[section][key]:
                    if classify(section, key) == "restart":
                        changed_restart.append(f"{section}.{key}")

        if changed_restart:
            state.toast = f"Settings saved. Restart required to apply changes to: {', '.join(changed_restart)}"
        else:
            state.toast = "Settings saved successfully"
    except Exception as e:
        state.toast = f"Save Settings Error: {e}"
