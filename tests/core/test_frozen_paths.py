"""Frozen-aware PROJ_ROOT resolution for PyInstaller (bead zqj).

When the app is frozen (PyInstaller sets ``sys.frozen``), bundled native
binaries (bin/, vendor/) ship next to the executable, so PROJ_ROOT must
resolve to the executable's directory rather than the source-tree layout
``Path(__file__).parent.parent``.
"""

import sys
from pathlib import Path

import app.core.paths as paths


def test_proj_root_resolves_to_exe_dir_when_frozen(monkeypatch):
    """Frozen build: PROJ_ROOT = dir(sys.executable)."""
    exe = Path("C:/dist/pixelpivot/pixelpivot.exe")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    monkeypatch.delenv("PIXELPIVOT_PROJ_ROOT", raising=False)

    assert paths.resolve_proj_root() == exe.resolve().parent


def test_proj_root_uses_source_layout_when_not_frozen(monkeypatch):
    """Dev/source run: PROJ_ROOT keeps the two-up-from-app layout."""
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.delenv("PIXELPIVOT_PROJ_ROOT", raising=False)

    expected = Path(paths.__file__).resolve().parent.parent.parent
    assert paths.resolve_proj_root() == expected


def test_env_override_wins_over_frozen(monkeypatch):
    """Explicit PIXELPIVOT_PROJ_ROOT beats both frozen and source detection."""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", "C:/dist/pixelpivot/pixelpivot.exe")
    monkeypatch.setenv("PIXELPIVOT_PROJ_ROOT", "D:/custom/root")

    assert paths.resolve_proj_root() == Path("D:/custom/root")
