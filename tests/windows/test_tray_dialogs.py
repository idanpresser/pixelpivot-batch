"""Tray dialogs — payload construction + validation. Needs a QApplication.

Dialogs are exercised without exec(): set widget values, call _submit()
directly, inspect .payload. QMessageBox.warning is patched to a no-op so a
failed validation does not pop a modal and block the test.
"""
from __future__ import annotations

import json
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="tray is win32-only")

pytest.importorskip("pytestqt")

from app.windows import tray as tray_mod


@pytest.fixture(autouse=True)
def _silence_warnings(monkeypatch):
    monkeypatch.setattr(tray_mod.QMessageBox, "warning", lambda *a, **k: None)


# --------------------------------------------------------------------------
# StartBatchDialog
# --------------------------------------------------------------------------

def test_start_batch_valid_payload(qtbot):
    dlg = tray_mod.StartBatchDialog()
    qtbot.addWidget(dlg)
    dlg._src.setText("C:/in")
    dlg._tgt.setText("C:/out")
    dlg._tools.item(0).setSelected(True)  # magick
    dlg._fmts.item(0).setSelected(True)   # webp
    dlg._cat.setText("photos, art")
    dlg._sample.setValue(5)

    dlg._submit()

    assert dlg.payload == {
        "source_dir": "C:/in",
        "target_dir": "C:/out",
        "tool": ["magick"],
        "target_format": ["webp"],
        "category": ["photos", "art"],
        "trigger_type": "manual",
        "sample": 5,
    }


def test_start_batch_sample_zero_omitted(qtbot):
    dlg = tray_mod.StartBatchDialog()
    qtbot.addWidget(dlg)
    dlg._src.setText("C:/in")
    dlg._tgt.setText("C:/out")
    dlg._tools.item(1).setSelected(True)  # ffmpeg
    dlg._fmts.item(0).setSelected(True)
    dlg._sample.setValue(0)  # "All files"

    dlg._submit()
    assert "sample" not in dlg.payload
    assert dlg.payload["tool"] == ["ffmpeg"]


def test_start_batch_missing_source_blocks(qtbot):
    dlg = tray_mod.StartBatchDialog()
    qtbot.addWidget(dlg)
    dlg._tgt.setText("C:/out")
    dlg._tools.item(0).setSelected(True)
    dlg._fmts.item(0).setSelected(True)
    dlg._submit()
    assert dlg.payload is None


def test_start_batch_missing_tool_blocks(qtbot):
    dlg = tray_mod.StartBatchDialog()
    qtbot.addWidget(dlg)
    dlg._src.setText("C:/in")
    dlg._tgt.setText("C:/out")
    dlg._fmts.item(0).setSelected(True)
    dlg._submit()
    assert dlg.payload is None


def test_start_batch_default_category(qtbot):
    dlg = tray_mod.StartBatchDialog()
    qtbot.addWidget(dlg)
    dlg._src.setText("C:/in")
    dlg._tgt.setText("C:/out")
    dlg._tools.item(0).setSelected(True)
    dlg._fmts.item(0).setSelected(True)
    dlg._cat.setText("   ")  # whitespace only -> default
    dlg._submit()
    assert dlg.payload["category"] == ["general"]


# --------------------------------------------------------------------------
# RegisterHotFolderDialog
# --------------------------------------------------------------------------

def test_hotfolder_valid_payload(qtbot):
    dlg = tray_mod.RegisterHotFolderDialog()
    qtbot.addWidget(dlg)
    dlg._src.setText("C:/watch")
    dlg._tgt.setText("C:/out")
    dlg._tools.item(2).setSelected(True)  # vips
    dlg._fmts.item(1).setSelected(True)   # avif
    dlg._submit()
    assert dlg.payload == {
        "source_dir": "C:/watch",
        "target_dir": "C:/out",
        "tool": ["vips"],
        "target_format": ["avif"],
        "category": ["general"],
    }


def test_hotfolder_missing_dirs_blocks(qtbot):
    dlg = tray_mod.RegisterHotFolderDialog()
    qtbot.addWidget(dlg)
    dlg._tools.item(0).setSelected(True)
    dlg._fmts.item(0).setSelected(True)
    dlg._submit()
    assert dlg.payload is None


# --------------------------------------------------------------------------
# CalibrateDialog
# --------------------------------------------------------------------------

def test_calibrate_valid_payload(qtbot):
    dlg = tray_mod.CalibrateDialog()
    qtbot.addWidget(dlg)
    dlg._src.setText("C:/samples")
    dlg._tools.item(0).setSelected(True)  # magick
    dlg._fmts.item(0).setSelected(True)   # webp
    dlg._sample.setValue(20)
    dlg._ssim.setValue(0.97)
    dlg._regen.setChecked(False)
    dlg._submit()
    assert dlg.payload == {
        "source_dir": "C:/samples",
        "tool": ["magick"],
        "target_format": ["webp"],
        "category": ["general"],
        "sample": 20,
        "target_ssim": pytest.approx(0.97),
        "regenerate_table": False,
    }


def test_calibrate_missing_source_blocks(qtbot):
    dlg = tray_mod.CalibrateDialog()
    qtbot.addWidget(dlg)
    dlg._tools.item(0).setSelected(True)
    dlg._fmts.item(0).setSelected(True)
    dlg._submit()
    assert dlg.payload is None


# --------------------------------------------------------------------------
# SettingsDialog
# --------------------------------------------------------------------------

def test_settings_dialog_loads_current_values(qtbot, tmp_path):
    s = tray_mod._Settings(tmp_path)
    data = s.load()
    data["disk_backpressure_pct"] = 88.0
    s.save(data)

    dlg = tray_mod.SettingsDialog(s)
    qtbot.addWidget(dlg)
    assert dlg._disk.value() == 88.0


def test_settings_dialog_save_persists_and_maps_auto_workers(qtbot, tmp_path):
    s = tray_mod._Settings(tmp_path)
    dlg = tray_mod.SettingsDialog(s)
    qtbot.addWidget(dlg)

    dlg._scaling.setValue(3.5)
    dlg._max_w.setValue(0)          # "Auto" -> None
    dlg._calibration = dlg._cal_en.setChecked(True)
    dlg._save()

    loaded = s.load()
    assert loaded["concurrent_encodes_scaling_factor"] == 3.5
    assert loaded["concurrent_encodes_max_workers"] is None
    assert loaded["calibration_enabled"] is True
    assert (tmp_path / "pixelpivot_config.json").exists()


def test_settings_dialog_save_concrete_worker_cap(qtbot, tmp_path):
    s = tray_mod._Settings(tmp_path)
    dlg = tray_mod.SettingsDialog(s)
    qtbot.addWidget(dlg)
    dlg._max_w.setValue(6)
    dlg._save()
    assert s.load()["concurrent_encodes_max_workers"] == 6
