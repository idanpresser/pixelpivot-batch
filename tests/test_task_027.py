"""Task 027 - the Streamlit style_utils module must not reach any external CDN.

The module already labels itself "Air-Gapped / Zero-Dependency"; the @import
to fonts.googleapis.com directly contradicts that. On a Networking-Disabled
sandbox the request hangs/fails and fonts silently fall back to system
defaults. Drop the @import.
"""
from __future__ import annotations
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
STYLE_UTILS = PROJ / "app" / "web" / "batch_gui" / "style_utils.py"


def _src() -> str:
    return STYLE_UTILS.read_text(encoding="utf-8")


def test_module_loads_without_crashing() -> None:
    """The module must remain importable after the fix."""
    from app.web.batch_gui.style_utils import get_icon
    out = get_icon("bolt")
    assert "<svg" in out


def test_no_googleapis_or_gstatic_egress() -> None:
    text = _src()
    assert "fonts.googleapis.com" not in text, (
        "style_utils.py must not @import fonts.googleapis.com"
    )
    assert "fonts.gstatic.com" not in text, (
        "style_utils.py must not reference fonts.gstatic.com"
    )


def test_no_https_at_import_url() -> None:
    """No CSS @import url(http...) at all -- pure offline."""
    text = _src()
    lowered = text.lower()
    # @import url(... covers single- and double-quoted forms
    for needle in ("@import url('http", '@import url("http', "@import url(http"):
        assert needle not in lowered, (
            f"style_utils.py contains a CSS @import to a remote URL: {needle!r}"
        )
