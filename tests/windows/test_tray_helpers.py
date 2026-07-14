"""Tray small UI helpers — icon + tool/format list widgets. Needs a QApplication."""
from __future__ import annotations

import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="tray is win32-only")

pytest.importorskip("pytestqt")

from app.windows import tray as tray_mod


def test_make_icon_non_null(qtbot):
    icon = tray_mod._make_icon()
    assert not icon.isNull()


def test_make_icon_accepts_color(qtbot):
    icon = tray_mod._make_icon("#ff0000")
    assert not icon.isNull()


def test_tool_fmt_widgets_have_expected_items(qtbot):
    tools, fmts = tray_mod._tool_fmt_widgets()
    tool_items = [tools.item(i).text() for i in range(tools.count())]
    fmt_items = [fmts.item(i).text() for i in range(fmts.count())]
    assert tool_items == ["magick", "ffmpeg", "vips", "sharp", "cavif"]
    assert fmt_items == ["webp", "avif", "jxl"]


def test_tool_fmt_widgets_are_multiselect(qtbot):
    from PySide6.QtWidgets import QListWidget

    tools, _ = tray_mod._tool_fmt_widgets()
    assert tools.selectionMode() == QListWidget.SelectionMode.MultiSelection


def test_selected_reflects_selection(qtbot):
    tools, _ = tray_mod._tool_fmt_widgets()
    tools.item(0).setSelected(True)  # magick
    tools.item(2).setSelected(True)  # vips
    assert tray_mod._selected(tools) == ["magick", "vips"]


def test_selected_empty_when_nothing_chosen(qtbot):
    tools, _ = tray_mod._tool_fmt_widgets()
    assert tray_mod._selected(tools) == []
