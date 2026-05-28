"""
PixelPivot Batch Engine Theme Engine
====================================
Standardized aesthetic loading for the premium Streamlit frontend.
Optimized for air-gapped environments via local font embedding.
"""
import streamlit as st
import base64
from pathlib import Path

# Theme constants (matching CSS tokens)
TEAL = "#009688"
BURNT_ORANGE = "#E65100"
BG_WHITE = "#FFFFFF"
BG_GREY = "#F5F7F7"

@st.cache_data
def get_base64_font(font_path: Path) -> str:
    """Encode font file as base64 data URI.

    Args:
        font_path: Path to font file.

    Returns:
        Base64-encoded font string, or empty string if not found.
    """
    if not font_path.exists():
        return ""
    with open(font_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")

def get_font_face_css():
    """Generate @font-face CSS declarations for embedded local fonts.

    Returns:
        CSS string with @font-face rules.
    """
    base_path = Path(__file__).resolve().parent / "static" / "fonts" / "ttf"

    fonts = [
        ("Inter", "Inter-VariableFont_opsz,wght.ttf"),
        ("Space Grotesk", "SpaceGrotesk-VariableFont_wght.ttf"),
        ("JetBrains Mono", "JetBrainsMono-VariableFont_wght.ttf"),
    ]

    css = "<style>\n"
    for family, filename in fonts:
        b64 = get_base64_font(base_path / filename)
        if b64:
            css += f"""
            @font-face {{
                font-family: '{family}';
                src: url(data:font/ttf;base64,{b64}) format('truetype');
                font-weight: normal;
                font-style: normal;
            }}\n"""
    css += "</style>"
    return css

def inject_theme_css():
    """Inject embedded fonts and theme CSS to Streamlit page.

    Loads local font files and theme_light.css stylesheet.
    """
    # 1. Local Fonts (Base64)
    st.markdown(get_font_face_css(), unsafe_allow_html=True)

    # 2. Local CSS File
    css_path = Path(__file__).resolve().parent / "static" / "css" / "theme_light.css"
    if css_path.exists():
        with open(css_path, "r", encoding="utf-8") as f:
            st.markdown(f"<style>\n{f.read()}\n</style>", unsafe_allow_html=True)
    else:
        st.warning("Theme CSS not found. UI may appear unstyled.")
