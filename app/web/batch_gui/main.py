"""Streamlit entry point for PixelPivot Batch GUI.

Renders the main application shell with three tabs: EXECUTE (batch jobs),
HOT FOLDERS (watcher management), and HISTORY (batch results).
"""
import streamlit as st
import os
from pathlib import Path
import base64
from app.web.batch_gui.api_client import APIClient
from app.web.batch_gui.panels.run_panel import render_run_panel
from app.web.batch_gui.panels.history_panel import render_history_panel
from app.web.batch_gui.panels.hot_folder_panel import render_hot_folder_panel
from app.web.batch_gui.theme_engine import inject_theme_css, TEAL, BURNT_ORANGE
from app.web.batch_gui.style_utils import inject_custom_css

# Configuration
API_URL = os.getenv("BATCH_API_URL", "http://127.0.0.1:8000/api/v1")

st.set_page_config(
    page_title="PixelPivot // Batch Terminal",
    page_icon="🚀",
    layout="wide",
    initial_sidebar_state="collapsed"
)

@st.cache_data
def get_base64_logo() -> str:
    """Load and encode logo SVG as base64 data URI.

    Returns:
        Base64-encoded SVG string, or empty string if file not found.
    """
    logo_path = Path(__file__).resolve().parent / "static" / "images" / "logo_light.svg"
    if logo_path.exists():
        with open(logo_path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")
    return ""

def render_header():
    """Render the application header with logo and title."""
    logo_b64 = get_base64_logo()
    logo_html = ""
    if logo_b64:
        logo_html = f'<img src="data:image/svg+xml;base64,{logo_b64}" style="height: 50px; width: auto; margin-right: 10px;" />'

    st.markdown(
        f"""
        <div style="display: flex; align-items: center; gap: 15px; margin-bottom: 2rem; margin-top: 1rem;">
            {logo_html}
            <h1 class="main-title" style="margin: 0; line-height: 1;">PIXELPIVOT</h1>
            <div style="background: linear-gradient(180deg, {TEAL}, transparent); width: 2px; height: 50px;"></div>
            <div>
                <div class="subtitle" style="font-size: 1.2rem; font-weight: 700; color: {TEAL};">// BATCH TERMINAL //</div>
                <div class="subtitle" style="opacity: 0.6;">System Version 4.0.0-PROD</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True
    )

def main():
    """Initialize and render the application UI with theme and panels."""
    inject_theme_css()
    inject_custom_css()
    render_header()

    st.sidebar.markdown("### SYSTEM STATUS")
    st.sidebar.info(f"CONNECTED: `{API_URL}`")

    if "api_client" not in st.session_state:
        st.session_state["api_client"] = APIClient(base_url=API_URL)
    client = st.session_state["api_client"]

    tab_names = ["⚡ EXECUTE", "📂 HOT FOLDERS", "📊 HISTORY"]
    tabs = st.tabs(tab_names)

    with tabs[0]:
        render_run_panel(client)

    with tabs[1]:
        render_hot_folder_panel(client)

    with tabs[2]:
        render_history_panel(client)

if __name__ == "__main__":
    main()
