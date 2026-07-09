"""PixelPivotTray menu — structure, dynamic submenus, SCM-state enable/disable.

scm.get_state is patched so no real service is queried, and _fetch_api is
neutralised so the background poll never touches the network.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="tray is win32-only")

pytest.importorskip("pytestqt")

from PySide6.QtWidgets import QSystemTrayIcon

from app.windows import tray as tray_mod


@pytest.fixture
def make_tray(qapp, qtbot, tmp_path, monkeypatch):
    """Build a PixelPivotTray with a patched SCM state and no network poll."""
    from PySide6.QtCore import QThreadPool
    if not QSystemTrayIcon.isSystemTrayAvailable():
        pytest.skip("no system tray on this session")

    # We mock _api calls to return dummy data so they don't touch the network
    monkeypatch.setattr(tray_mod._api, "health", lambda: {"status": "ready", "ready": True})
    monkeypatch.setattr(tray_mod._api, "batch_history", lambda: [])
    monkeypatch.setattr(tray_mod._api, "hotfolders", lambda: [])

    trays = []

    def _build(state="stopped"):
        monkeypatch.setattr(tray_mod.scm, "get_state", lambda: state)
        t = tray_mod.PixelPivotTray(qapp, tmp_path / "svc.exe", tmp_path / "logs")
        def check():
            txt = t._act_status.text()
            print(f"DEBUG BUILD: state={state}, txt={txt}")
            return state in txt or (state == "running" and "API" in txt)
        qtbot.waitUntil(check, timeout=5000)
        trays.append(t)
        return t

    yield _build

    for t in trays:
        t._poll_timer.stop()
    QThreadPool.globalInstance().waitForDone()


def _action_texts(menu):
    return [a.text() for a in menu.actions()]


def test_top_level_menu_items(make_tray):
    t = make_tray("stopped")
    texts = _action_texts(t.contextMenu())
    for expected in ("Open GUI", "Open API Docs", "Settings...", "View Logs...", "Exit"):
        assert expected in texts


def test_service_submenu_disable_when_not_installed(make_tray):
    t = make_tray("not_installed")
    assert t._act_install.isEnabled() is True
    assert t._act_start.isEnabled() is False
    assert t._act_stop.isEnabled() is False
    assert t._act_uninstall.isEnabled() is False


def test_service_submenu_when_stopped(make_tray):
    t = make_tray("stopped")
    assert t._act_start.isEnabled() is True
    assert t._act_stop.isEnabled() is False
    assert t._act_install.isEnabled() is False   # already installed
    assert t._act_uninstall.isEnabled() is True


def test_service_submenu_when_running(make_tray):
    t = make_tray("running")
    assert t._act_start.isEnabled() is False
    assert t._act_stop.isEnabled() is True
    assert t._act_uninstall.isEnabled() is False  # must stop before uninstall


def test_busy_state_disables_actions(make_tray):
    t = make_tray("starting")
    assert t._act_start.isEnabled() is False
    assert t._act_stop.isEnabled() is False


def test_rebuild_batch_menu_running_job_has_pause_stop(make_tray):
    t = make_tray("stopped")
    t._rebuild_batch_menu([{"run_id": 3, "status": "running", "progress": 40}])
    # last action is always "Start New Batch..."; the job is a submenu above it
    texts = _action_texts(t._batch_menu)
    assert any("#3" in x and "running" in x for x in texts)
    assert "Start New Batch..." in texts
    # drill into the job submenu
    job_action = next(a for a in t._batch_menu.actions() if "#3" in a.text())
    sub_texts = _action_texts(job_action.menu())
    assert "Pause" in sub_texts and "Stop" in sub_texts


def test_rebuild_batch_menu_completed_job_has_restart(make_tray):
    t = make_tray("stopped")
    t._rebuild_batch_menu([{"run_id": 8, "status": "completed"}])
    job_action = next(a for a in t._batch_menu.actions() if "#8" in a.text())
    assert "Restart" in _action_texts(job_action.menu())


def test_rebuild_batch_menu_empty_still_has_start(make_tray):
    t = make_tray("stopped")
    t._rebuild_batch_menu([])
    assert "Start New Batch..." in _action_texts(t._batch_menu)


def test_rebuild_hf_menu_lists_watcher_and_register(make_tray):
    t = make_tray("stopped")
    t._rebuild_hf_menu([{"id": "w1", "source_dir": "C:/watch/inbox"}])
    texts = _action_texts(t._hf_menu)
    assert "Register Hot Folder..." in texts
    watcher_action = next(a for a in t._hf_menu.actions() if a.menu())
    assert "Unregister" in _action_texts(watcher_action.menu())


def test_batch_control_calls_api(make_tray, monkeypatch):
    from PySide6.QtCore import QThreadPool
    t = make_tray("running")
    calls = []
    monkeypatch.setattr(tray_mod._api, "batch_control", lambda rid, act: calls.append((rid, act)) or {"ok": True})
    monkeypatch.setattr(tray_mod.PixelPivotTray, "_update_state", lambda self: None)
    t._batch_control(5, "pause")
    QThreadPool.globalInstance().waitForDone()
    assert calls == [(5, "pause")]



def test_svc_install_elevate_args(make_tray, monkeypatch):
    t = make_tray("not_installed")
    elevate_calls = []
    monkeypatch.setattr(tray_mod.elevation, "elevate", lambda *args: elevate_calls.append(args))
    t._svc_install()
    assert len(elevate_calls) == 1
    assert elevate_calls[0][1:] == ("--startup", "auto", "install")


def test_batch_control_is_async(make_tray, monkeypatch):
    import threading
    import time
    t = make_tray("running")
    
    api_started = threading.Event()
    api_continue = threading.Event()
    
    def slow_batch_control(rid, act):
        api_started.set()
        api_continue.wait(timeout=2)
        return {"ok": True}
        
    monkeypatch.setattr(tray_mod._api, "batch_control", slow_batch_control)
    monkeypatch.setattr(tray_mod.PixelPivotTray, "_update_state", lambda self: None)
    
    start_time = time.monotonic()
    t._batch_control(5, "pause")
    elapsed = time.monotonic() - start_time
    
    api_continue.set()
    
    assert elapsed < 0.2
    assert api_started.wait(timeout=1.0) is True


def test_scm_query_is_async(qapp, monkeypatch):
    import threading
    import time
    from PySide6.QtCore import QThreadPool
    
    scm_started = threading.Event()
    scm_continue = threading.Event()
    
    def slow_get_state():
        scm_started.set()
        scm_continue.wait(timeout=2)
        return "running"
        
    monkeypatch.setattr(tray_mod.scm, "get_state", slow_get_state)
    
    # We mock _api calls as well
    monkeypatch.setattr(tray_mod._api, "health", lambda: {"status": "ready", "ready": True})
    monkeypatch.setattr(tray_mod._api, "batch_history", lambda: [])
    monkeypatch.setattr(tray_mod._api, "hotfolders", lambda: [])
    
    start_time = time.monotonic()
    t = tray_mod.PixelPivotTray(qapp, Path("C:/dummy.exe"), Path("C:/logs"))
    elapsed = time.monotonic() - start_time
    
    # Check that constructor returns immediately
    assert elapsed < 0.2
    
    assert scm_started.wait(timeout=1.0) is True
    scm_continue.set()
    QThreadPool.globalInstance().waitForDone()


def test_svc_actions_tolerate_uac_decline(make_tray, monkeypatch):
    import pywintypes
    def raise_uac_cancel(*args, **kwargs):
        raise pywintypes.error(1223, "ShellExecuteEx", "The operation was canceled by the user.")
        
    t = make_tray("stopped")
    monkeypatch.setattr(tray_mod.elevation, "elevate", raise_uac_cancel)
    monkeypatch.setattr(tray_mod.elevation, "is_admin", lambda: False)
    
    critical_dialogs = []
    monkeypatch.setattr(tray_mod.QMessageBox, "critical", lambda *args: critical_dialogs.append(args))
    
    t._svc_start()
    t._svc_stop()
    t._svc_install()
    t._svc_uninstall()
    
    assert len(critical_dialogs) == 0


def test_svc_actions_report_other_errors(make_tray, monkeypatch):
    import pywintypes
    def raise_access_denied(*args, **kwargs):
        raise pywintypes.error(5, "ShellExecuteEx", "Access is denied.")
        
    t = make_tray("stopped")
    monkeypatch.setattr(tray_mod.elevation, "elevate", raise_access_denied)
    monkeypatch.setattr(tray_mod.elevation, "is_admin", lambda: False)
    
    critical_dialogs = []
    monkeypatch.setattr(tray_mod.QMessageBox, "critical", lambda *args: critical_dialogs.append(args))
    
    t._svc_start()
    assert len(critical_dialogs) == 1


def test_fetch_pileup_prevention(make_tray, monkeypatch, qtbot):
    from PySide6.QtCore import QThreadPool
    t = make_tray("stopped")
    
    get_state_calls = []
    def mock_get_state():
        get_state_calls.append(1)
        return "stopped"
    monkeypatch.setattr(tray_mod.scm, "get_state", mock_get_state)
    
    t._fetch_in_flight = False
    t._refresh_pending = False
    
    t._run_state_fetch()
    t._run_state_fetch()
    
    qtbot.waitUntil(lambda: len(get_state_calls) == 2, timeout=2000)


def test_first_running_tick_shows_checking(make_tray):
    t = make_tray("stopped")
    
    res_first = {
        "state": "running",
        "health": None,
        "jobs": [],
        "hfs": [],
    }
    t._apply_fetched_state(res_first)
    assert "checking..." in t._act_status.text()
    
    res_second = {
        "state": "running",
        "health": {"ready": True},
        "jobs": [],
        "hfs": [],
    }
    t._apply_fetched_state(res_second)
    assert "API ready" in t._act_status.text()
    
    res_third = {
        "state": "running",
        "health": None,
        "jobs": [],
        "hfs": [],
    }
    t._apply_fetched_state(res_third)
    assert "API unreachable" in t._act_status.text()




