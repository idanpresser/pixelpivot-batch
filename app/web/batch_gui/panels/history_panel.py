"""History panel — displays past batch runs and aggregated metrics."""
import streamlit as st
import pandas as pd
import altair as alt
from app.core.api_client import APIClient

def render_history_panel(client: APIClient):
    """Render the history panel with batch runs table and failure details.

    Args:
        client: APIClient instance for querying batch history.
    """
    st.header("Batch History & Yield")
    
    if st.button("Refresh History"):
        st.rerun()

    try:
        history = client.get_history()
        if not history:
            st.info("No batch history found.")
            return

        df = pd.DataFrame(history)
        
        # Display summary metrics
        total_runs = len(df)
        total_images = df["total_images"].sum()
        avg_duration = df["duration_ms"].mean() / 1000 # in seconds
        
        col1, col2, col3 = st.columns(3)
        col1.metric("Total Runs", total_runs)
        col2.metric("Images Processed", total_images)
        col3.metric("Avg Duration (s)", f"{avg_duration:.2f}")

        # Yield Chart (Yield MB/s)
        # Note: In our current implementation yield_mb_sec is 0.0 because it's a TODO.
        # But we can plot success vs failure count
        st.subheader("Processing Performance")

        chart_data = df[["created_at", "success_count", "failure_count"]].copy()
        chart_data["created_at"] = pd.to_datetime(chart_data["created_at"])
        chart_long = chart_data.melt(
            id_vars="created_at",
            value_vars=["success_count", "failure_count"],
            var_name="metric",
            value_name="count",
        )
        color_scale = alt.Scale(
            domain=["success_count", "failure_count"],
            range=["#009688", "#E65100"],
        )
        chart = (
            alt.Chart(chart_long)
            .mark_bar()
            .encode(
                x=alt.X("created_at:T", title="Run Time"),
                y=alt.Y("count:Q", title="Images"),
                color=alt.Color("metric:N", scale=color_scale,
                                legend=alt.Legend(title=None,
                                                  labelExpr="datum.value === 'success_count' ? 'Success' : 'Failure'")),
                xOffset="metric:N",
                tooltip=[
                    alt.Tooltip("created_at:T", title="Time"),
                    alt.Tooltip("metric:N", title="Metric"),
                    alt.Tooltip("count:Q", title="Count"),
                ],
            )
            .properties(height=220)
        )
        st.altair_chart(chart, use_container_width=True)

        # Detailed Table
        st.subheader("Recent Runs")
        # Format the dataframe for display
        display_df = df[[
            "run_id", "status", "target_format", "tool", 
            "total_images", "success_count", "failure_count", 
            "duration_ms", "created_at"
        ]].copy()
        
        display_df["duration_s"] = display_df["duration_ms"] / 1000
        display_df = display_df.drop(columns=["duration_ms"])
        
        st.dataframe(
            display_df,
            column_config={
                "run_id": "ID",
                "status": st.column_config.TextColumn("Status"),
                "success_count": "Success",
                "failure_count": "Failure",
                "duration_s": "Duration (s)",
                "created_at": "Started At"
            },
            hide_index=True,
            use_container_width=True
        )

        # Failures Expanders (Lazy loaded)
        failures_df = df[df["failure_count"] > 0]
        if not failures_df.empty:
            st.subheader("Batch Failure Details")
            for _, row in failures_df.iterrows():
                run_id = row["run_id"]
                with st.expander(f"Run {run_id} — {row['failure_count']} failures ({row['tool']} to {row['target_format']})"):
                    try:
                        errors = client.get_errors(run_id)
                        if errors:
                            # Show errors in a nice table
                            err_df = pd.DataFrame(errors)
                            st.dataframe(err_df, use_container_width=True, hide_index=True)
                        else:
                            st.info("No detailed error logs found in database.")
                    except Exception as e:
                        st.error(f"Failed to load errors: {e}")

    except Exception as e:
        st.error(f"Failed to fetch history: {e}")
