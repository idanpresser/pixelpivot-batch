"""Guard tests for bd-19y: Streamlit phone-home fully disabled for air-gap.

Streamlit's defaults attempt outbound calls (api.streamlit.io usage stats +
update check). For a zero-outbound deployment these must be disabled via a
staged .streamlit/config.toml, and the air-gap guide must document it.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_streamlit_config_disables_all_outbound():
    cfg = ROOT / ".streamlit" / "config.toml"
    assert cfg.exists()
    text = cfg.read_text(encoding="utf-8").replace(" ", "").lower()
    assert "gatherusagestats=false" in text   # usage telemetry off
    assert "checkupdate=false" in text         # update phone-home off
    assert "headless=true" in text             # no browser auto-launch


def test_airgap_guide_documents_streamlit_telemetry():
    guide = ROOT / "air_gapped_guide.md"
    assert guide.exists()
    text = guide.read_text(encoding="utf-8").lower()
    assert "gatherusagestats" in text.replace(" ", "")
    assert "api.streamlit.io" in text
