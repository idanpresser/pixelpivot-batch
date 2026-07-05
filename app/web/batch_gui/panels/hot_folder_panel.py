"""Hot folder panel — manages automatic directory monitoring and batch triggers."""
import streamlit as st
from app.web.batch_gui.api_client import APIClient

def render_hot_folder_panel(client: APIClient):
    """Render the hot folder management panel with registration form and active watchers.

    Args:
        client: APIClient instance for hot folder operations.
    """
    st.header("Hot Folder Management")
    st.write("Register directories to be automatically processed when new images arrive.")
    
    # 1. Registration Form
    with st.expander("Register New Hot Folder", expanded=True):
        with st.form("hot_folder_reg_form"):
            source_dir = st.text_input("Source Directory (Watch)", placeholder="/path/to/watch")
            target_dir = st.text_input("Target Directory (Output)", placeholder="/path/to/output")
            
            col1, col2 = st.columns(2)
            with col1:
                target_format = st.selectbox("Target Format", ["webp", "avif", "jxl"])
            with col2:
                tool = st.selectbox("Tool", ["magick", "ffmpeg", "vips", "sharp"])
                
            category = st.selectbox("Category", ["general", "highRes", "web", "uiSharp", "lowContrst", "edgeCase"])
            
            submitted = st.form_submit_button("Activate Watcher")
            
            if submitted:
                if not source_dir or not target_dir:
                    st.error("Please provide both source and target directories.")
                else:
                    try:
                        result = client.register_hot_folder(
                            source_dir, target_dir, target_format, tool, category
                        )
                        st.success(f"Hot folder activated! Watcher ID: {result['watcher_id']}")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Failed to register hot folder: {e}")

    # 2. List Active Hot Folders
    st.divider()
    st.subheader("Active Watchers")
    
    try:
        hot_folders = client.list_hot_folders()
        if not hot_folders:
            st.info("No active hot folder watchers.")
        else:
            for hf in hot_folders:
                watcher_id = hf['watcher_id']
                with st.container(border=True):
                    col1, col2 = st.columns([3, 1])
                    with col1:
                        st.write(f"**Watcher ID:** `{watcher_id}`")
                        st.write(f"**Source:** `{hf['source_dir']}`")
                        st.write(f"**Target:** `{hf['target_dir']}`")
                        st.write(f"**Format:** `{hf['target_format']}` | **Tool:** `{hf['tool']}` | **Category:** `{hf['category']}`")
                    with col2:
                        if st.button("Stop Watcher", key=f"stop_{watcher_id}"):
                            try:
                                client.unregister_hot_folder(watcher_id)
                                st.success("Stopped.")
                                st.rerun()
                            except Exception as e:
                                st.error(f"Failed to stop: {e}")
                        st.status("Active", state="running")
    except Exception as e:
        st.error(f"Error fetching hot folders: {e}")
