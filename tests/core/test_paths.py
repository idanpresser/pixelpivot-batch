import os
import sys
from pathlib import Path
import pytest

from app.core import paths
from app.windows import _settings
from app.core.db.connection import get_db_path


def test_resolve_data_dir_honors_env(monkeypatch, tmp_path):
    custom_dir = tmp_path / "custom_data"
    monkeypatch.setenv("PIXELPIVOT_DATA_DIR", str(custom_dir))

    assert paths.resolve_data_dir() == custom_dir
    assert _settings.resolve_data_dir() == custom_dir
    assert get_db_path() == custom_dir / "pixelpivot.db"


def test_resolve_data_dir_frozen_windows_programdata(monkeypatch, tmp_path):
    monkeypatch.delenv("PIXELPIVOT_DATA_DIR", raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "PixelPivotService.exe"))
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("ProgramData", str(tmp_path / "ProgramData"))

    expected = tmp_path / "ProgramData" / "PixelPivot"
    assert paths.resolve_data_dir() == expected
    assert _settings.resolve_data_dir() == expected
    assert get_db_path() == expected / "pixelpivot.db"
