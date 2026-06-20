"""Streamlit is excluded from the runtime + air-gap build (bead y0z).

The Streamlit GUI (app/web/batch_gui) is a separate, optional component. The
deployable backend (FastAPI + CLI/TUI) must not pull streamlit as a runtime
dependency, must not ship it in the air-gap wheel manifest, and no runtime
module outside app/web may import it. The GUI source is kept (installable via
the ``gui`` extra) but excluded from the frozen build.
"""

import tomllib
from pathlib import Path

import app.core.paths as paths

ROOT = Path(paths.__file__).resolve().parent.parent.parent  # core -> app -> root


def test_streamlit_not_in_core_dependencies():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    deps = data["project"]["dependencies"]
    assert not any("streamlit" in d.lower() for d in deps), deps


def test_streamlit_available_as_optional_gui_extra():
    """Kept, not deleted: installable on demand for whoever runs the GUI."""
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    extras = data["project"].get("optional-dependencies", {})
    gui = extras.get("gui", [])
    assert any("streamlit" in d.lower() for d in gui), extras


def test_streamlit_not_in_air_gap_manifest():
    lines = (ROOT / "scripts" / "air_gap_deps.txt").read_text(encoding="utf-8").splitlines()
    pkgs = [ln.strip().lower() for ln in lines if ln.strip() and not ln.strip().startswith("#")]
    assert "streamlit" not in pkgs, pkgs


def test_no_runtime_module_imports_streamlit():
    app_dir = ROOT / "app"
    offenders = []
    for py in app_dir.rglob("*.py"):
        if "web" in py.relative_to(app_dir).parts:  # GUI is the one allowed home
            continue
        text = py.read_text(encoding="utf-8", errors="ignore")
        if "import streamlit" in text:
            offenders.append(str(py))
    assert offenders == [], offenders
