"""UI styling utilities and custom CSS injection for Streamlit frontend."""
import streamlit as st

# Embedded SVG Icons (Air-Gapped / Zero-Dependency)
ICONS = {
    "bolt": '<svg viewBox="0 0 24 24" width="18" height="18" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"></polygon></svg>',
    "folder": '<svg viewBox="0 0 24 24" width="18" height="18" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"></path></svg>',
    "target": '<svg viewBox="0 0 24 24" width="18" height="18" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><circle cx="12" cy="12" r="6"></circle><circle cx="12" cy="12" r="2"></circle></svg>',
    "activity": '<svg viewBox="0 0 24 24" width="18" height="18" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"></polyline></svg>',
    "check": '<svg viewBox="0 0 24 24" width="18" height="18" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg>',
    "alert": '<svg viewBox="0 0 24 24" width="18" height="18" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="8" x2="12" y2="12"></line><line x1="12" y1="16" x2="12.01" y2="16"></line></svg>'
}

def get_icon(name: str, color: str = "inherit") -> str:
    """Retrieve an inline SVG icon with optional color override.

    Args:
        name: Icon name (bolt, folder, target, activity, check, alert).
        color: CSS color string, or "inherit" for current color.

    Returns:
        HTML span element with embedded SVG.
    """
    svg = ICONS.get(name, "")
    if color != "inherit":
        svg = svg.replace('stroke="currentColor"', f'stroke="{color}"')
    return f'<span style="display: inline-flex; align-items: center; vertical-align: middle; margin-right: 8px;">{svg}</span>'

def inject_custom_css():
    """Inject custom CSS for Streamlit UI enhancement (light theme, air-gapped).

    Uses system fonts to avoid CDN fetches in sandboxed environments.
    """
    st.markdown(
        """
        <style>
        /* Modern Typography (Air-Gapped: system fonts only) */
        .stApp {
            background-color: #FFFFFF;
        }

        /* Glassmorphism Container */
        .glass-card {
            background: rgba(255, 255, 255, 0.7);
            backdrop-filter: blur(10px);
            -webkit-backdrop-filter: blur(10px);
            border: 1px solid rgba(255, 255, 255, 0.3);
            border-radius: 16px;
            padding: 20px;
            box-shadow: 0 8px 32px 0 rgba(31, 38, 135, 0.07);
            margin-bottom: 20px;
        }

        /* Metric Tiles */
        .metric-tile {
            background: #F8FAFB;
            border-radius: 12px;
            padding: 15px;
            border: 1px solid #EDF1F3;
            transition: all 0.3s ease;
        }
        .metric-tile:hover {
            border-color: #009688;
            box-shadow: 0 4px 12px rgba(0, 150, 136, 0.08);
            transform: translateY(-2px);
        }
        .metric-value {
            font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
            font-size: 1.5rem;
            font-weight: 700;
            color: #1F2021;
        }
        .metric-label {
            font-size: 0.8rem;
            text-transform: uppercase;
            letter-spacing: 1px;
            color: #607D8B;
            margin-bottom: 5px;
        }

        /* Status Badges */
        .badge {
            display: inline-flex;
            align-items: center;
            padding: 4px 12px;
            border-radius: 99px;
            font-size: 0.75rem;
            font-weight: 700;
            text-transform: uppercase;
        }
        .badge-teal { background: #E0F2F1; color: #00796B; }
        .badge-orange { background: #FFF3E0; color: #E65100; }
        .badge-blue { background: #E3F2FD; color: #0277BD; }

        /* Button Polishing */
        .stButton>button {
            border-radius: 10px;
            font-weight: 600;
            padding: 0.6rem 2rem;
            transition: all 0.2s ease;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }
        .stButton>button:hover {
            box-shadow: 0 4px 15px rgba(0, 150, 136, 0.2);
        }
        
        /* Form Styling */
        [data-testid="stForm"] {
            border: none !important;
            padding: 0 !important;
        }
        </style>
        """,
        unsafe_allow_html=True
    )

def render_metric_dashboard(summary: dict):
    """Render a 4-column metric dashboard with batch statistics.

    Args:
        summary: Batch summary dict with duration_ms, savings_pct, success_count, failure_count.
    """
    cols = st.columns(4)

    metrics = [
        ("Duration", f"{summary.get('duration_ms', 0)/1000:.2f}s", "blue"),
        ("Savings", f"{summary.get('savings_pct', 0):.1f}%", "teal"),
        ("Success", str(summary.get('success_count', 0)), "teal"),
        ("Failures", str(summary.get('failure_count', 0)), "orange")
    ]

    for i, (label, value, color) in enumerate(metrics):
        with cols[i]:
            st.markdown(
                f"""
                <div class="metric-tile">
                    <div class="metric-label">{label}</div>
                    <div class="metric-value" style="color: {'#009688' if color == 'teal' else '#E65100' if color == 'orange' else '#0288D1'};">
                        {value}
                    </div>
                </div>
                """,
                unsafe_allow_html=True
            )

def render_status_header(status: str, run_id: int):
    """Render a status badge and run ID header.

    Args:
        status: Batch status (completed, running, failed, queued).
        run_id: Batch run identifier.
    """
    color_map = {
        "completed": ("#E0F2F1", "#00796B", "DONE", "check"),
        "running": ("#E3F2FD", "#0277BD", "PROCESSING", "activity"),
        "failed": ("#FFF3E0", "#E65100", "ERROR", "alert"),
        "queued": ("#F5F5F5", "#616161", "QUEUED", "bolt")
    }
    bg, fg, label, icon = color_map.get(status, ("#F5F5F5", "#616161", status.upper(), "bolt"))
    
    st.markdown(
        f"""
        <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 20px;">
            <h3 style="margin:0;">RUN #{run_id}</h3>
            <span class="badge" style="background: {bg}; color: {fg}; font-size: 0.9rem;">
                {get_icon(icon)} {label}
            </span>
        </div>
        """,
        unsafe_allow_html=True
    )
