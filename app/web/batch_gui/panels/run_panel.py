"""Run panel — batch job submission and real-time status monitoring."""
import streamlit as st
import time
import json
import os
from pathlib import Path
from app.core.api_client import APIClient
from app.web.batch_gui.style_utils import render_metric_dashboard, render_status_header

def load_defaults():
    """Load default batch parameters from gui_defaults.json.

    Returns:
        Dict with source_dir, target_dir, target_format, tool, category keys.
    """
    defaults_path = Path(__file__).resolve().parent.parent / "gui_defaults.json"
    if defaults_path.exists():
        try:
            with open(defaults_path, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "source_dir": "",
        "target_dir": "",
        "target_format": ["webp", "avif", "jxl"],
        "tool": ["magick", "ffmpeg", "vips", "sharp", "cavif"],
        "category": ["general", "highRes", "web", "uiSharp", "lowContrst", "edgeCase"],
        "sample": None,
        "input_files": None
    }

def render_run_panel(client: APIClient):
    """Render the batch execution panel with form and status monitoring.

    Displays input form for batch parameters, launches jobs in background,
    and polls for completion with real-time status updates.

    Args:
        client: APIClient instance for batch operations.
    """
    st.markdown('<h2 class="gradient-header">⚡ BATCH EXECUTION</h2>', unsafe_allow_html=True)
    
    defaults = load_defaults()
    
    with st.container():
        st.markdown('<div class="glass-card">', unsafe_allow_html=True)
        with st.form("batch_run_form"):
            col_a, col_b = st.columns(2)
            with col_a:
                source_dir = st.text_input("📁 SOURCE DIRECTORY", value=defaults.get("source_dir", ""), placeholder="/path/to/images")
            with col_b:
                target_dir = st.text_input("🎯 TARGET DIRECTORY", value=defaults.get("target_dir", ""), placeholder="/path/to/output")
            
            st.divider()
            
            c1, c2, c3 = st.columns(3)
            with c1:
                target_formats = st.multiselect("FORMAT", ["webp", "avif", "jxl"], default=defaults.get("target_format", ["webp", "avif", "jxl"]))
            with c2:
                tools = st.multiselect("ENGINE", ["magick", "ffmpeg", "vips", "sharp", "cavif"], default=defaults.get("tool", ["magick", "ffmpeg", "vips", "sharp", "cavif"]))
            with c3:
                categories = st.multiselect("CATEGORY", ["general", "highRes", "web", "uiSharp", "lowContrst", "edgeCase"], default=defaults.get("category", ["general", "highRes", "web", "uiSharp", "lowContrst", "edgeCase"]))
            
            st.markdown('<div style="margin-top: 15px;"></div>', unsafe_allow_html=True)
            st.markdown("##### 🛠️ ADVANCED OPTIONS")
            c_opt1, c_opt2 = st.columns(2)
            with c_opt1:
                use_sample = st.checkbox("LIMIT SAMPLE SIZE", value=defaults.get("sample") is not None)
                sample_val = defaults.get("sample", 30)
                if sample_val is None or not isinstance(sample_val, int) or sample_val < 2:
                    sample_val = 30
                sample_size = st.number_input("MAX IMAGES", min_value=2, value=sample_val, step=1)
            with c_opt2:
                use_filter = st.checkbox("SPECIFIC FILES FILTER", value=defaults.get("input_files") is not None)
                filter_val = ",".join(defaults.get("input_files", [])) if defaults.get("input_files") else ""
                input_files_str = st.text_input("COMMA-SEPARATED FILENAMES", value=filter_val, placeholder="image1.jpg, image2.png")

            st.markdown('<div style="margin-top: 20px;"></div>', unsafe_allow_html=True)
            submitted = st.form_submit_button("LAUNCH BATCH PROCESSOR", use_container_width=True)
            
            if submitted:
                if not source_dir or not target_dir:
                    st.error("CONFIGURATION ERROR: Source and target directories are required.")
                elif not target_formats or not tools or not categories:
                    st.error("CONFIGURATION ERROR: At least one format, engine, and category must be selected.")
                else:
                    try:
                        sample_arg = int(sample_size) if use_sample else None
                        input_files_arg = [f.strip() for f in input_files_str.split(",") if f.strip()] if use_filter else None
                        result = client.start_batch(
                            source_dir, target_dir, target_formats, tools, categories,
                            sample=sample_arg, input_files=input_files_arg
                        )
                        st.toast(f"Batch {result['run_id']} launched successfully!", icon="🚀")
                        st.session_state.active_run_id = result["run_id"]
                    except Exception as e:
                        st.error(f"SYSTEM ERROR: Failed to start batch: {e}")
        st.markdown('</div>', unsafe_allow_html=True)

    if "active_run_id" in st.session_state:
        st.divider()
        
        try:
            status = client.get_status(st.session_state.active_run_id)
            render_status_header(status["status"], st.session_state.active_run_id)
            
            if status.get("summary"):
                render_metric_dashboard(status["summary"])
            else:
                # Placeholder for active run
                cols = st.columns(3)
                cols[0].metric("TOTAL IMAGES", status['total_images'])
                cols[1].metric("STATE", status['status'].upper())
                cols[2].metric("ELAPSED", "TRANSMITTING...")

            if status["status"] == "completed":
                st.markdown('<div style="margin-top: 20px;"></div>', unsafe_allow_html=True)
                summary = status.get("summary") or {}
                if summary.get("failure_count", 0) > 0:
                    st.warning(f"BATCH FINISHED WITH ERRORS")
                    try:
                        errors = client.get_errors(st.session_state.active_run_id)
                        if errors:
                            import pandas as pd
                            err_df = pd.DataFrame(errors)
                            st.dataframe(
                                err_df,
                                column_config={
                                    "input_path": "File Path",
                                    "error": "Error Message",
                                    "created_at": "Time"
                                },
                                hide_index=True,
                                use_container_width=True
                            )
                    except Exception as e:
                        st.error(f"Failed to load logs: {e}")
                else:
                    st.success("BATCH SUCCESS: All images processed without errors.")
                    
                if st.button("RESET TERMINAL", use_container_width=True):
                    del st.session_state.active_run_id
                    st.rerun()
            elif status["status"] in ("failed", "interrupted", "cancelled"):
                st.error(f"RUN TERMINATED: Status is '{status['status']}'.")
                if st.button("CLEAR TERMINAL RUN", use_container_width=True):
                    del st.session_state.active_run_id
                    st.rerun()
            elif status["status"] not in ("queued", "processing", "running"):
                st.info(f"Run ended with status: '{status['status']}'.")
                if st.button("CLEAR RUN", use_container_width=True):
                    del st.session_state.active_run_id
                    st.rerun()
            else:
                time.sleep(2)
                st.rerun()

        except Exception as e:
            st.error(f"COMMUNICATION LOSS: {e}")
            if st.button("RETRY CONNECTION"):
                st.rerun()
