"""Task 025 - HOT_FOLDER_DEBOUNCE_MS must reach the handler.

Today HotFolderManager.add_hot_folder constructs HotFolderHandler without
passing debounce_seconds, so the constructor's hardcoded 5.0 s default is
always used and the config knob is silently inert.
"""
from __future__ import annotations
import asyncio
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def tmp_dirs(tmp_path):
    src = tmp_path / "src"
    tgt = tmp_path / "tgt"
    src.mkdir()
    tgt.mkdir()
    return src, tgt


def _make_manager():
    """Construct a HotFolderManager with a dummy orchestrator + event loop.

    The polling thread is left running (daemon=True); the test just inspects
    the handler attached to a registered watcher and tears down via stop().
    """
    from app.batch_api.hot_folder import HotFolderManager

    class _StubOrchestrator:
        class _Interp:
            version = "test"
        interpolator = _Interp()

        def execute_batch(self, run_id, request):
            pass

    loop = asyncio.new_event_loop()
    return HotFolderManager(_StubOrchestrator(), loop), loop


def test_default_debounce_matches_config(tmp_dirs) -> None:
    """With unmodified config, the handler debounce equals the config value."""
    from app.core.config import HOT_FOLDER_DEBOUNCE_MS

    src, tgt = tmp_dirs
    mgr, loop = _make_manager()
    try:
        wid = mgr.add_hot_folder({
            "source_dir": str(src),
            "target_dir": str(tgt),
            "target_format": ["webp"],
            "tool": ["magick"],
            "category": ["general"],
        })
        handler = mgr.watchers[wid]["handler"]
        expected = HOT_FOLDER_DEBOUNCE_MS / 1000.0
        assert handler.debounce_seconds == pytest.approx(expected), (
            f"handler.debounce_seconds={handler.debounce_seconds}; "
            f"expected {expected} (from HOT_FOLDER_DEBOUNCE_MS={HOT_FOLDER_DEBOUNCE_MS})"
        )
    finally:
        mgr.stop()
        loop.close()


def test_config_override_propagates_to_handler(monkeypatch, tmp_dirs) -> None:
    """Changing the config constant changes the handler debounce."""
    from app.batch_api import hot_folder as hf_mod

    # Override the binding inside the hot_folder module so the next
    # add_hot_folder call sees the new value.
    monkeypatch.setattr(hf_mod, "HOT_FOLDER_DEBOUNCE_MS", 1500)

    src, tgt = tmp_dirs
    mgr, loop = _make_manager()
    try:
        wid = mgr.add_hot_folder({
            "source_dir": str(src),
            "target_dir": str(tgt),
            "target_format": ["webp"],
            "tool": ["magick"],
            "category": ["general"],
        })
        handler = mgr.watchers[wid]["handler"]
        assert handler.debounce_seconds == pytest.approx(1.5), (
            f"handler.debounce_seconds={handler.debounce_seconds}; "
            f"expected 1.5 after monkeypatching HOT_FOLDER_DEBOUNCE_MS=1500"
        )
    finally:
        mgr.stop()
        loop.close()
